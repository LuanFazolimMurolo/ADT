"""Gate 2C-A1 paper-session materialization application-service tests."""

from __future__ import annotations

import asyncio
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
from app.operational_paper_capital_authorizations import (
    OperationalPaperCapitalAuthorizationCreateIntent,
    OperationalPaperCapitalAuthorizationProfileBinding,
)
from app.operational_paper_session_materializations import (
    OperationalPaperSessionMaterialization,
    OperationalPaperSessionMaterializationAuthorizationBinding,
    OperationalPaperSessionMaterializationConfigIdentityConflictError,
    OperationalPaperSessionMaterializationPlan,
    OperationalPaperSessionMaterializationState,
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


def _service(
    repository: object,
    paper_repository: object,
    clock: _SequenceClock,
) -> OperationalPaperSessionMaterializationService:
    return OperationalPaperSessionMaterializationService(
        repository=cast(PostgresOperationalPaperSessionMaterializationRepository, repository),
        paper_repository=cast(PaperTradingRepository, paper_repository),
        clock=clock,
    )


def _config_path(data_dir: Path, session_id: str) -> Path:
    return data_dir / "market" / "paper-trading" / session_id / "config.json"


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


def test_materialization_service_has_one_public_mutation_and_no_runtime_surface() -> None:
    source = Path(service_module.__file__).read_text()
    public_methods = {
        name
        for name, value in vars(OperationalPaperSessionMaterializationService).items()
        if callable(value) and not name.startswith("_")
    }
    assert public_methods == {"materialize"}
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
