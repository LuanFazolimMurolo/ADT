"""Gate 2C-A1 paper-session materialization application-service tests."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import psycopg
import pytest

import app.services.operational_paper_session_materializations as service_module
from app.database import Database
from app.domain.errors import PersistenceError, PersistenceUnavailableError
from app.operational_paper_capital_authorizations import (
    OperationalPaperCapitalAuthorizationCreateIntent,
    OperationalPaperCapitalAuthorizationNotFoundError,
    OperationalPaperCapitalAuthorizationProfileBinding,
    OperationalPaperCapitalAuthorizationSpecification,
    operational_paper_capital_authorization_specification_checksum,
)
from app.operational_paper_session_materializations import (
    OperationalPaperSessionMaterialization,
    OperationalPaperSessionMaterializationAuthorizationBinding,
    OperationalPaperSessionMaterializationConfigIdentityConflictError,
    OperationalPaperSessionMaterializationNotFoundError,
    OperationalPaperSessionMaterializationPlan,
    OperationalPaperSessionMaterializationProfileBindingConflictError,
    OperationalPaperSessionMaterializationState,
    build_operational_paper_session_materialization_plan,
    materialize_operational_paper_session_materialization,
    prepare_operational_paper_session_materialization,
)
from app.paper_trading.documents import encode_paper_config
from app.paper_trading.domain import PaperSessionConfig
from app.paper_trading.errors import PaperSessionCorruptError
from app.paper_trading.repository import PaperTradingRepository
from app.repositories.operational_paper_capital_authorizations import (
    PostgresOperationalPaperCapitalAuthorizationRepository,
)
from app.repositories.operational_paper_session_materializations import (
    PostgresOperationalPaperSessionMaterializationRepository,
)
from app.repositories.operational_paper_session_profiles import (
    PostgresOperationalPaperSessionProfileRepository,
)
from app.services.operational_paper_session_materializations import (
    OperationalPaperSessionMaterializationService,
)
from tests.test_operational_paper_session_materializations_domain import _plan as _domain_plan
from tests.test_operational_paper_session_materializations_repository import (
    PREPARED_AT,
    _materialization_count,
    _plan_context,
)


class _SequenceClock:
    def __init__(self, *values: datetime) -> None:
        self._values = iter(values)
        self.calls: list[datetime] = []

    def __call__(self) -> datetime:
        try:
            value = next(self._values)
        except StopIteration:
            raise AssertionError(
                "application service requested an unexpected clock value"
            ) from None
        self.calls.append(value)
        return value


class _RepositoryDouble:
    def __init__(
        self,
        prepared: OperationalPaperSessionMaterialization,
        events: list[str],
    ) -> None:
        self.prepared = prepared
        self.events = events
        self.prepare_calls = 0
        self.mark_calls = 0
        self.prepare_now: datetime | None = None
        self.mark_now: datetime | None = None

    async def prepare(
        self,
        plan: OperationalPaperSessionMaterializationPlan,
        *,
        actor_id: UUID,
        now: datetime,
    ) -> OperationalPaperSessionMaterialization:
        del plan, actor_id
        self.events.append("prepare")
        self.prepare_calls += 1
        self.prepare_now = now
        return self.prepared

    async def mark_materialized(
        self,
        materialization_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
    ) -> OperationalPaperSessionMaterialization:
        self.events.append("mark_materialized")
        self.mark_calls += 1
        self.mark_now = now
        assert materialization_id == self.prepared.materialization_id
        assert expected_record_version == self.prepared.record_version
        return materialize_operational_paper_session_materialization(
            self.prepared,
            materialized_by=actor_id,
            materialized_at=now,
        )


class _QueryRepositoryDouble:
    def __init__(
        self,
        *,
        get_result: OperationalPaperSessionMaterialization | None = None,
        list_result: tuple[list[OperationalPaperSessionMaterialization], int] | None = None,
    ) -> None:
        self.get_result = get_result
        self.list_result = list_result if list_result is not None else ([], 0)
        self.get_calls: list[UUID] = []
        self.list_calls: list[
            tuple[int, int, OperationalPaperSessionMaterializationState | None]
        ] = []

    async def get(
        self,
        materialization_id: UUID,
    ) -> OperationalPaperSessionMaterialization | None:
        self.get_calls.append(materialization_id)
        return self.get_result

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: OperationalPaperSessionMaterializationState | None = None,
    ) -> tuple[list[OperationalPaperSessionMaterialization], int]:
        self.list_calls.append((limit, offset, state))
        return self.list_result


class _PaperRepositoryDouble:
    def __init__(
        self,
        config: PaperSessionConfig,
        events: list[str],
    ) -> None:
        self.config = config
        self.events = events
        self.create_calls = 0
        self.load_calls = 0

    def create(self, config: PaperSessionConfig) -> PaperSessionConfig:
        del config
        self.events.append("filesystem_create")
        self.create_calls += 1
        return self.config

    def load_config(self, session_id: str) -> PaperSessionConfig:
        del session_id
        self.events.append("filesystem_load")
        self.load_calls += 1
        return self.config


class _NoMarkRepository:
    def __init__(
        self,
        repository: PostgresOperationalPaperSessionMaterializationRepository,
    ) -> None:
        self._repository = repository
        self.mark_calls = 0

    async def prepare(
        self,
        plan: OperationalPaperSessionMaterializationPlan,
        *,
        actor_id: UUID,
        now: datetime,
    ) -> OperationalPaperSessionMaterialization:
        return await self._repository.prepare(plan, actor_id=actor_id, now=now)

    async def mark_materialized(
        self,
        materialization_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
    ) -> OperationalPaperSessionMaterialization:
        del materialization_id, expected_record_version, actor_id, now
        self.mark_calls += 1
        raise AssertionError("MATERIALIZED replay attempted a second transition")


class _PrepareBarrierRepository:
    def __init__(
        self,
        repository: PostgresOperationalPaperSessionMaterializationRepository,
    ) -> None:
        self._repository = repository
        self._arrival_lock = asyncio.Lock()
        self._release = asyncio.Event()
        self.prepared_arrivals = 0
        self.mark_calls = 0

    async def prepare(
        self,
        plan: OperationalPaperSessionMaterializationPlan,
        *,
        actor_id: UUID,
        now: datetime,
    ) -> OperationalPaperSessionMaterialization:
        prepared = await self._repository.prepare(plan, actor_id=actor_id, now=now)
        assert prepared.state is OperationalPaperSessionMaterializationState.PREPARED
        async with self._arrival_lock:
            self.prepared_arrivals += 1
            if self.prepared_arrivals == 2:
                self._release.set()
        await self._release.wait()
        return prepared

    async def mark_materialized(
        self,
        materialization_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
    ) -> OperationalPaperSessionMaterialization:
        self.mark_calls += 1
        return await self._repository.mark_materialized(
            materialization_id,
            expected_record_version=expected_record_version,
            actor_id=actor_id,
            now=now,
        )


class _CountingPaperTradingRepository(PaperTradingRepository):
    def __init__(self, data_dir: Path) -> None:
        super().__init__(data_dir)
        self.create_calls = 0

    def create(self, config: PaperSessionConfig) -> PaperSessionConfig:
        self.create_calls += 1
        return super().create(config)


class _AuthorizationRepositoryDouble:
    def __init__(self, authorization: object | None) -> None:
        self.authorization = authorization
        self.calls: list[UUID] = []

    async def get(self, authorization_id: UUID) -> object | None:
        self.calls.append(authorization_id)
        return self.authorization


class _ProfileRevisionRepositoryDouble:
    def __init__(self, revision: object | None) -> None:
        self.revision = revision
        self.calls: list[tuple[UUID, int]] = []

    async def get_revision(self, profile_id: UUID, revision: int) -> object | None:
        self.calls.append((profile_id, revision))
        return self.revision


def _service(
    repository: object,
    paper_repository: object,
    clock: _SequenceClock,
    *,
    authorization_repository: object | None = None,
    profile_repository: object | None = None,
) -> OperationalPaperSessionMaterializationService:
    if authorization_repository is None:
        authorization_repository = object()
    if profile_repository is None:
        profile_repository = object()
    return OperationalPaperSessionMaterializationService(
        repository=cast(PostgresOperationalPaperSessionMaterializationRepository, repository),
        authorization_repository=cast(
            PostgresOperationalPaperCapitalAuthorizationRepository,
            authorization_repository,
        ),
        profile_repository=cast(
            PostgresOperationalPaperSessionProfileRepository,
            profile_repository,
        ),
        paper_repository=cast(PaperTradingRepository, paper_repository),
        clock=clock,
    )


def _config_path(data_dir: Path, session_id: str) -> Path:
    return data_dir / "market" / "paper-trading" / session_id / "config.json"


@pytest.mark.asyncio
async def test_materialization_service_get_returns_repository_instance() -> None:
    materialization_id = uuid4()
    materialization = prepare_operational_paper_session_materialization(
        materialization_id=materialization_id,
        plan=_domain_plan(),
        prepared_by=uuid4(),
        prepared_at=PREPARED_AT,
    )
    repository = _QueryRepositoryDouble(get_result=materialization)
    clock = _SequenceClock()

    result = await _service(repository, object(), clock).get(materialization_id)

    assert result is materialization
    assert repository.get_calls == [materialization_id]
    assert clock.calls == []


@pytest.mark.asyncio
async def test_materialization_service_get_missing_raises_stable_not_found() -> None:
    materialization_id = uuid4()
    repository = _QueryRepositoryDouble()
    clock = _SequenceClock()

    with pytest.raises(OperationalPaperSessionMaterializationNotFoundError) as exc_info:
        await _service(repository, object(), clock).get(materialization_id)

    assert type(exc_info.value) is OperationalPaperSessionMaterializationNotFoundError
    assert repository.get_calls == [materialization_id]
    assert clock.calls == []


@pytest.mark.asyncio
async def test_materialization_service_list_delegates_without_transformation() -> None:
    materialization = prepare_operational_paper_session_materialization(
        materialization_id=uuid4(),
        plan=_domain_plan(),
        prepared_by=uuid4(),
        prepared_at=PREPARED_AT,
    )
    expected = ([materialization], 1)
    repository = _QueryRepositoryDouble(list_result=expected)
    clock = _SequenceClock()

    result = await _service(repository, object(), clock).list(
        limit=7,
        offset=3,
        state=OperationalPaperSessionMaterializationState.PREPARED,
    )

    assert result is expected
    assert repository.list_calls == [(7, 3, OperationalPaperSessionMaterializationState.PREPARED)]
    assert clock.calls == []


@pytest.mark.asyncio
async def test_materialization_service_happy_path_publishes_then_materializes(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
    tmp_path: Path,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    repository = PostgresOperationalPaperSessionMaterializationRepository(database)
    paper_repository = PaperTradingRepository(tmp_path)
    materialized_at = PREPARED_AT + timedelta(minutes=1)
    clock = _SequenceClock(PREPARED_AT, materialized_at)

    materialized = await _service(repository, paper_repository, clock).materialize(
        plan,
        actor_id=auth_user_id,
    )

    assert clock.calls == [PREPARED_AT, materialized_at]
    assert materialized.state is OperationalPaperSessionMaterializationState.MATERIALIZED
    assert materialized.record_version == 2
    assert materialized.prepared_at == PREPARED_AT
    assert materialized.materialized_at == materialized_at
    assert paper_repository.load_config(materialized.session_id) == plan.config
    assert await repository.get(materialized.materialization_id) == materialized
    assert _config_path(tmp_path, materialized.session_id).is_file()
    assert not _config_path(tmp_path, materialized.session_id).with_name("state.json").exists()


@pytest.mark.asyncio
async def test_materialization_service_orders_prepare_filesystem_then_mark() -> None:
    plan = _domain_plan()
    actor_id = uuid4()
    prepared = prepare_operational_paper_session_materialization(
        materialization_id=uuid4(),
        plan=plan,
        prepared_by=actor_id,
        prepared_at=PREPARED_AT,
    )
    events: list[str] = []
    repository = _RepositoryDouble(prepared, events)
    paper_repository = _PaperRepositoryDouble(plan.config, events)
    materialized_at = PREPARED_AT + timedelta(minutes=1)
    clock = _SequenceClock(PREPARED_AT, materialized_at)

    result = await _service(repository, paper_repository, clock).materialize(
        plan,
        actor_id=actor_id,
    )

    assert events == ["prepare", "filesystem_create", "mark_materialized"]
    assert repository.prepare_now == PREPARED_AT
    assert repository.mark_now == materialized_at
    assert result.state is OperationalPaperSessionMaterializationState.MATERIALIZED


@pytest.mark.asyncio
async def test_materialization_service_recovers_prepared_before_filesystem(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
    tmp_path: Path,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    repository = PostgresOperationalPaperSessionMaterializationRepository(database)
    prepared = await repository.prepare(plan, actor_id=auth_user_id, now=PREPARED_AT)
    paper_repository = PaperTradingRepository(tmp_path)
    replay_request_at = PREPARED_AT + timedelta(minutes=1)
    materialized_at = PREPARED_AT + timedelta(minutes=2)

    materialized = await _service(
        repository,
        paper_repository,
        _SequenceClock(replay_request_at, materialized_at),
    ).materialize(plan, actor_id=auth_user_id)

    assert materialized.materialization_id == prepared.materialization_id
    assert materialized.prepared_at == PREPARED_AT
    assert materialized.materialized_at == materialized_at
    assert paper_repository.load_config(materialized.session_id) == plan.config
    assert (
        _materialization_count(
            database_url,
            plan.specification.authorization_binding.authorization_id,
        )
        == 1
    )


@pytest.mark.asyncio
async def test_materialization_service_recovers_prepared_after_filesystem(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
    tmp_path: Path,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    repository = PostgresOperationalPaperSessionMaterializationRepository(database)
    prepared = await repository.prepare(plan, actor_id=auth_user_id, now=PREPARED_AT)
    paper_repository = PaperTradingRepository(tmp_path)
    paper_repository.create(plan.config)
    config_path = _config_path(tmp_path, prepared.session_id)
    original = config_path.read_bytes()

    materialized = await _service(
        repository,
        paper_repository,
        _SequenceClock(
            PREPARED_AT + timedelta(minutes=1),
            PREPARED_AT + timedelta(minutes=2),
        ),
    ).materialize(plan, actor_id=auth_user_id)

    assert materialized.state is OperationalPaperSessionMaterializationState.MATERIALIZED
    assert config_path.read_bytes() == original
    assert (
        _materialization_count(
            database_url,
            plan.specification.authorization_binding.authorization_id,
        )
        == 1
    )


@pytest.mark.asyncio
async def test_materialization_service_materialized_replay_verifies_without_second_mark(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
    tmp_path: Path,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    repository = PostgresOperationalPaperSessionMaterializationRepository(database)
    prepared = await repository.prepare(plan, actor_id=auth_user_id, now=PREPARED_AT)
    paper_repository = PaperTradingRepository(tmp_path)
    paper_repository.create(plan.config)
    historical_materialized_at = PREPARED_AT + timedelta(minutes=1)
    existing = await repository.mark_materialized(
        prepared.materialization_id,
        expected_record_version=1,
        actor_id=auth_user_id,
        now=historical_materialized_at,
    )
    observed = _NoMarkRepository(repository)
    replay_clock = _SequenceClock(PREPARED_AT + timedelta(hours=1))

    replay = await _service(observed, paper_repository, replay_clock).materialize(
        plan,
        actor_id=auth_user_id,
    )

    assert replay == existing
    assert replay.prepared_at == PREPARED_AT
    assert replay.materialized_at == historical_materialized_at
    assert observed.mark_calls == 0
    assert len(replay_clock.calls) == 1


@pytest.mark.asyncio
async def test_materialization_service_materialized_missing_config_fails_closed(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
    tmp_path: Path,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    repository = PostgresOperationalPaperSessionMaterializationRepository(database)
    prepared = await repository.prepare(plan, actor_id=auth_user_id, now=PREPARED_AT)
    existing = await repository.mark_materialized(
        prepared.materialization_id,
        expected_record_version=1,
        actor_id=auth_user_id,
        now=PREPARED_AT + timedelta(minutes=1),
    )

    with pytest.raises(OperationalPaperSessionMaterializationConfigIdentityConflictError):
        await _service(
            repository,
            PaperTradingRepository(tmp_path),
            _SequenceClock(PREPARED_AT + timedelta(hours=1)),
        ).materialize(plan, actor_id=auth_user_id)

    assert await repository.get(prepared.materialization_id) == existing
    assert not _config_path(tmp_path, prepared.session_id).exists()


@pytest.mark.asyncio
async def test_materialization_service_divergent_filesystem_keeps_prepared(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
    tmp_path: Path,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    repository = PostgresOperationalPaperSessionMaterializationRepository(database)
    prepared = await repository.prepare(plan, actor_id=auth_user_id, now=PREPARED_AT)
    divergent = replace(
        plan.config,
        initial_capital=plan.config.initial_capital + Decimal("1"),
    )
    config_path = _config_path(tmp_path, prepared.session_id)
    config_path.parent.mkdir(parents=True)
    divergent_bytes = encode_paper_config(divergent)
    config_path.write_bytes(divergent_bytes)

    with pytest.raises(OperationalPaperSessionMaterializationConfigIdentityConflictError):
        await _service(
            repository,
            PaperTradingRepository(tmp_path),
            _SequenceClock(PREPARED_AT + timedelta(minutes=1)),
        ).materialize(plan, actor_id=auth_user_id)

    assert await repository.get(prepared.materialization_id) == prepared
    assert config_path.read_bytes() == divergent_bytes


@pytest.mark.asyncio
async def test_materialization_service_corrupt_filesystem_keeps_prepared(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
    tmp_path: Path,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    repository = PostgresOperationalPaperSessionMaterializationRepository(database)
    prepared = await repository.prepare(plan, actor_id=auth_user_id, now=PREPARED_AT)
    config_path = _config_path(tmp_path, prepared.session_id)
    config_path.parent.mkdir(parents=True)
    config_path.write_bytes(b"{}")

    with pytest.raises(PaperSessionCorruptError):
        await _service(
            repository,
            PaperTradingRepository(tmp_path),
            _SequenceClock(PREPARED_AT + timedelta(minutes=1)),
        ).materialize(plan, actor_id=auth_user_id)

    assert await repository.get(prepared.materialization_id) == prepared
    assert config_path.read_bytes() == b"{}"


@pytest.mark.asyncio
async def test_materialization_service_recovers_after_authorization_revocation(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
    tmp_path: Path,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    repository = PostgresOperationalPaperSessionMaterializationRepository(database)
    prepared = await repository.prepare(plan, actor_id=auth_user_id, now=PREPARED_AT)
    await PostgresOperationalPaperCapitalAuthorizationRepository(database).revoke(
        prepared.authorization_binding.authorization_id,
        expected_record_version=1,
        actor_id=auth_user_id,
        now=PREPARED_AT + timedelta(seconds=1),
    )

    materialized = await _service(
        repository,
        PaperTradingRepository(tmp_path),
        _SequenceClock(
            PREPARED_AT + timedelta(seconds=2),
            PREPARED_AT + timedelta(seconds=3),
        ),
    ).materialize(plan, actor_id=auth_user_id)

    assert materialized.materialization_id == prepared.materialization_id
    assert materialized.state is OperationalPaperSessionMaterializationState.MATERIALIZED
    assert materialized.record_version == 2


@pytest.mark.asyncio
async def test_materialization_service_concurrent_calls_converge(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
    tmp_path: Path,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    repository = PostgresOperationalPaperSessionMaterializationRepository(database)
    observed_repository = _PrepareBarrierRepository(repository)
    paper_repository = _CountingPaperTradingRepository(tmp_path)
    clock = _SequenceClock(
        PREPARED_AT,
        PREPARED_AT + timedelta(microseconds=1),
        PREPARED_AT + timedelta(minutes=1),
        PREPARED_AT + timedelta(minutes=1, microseconds=1),
    )
    service = _service(observed_repository, paper_repository, clock)

    results = await asyncio.gather(
        service.materialize(plan, actor_id=auth_user_id),
        service.materialize(plan, actor_id=auth_user_id),
    )

    assert observed_repository.prepared_arrivals == 2
    assert observed_repository.mark_calls == 2
    assert paper_repository.create_calls == 2
    assert results[0] == results[1]
    assert results[0].state is OperationalPaperSessionMaterializationState.MATERIALIZED
    assert results[0].record_version == 2
    assert (
        _materialization_count(
            database_url,
            plan.specification.authorization_binding.authorization_id,
        )
        == 1
    )
    assert paper_repository.list_session_ids() == (plan.specification.session_id,)


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ("object", "session_id", "config_checksum"))
async def test_materialization_service_rejects_unverified_executable_identity(
    mismatch: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _domain_plan()
    actor_id = uuid4()
    prepared = prepare_operational_paper_session_materialization(
        materialization_id=uuid4(),
        plan=plan,
        prepared_by=actor_id,
        prepared_at=PREPARED_AT,
    )
    events: list[str] = []
    repository = _RepositoryDouble(prepared, events)
    persisted_config = plan.config
    if mismatch == "object":
        persisted_config = replace(
            plan.config,
            initial_capital=plan.config.initial_capital + Decimal("1"),
        )
    elif mismatch == "session_id":
        monkeypatch.setattr(service_module, "paper_session_id", lambda config: "0" * 64)
    else:
        monkeypatch.setattr(service_module, "paper_config_checksum", lambda config: "0" * 64)
    paper_repository = _PaperRepositoryDouble(persisted_config, events)

    with pytest.raises(OperationalPaperSessionMaterializationConfigIdentityConflictError):
        await _service(
            repository,
            paper_repository,
            _SequenceClock(PREPARED_AT),
        ).materialize(plan, actor_id=actor_id)

    assert repository.mark_calls == 0
    assert events == ["prepare", "filesystem_create"]


@pytest.mark.asyncio
async def test_materialization_service_allows_two_provenances_for_one_executable_config(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
    tmp_path: Path,
) -> None:
    first_plan = await _plan_context(database_url, database, auth_user_id)
    repository = PostgresOperationalPaperSessionMaterializationRepository(database)
    paper_repository = PaperTradingRepository(tmp_path)
    first = await _service(
        repository,
        paper_repository,
        _SequenceClock(PREPARED_AT, PREPARED_AT + timedelta(minutes=1)),
    ).materialize(first_plan, actor_id=auth_user_id)
    authorization_repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)
    await authorization_repository.revoke(
        first.authorization_binding.authorization_id,
        expected_record_version=1,
        actor_id=auth_user_id,
        now=PREPARED_AT + timedelta(minutes=2),
    )
    profile = first_plan.specification.profile_binding
    second_authorization = await authorization_repository.create(
        OperationalPaperCapitalAuthorizationCreateIntent(
            profile_binding=OperationalPaperCapitalAuthorizationProfileBinding(
                profile_id=profile.profile_id,
                approved_revision=profile.approved_revision,
                specification_checksum=profile.specification_checksum,
            ),
            simulation_id=first_plan.specification.simulation_id,
            quote_asset=first_plan.config.pair.quote,
            authorized_capital=first_plan.config.initial_capital,
        ),
        actor_id=auth_user_id,
        idempotency_key=f"shared-executable-config:{uuid4().hex}",
        now=PREPARED_AT + timedelta(minutes=3),
    )
    second_plan = OperationalPaperSessionMaterializationPlan(
        specification=replace(
            first_plan.specification,
            authorization_binding=OperationalPaperSessionMaterializationAuthorizationBinding(
                authorization_id=second_authorization.authorization_id,
                authorization_checksum=second_authorization.authorization_checksum,
            ),
        ),
        config=first_plan.config,
    )

    second = await _service(
        repository,
        paper_repository,
        _SequenceClock(
            PREPARED_AT + timedelta(minutes=4),
            PREPARED_AT + timedelta(minutes=5),
        ),
    ).materialize(second_plan, actor_id=auth_user_id)

    assert first.materialization_id != second.materialization_id
    assert (
        first.authorization_binding.authorization_id
        != second.authorization_binding.authorization_id
    )
    assert first.session_id == second.session_id
    assert first.materialization_checksum != second.materialization_checksum
    assert first.state is second.state is OperationalPaperSessionMaterializationState.MATERIALIZED
    assert paper_repository.list_session_ids() == (first.session_id,)
    with psycopg.connect(database_url) as connection:
        rows = connection.execute(
            """
            select materialization_id, authorization_id, state, record_version
            from public.operational_paper_session_materializations
            where session_id = %s
            """,
            (first.session_id,),
        ).fetchall()
    assert len(rows) == 2
    assert {row[0] for row in rows} == {first.materialization_id, second.materialization_id}
    assert all(row[2:] == ("MATERIALIZED", 2) for row in rows)


def test_materialization_service_has_expected_public_surface_and_no_runtime_surface() -> None:
    source = Path(service_module.__file__).read_text()
    public_methods = {
        name
        for name, value in vars(OperationalPaperSessionMaterializationService).items()
        if callable(value) and not name.startswith("_")
    }
    assert public_methods == {
        "get",
        "list",
        "materialize",
        "materialize_authorization",
    }
    assert source.count("self._repository.prepare(") == 1
    assert source.count("self._repository.mark_materialized(") == 1
    for forbidden in (
        "._root",
        "._session_dir",
        "._atomic_write",
        "encode_paper_config",
        "state.json",
        "summary.json",
        "run_once",
        "Binance",
        "network",
        "orders",
        "fills",
        "PnL",
    ):
        assert forbidden not in source


@pytest.mark.asyncio
async def test_materialization_service_materializes_authoritative_authorization_id(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
    tmp_path: Path,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    authorization_id = plan.specification.authorization_binding.authorization_id
    repository = PostgresOperationalPaperSessionMaterializationRepository(database)
    authorization_repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)
    profile_repository = PostgresOperationalPaperSessionProfileRepository(database)
    paper_repository = PaperTradingRepository(tmp_path)
    service = _service(
        repository,
        paper_repository,
        _SequenceClock(PREPARED_AT, PREPARED_AT + timedelta(minutes=1)),
        authorization_repository=authorization_repository,
        profile_repository=profile_repository,
    )

    materialized = await service.materialize_authorization(
        authorization_id,
        actor_id=auth_user_id,
    )

    assert materialized.state is OperationalPaperSessionMaterializationState.MATERIALIZED
    assert materialized.authorization_binding == plan.specification.authorization_binding
    assert materialized.profile_binding == plan.specification.profile_binding
    assert materialized.session_id == plan.specification.session_id
    assert materialized.config_checksum == plan.specification.config_checksum
    assert paper_repository.load_config(materialized.session_id) == plan.config
    assert plan.config.initial_capital == Decimal("40")
    assert _materialization_count(database_url, authorization_id) == 1


@pytest.mark.asyncio
async def test_materialization_service_authorization_not_found_fails_before_orchestration() -> None:
    authorization_id = uuid4()
    authorization_repository = _AuthorizationRepositoryDouble(None)
    profile_repository = _ProfileRevisionRepositoryDouble(None)
    service = _service(
        object(),
        object(),
        _SequenceClock(),
        authorization_repository=authorization_repository,
        profile_repository=profile_repository,
    )

    with pytest.raises(OperationalPaperCapitalAuthorizationNotFoundError):
        await service.materialize_authorization(authorization_id, actor_id=uuid4())

    assert authorization_repository.calls == [authorization_id]
    assert profile_repository.calls == []


@pytest.mark.asyncio
async def test_materialization_service_missing_exact_profile_revision_fails_closed(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    authorization_id = plan.specification.authorization_binding.authorization_id
    authorization_repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)
    authorization = await authorization_repository.get(authorization_id)
    assert authorization is not None
    profile_repository = _ProfileRevisionRepositoryDouble(None)
    service = _service(
        object(),
        object(),
        _SequenceClock(),
        authorization_repository=authorization_repository,
        profile_repository=profile_repository,
    )

    with pytest.raises(PersistenceError):
        await service.materialize_authorization(
            authorization_id,
            actor_id=auth_user_id,
        )

    binding = authorization.profile_binding
    assert profile_repository.calls == [(binding.profile_id, binding.approved_revision)]


@pytest.mark.asyncio
async def test_materialization_service_revoked_before_prepared_is_denied(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
    tmp_path: Path,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    authorization_id = plan.specification.authorization_binding.authorization_id
    authorization_repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)
    authorization = await authorization_repository.get(authorization_id)
    assert authorization is not None

    revoked = await authorization_repository.revoke(
        authorization_id,
        expected_record_version=authorization.record_version,
        actor_id=auth_user_id,
        now=PREPARED_AT,
    )
    assert revoked.state.value == "REVOKED"

    repository = PostgresOperationalPaperSessionMaterializationRepository(database)
    paper_repository = PaperTradingRepository(tmp_path)
    service = _service(
        repository,
        paper_repository,
        _SequenceClock(PREPARED_AT + timedelta(minutes=1)),
        authorization_repository=authorization_repository,
        profile_repository=PostgresOperationalPaperSessionProfileRepository(database),
    )

    with pytest.raises(PersistenceUnavailableError):
        await service.materialize_authorization(
            authorization_id,
            actor_id=auth_user_id,
        )

    assert await repository.get_by_authorization(authorization_id) is None
    assert _materialization_count(database_url, authorization_id) == 0
    assert not _config_path(tmp_path, plan.specification.session_id).exists()


@pytest.mark.asyncio
async def test_materialization_service_recovers_prepared_after_authorization_revoke(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
    tmp_path: Path,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    authorization_id = plan.specification.authorization_binding.authorization_id

    repository = PostgresOperationalPaperSessionMaterializationRepository(database)
    prepared = await repository.prepare(
        plan,
        actor_id=auth_user_id,
        now=PREPARED_AT,
    )
    assert prepared.state is OperationalPaperSessionMaterializationState.PREPARED

    authorization_repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)
    authorization = await authorization_repository.get(authorization_id)
    assert authorization is not None

    revoked = await authorization_repository.revoke(
        authorization_id,
        expected_record_version=authorization.record_version,
        actor_id=auth_user_id,
        now=PREPARED_AT + timedelta(minutes=1),
    )
    assert revoked.state.value == "REVOKED"

    paper_repository = PaperTradingRepository(tmp_path)
    materialized = await _service(
        repository,
        paper_repository,
        _SequenceClock(
            PREPARED_AT + timedelta(minutes=2),
            PREPARED_AT + timedelta(minutes=3),
        ),
        authorization_repository=authorization_repository,
        profile_repository=PostgresOperationalPaperSessionProfileRepository(database),
    ).materialize_authorization(
        authorization_id,
        actor_id=auth_user_id,
    )

    assert materialized.materialization_id == prepared.materialization_id
    assert materialized.state is OperationalPaperSessionMaterializationState.MATERIALIZED
    assert materialized.record_version == 2
    assert materialized.authorization_binding.authorization_id == authorization_id
    assert paper_repository.load_config(materialized.session_id) == plan.config
    assert _materialization_count(database_url, authorization_id) == 1


@pytest.mark.asyncio
async def test_materialization_service_mismatched_profile_evidence_fails_closed(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    authorization_id = plan.specification.authorization_binding.authorization_id
    authorization_repository = PostgresOperationalPaperCapitalAuthorizationRepository(database)
    authorization = await authorization_repository.get(authorization_id)
    assert authorization is not None

    profile_repository = PostgresOperationalPaperSessionProfileRepository(database)
    binding = authorization.profile_binding
    profile_revision = await profile_repository.get_revision(
        binding.profile_id,
        binding.approved_revision,
    )
    assert profile_revision is not None

    mismatched_revision = replace(
        profile_revision,
        profile_id=uuid4(),
    )
    observed_profile_repository = _ProfileRevisionRepositoryDouble(mismatched_revision)

    service = _service(
        object(),
        object(),
        _SequenceClock(),
        authorization_repository=_AuthorizationRepositoryDouble(authorization),
        profile_repository=observed_profile_repository,
    )

    with pytest.raises(OperationalPaperSessionMaterializationProfileBindingConflictError):
        await service.materialize_authorization(
            authorization_id,
            actor_id=auth_user_id,
        )

    assert observed_profile_repository.calls == [(binding.profile_id, binding.approved_revision)]


@pytest.mark.asyncio
async def test_materialization_service_preserves_exact_authorized_decimal_capital(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    authorization_id = plan.specification.authorization_binding.authorization_id

    stored_authorization_repository = PostgresOperationalPaperCapitalAuthorizationRepository(
        database
    )
    authorization = await stored_authorization_repository.get(authorization_id)
    assert authorization is not None

    stored_profile_repository = PostgresOperationalPaperSessionProfileRepository(database)
    binding = authorization.profile_binding
    profile_revision = await stored_profile_repository.get_revision(
        binding.profile_id,
        binding.approved_revision,
    )
    assert profile_revision is not None

    capital = Decimal("1234.56789012")
    authorization_specification = OperationalPaperCapitalAuthorizationSpecification(
        schema_version=authorization.schema_version,
        profile_binding=authorization.profile_binding,
        simulation_id=authorization.simulation_id,
        quote_asset=authorization.quote_asset,
        authorized_capital=capital,
    )
    checksum = operational_paper_capital_authorization_specification_checksum(
        authorization_specification
    )
    exact_authorization = replace(
        authorization,
        authorized_capital=capital,
        authorization_checksum=checksum,
    )

    expected_plan = build_operational_paper_session_materialization_plan(
        authorization_id=authorization_id,
        authorization_specification=authorization_specification,
        authorization_checksum=checksum,
        profile_revision=profile_revision,
    )

    events: list[str] = []
    prepared = prepare_operational_paper_session_materialization(
        materialization_id=uuid4(),
        plan=expected_plan,
        prepared_by=auth_user_id,
        prepared_at=PREPARED_AT,
    )
    repository = _RepositoryDouble(prepared, events)
    paper_repository = _PaperRepositoryDouble(expected_plan.config, events)

    materialized = await _service(
        repository,
        paper_repository,
        _SequenceClock(
            PREPARED_AT,
            PREPARED_AT + timedelta(minutes=1),
        ),
        authorization_repository=_AuthorizationRepositoryDouble(exact_authorization),
        profile_repository=_ProfileRevisionRepositoryDouble(profile_revision),
    ).materialize_authorization(
        authorization_id,
        actor_id=auth_user_id,
    )

    assert expected_plan.config.initial_capital == capital
    assert type(expected_plan.config.initial_capital) is Decimal
    assert paper_repository.config.initial_capital == capital
    assert type(paper_repository.config.initial_capital) is Decimal
    assert materialized.config_checksum == expected_plan.specification.config_checksum
    assert events == ["prepare", "filesystem_create", "mark_materialized"]


def test_materialization_service_authoritative_resolution_order_and_boundary() -> None:
    source = inspect.getsource(
        OperationalPaperSessionMaterializationService.materialize_authorization
    )

    authorization_get = source.index("self._authorization_repository.get")
    profile_get = source.index("self._profile_repository.get_revision")
    plan_build = source.index("build_operational_paper_session_materialization_plan")
    materialize = source.index("self.materialize")

    assert authorization_get < profile_get < plan_build < materialize

    forbidden = (
        "mandate_repository",
        "simulation_repository",
        "strategy_definition_repository",
        "get_current",
        "FastAPI",
        "Depends",
        "Request",
        "runner",
    )
    assert all(token not in source for token in forbidden)
