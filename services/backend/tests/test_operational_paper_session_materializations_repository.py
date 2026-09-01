"""Gate 2B-B2B paper-session materialization repository tests."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import replace
from datetime import timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import psycopg
import pytest

from app.database import Database
from app.operational_paper_capital_authorizations import (
    OperationalPaperCapitalAuthorizationCreateIntent,
    OperationalPaperCapitalAuthorizationProfileBinding,
    build_operational_paper_capital_authorization_specification,
)
from app.operational_paper_session_materializations import (
    InvalidOperationalPaperSessionMaterializationSpecificationError,
    OperationalPaperSessionMaterialization,
    OperationalPaperSessionMaterializationChecksumMismatchError,
    OperationalPaperSessionMaterializationPlan,
    OperationalPaperSessionMaterializationState,
    build_operational_paper_session_materialization_plan,
    operational_paper_session_materialization_specification_checksum,
    prepare_operational_paper_session_materialization,
)
from app.repositories.operational_paper_capital_authorizations import (
    PostgresOperationalPaperCapitalAuthorizationRepository,
)
from app.repositories.operational_paper_session_materializations import (
    PostgresOperationalPaperSessionMaterializationRepository,
    _replay_materialization,
)
from app.repositories.operational_paper_session_profiles import (
    PostgresOperationalPaperSessionProfileRepository,
)
from tests.test_operational_paper_capital_authorizations_migration import (
    _seed_simulation,
)
from tests.test_operational_paper_session_materializations_domain import (
    _plan as _domain_plan,
)
from tests.test_operational_paper_session_profiles_repository import (
    BASE_TIME,
    _create,
    _resolver,
    _sources,
)

PREPARED_AT = BASE_TIME + timedelta(seconds=5)


async def _plan_context(
    database_url: str,
    database: Database,
    actor_id: UUID,
) -> OperationalPaperSessionMaterializationPlan:
    profile_repository = PostgresOperationalPaperSessionProfileRepository(database)
    profile_intent, _ = await _sources(database, actor_id)
    profile, profile_revision = await _create(
        profile_repository,
        profile_intent,
        actor_id,
        key=f"materialization-profile:{uuid4().hex}",
    )
    await profile_repository.approve(
        profile.profile_id,
        expected_revision=profile.current_revision,
        expected_checksum=profile_revision.specification_checksum,
        expected_record_version=profile.record_version,
        actor_id=actor_id,
        now=BASE_TIME + timedelta(seconds=3),
        strategy_resolver=_resolver,
    )

    with psycopg.connect(database_url, autocommit=True) as connection:
        simulation_id = _seed_simulation(
            connection,
            actor_id,
            initial_capital=Decimal("100"),
        )

    authorization_intent = OperationalPaperCapitalAuthorizationCreateIntent(
        profile_binding=OperationalPaperCapitalAuthorizationProfileBinding(
            profile_id=profile.profile_id,
            approved_revision=profile_revision.revision,
            specification_checksum=profile_revision.specification_checksum,
        ),
        simulation_id=simulation_id,
        quote_asset=profile_revision.specification.selected_instrument.pair.quote,
        authorized_capital=Decimal("40"),
    )
    authorization_specification = build_operational_paper_capital_authorization_specification(
        authorization_intent
    )
    authorization = await PostgresOperationalPaperCapitalAuthorizationRepository(database).create(
        authorization_intent,
        actor_id=actor_id,
        idempotency_key=f"materialization-authorization:{uuid4().hex}",
        now=BASE_TIME + timedelta(seconds=4),
    )
    return build_operational_paper_session_materialization_plan(
        authorization_id=authorization.authorization_id,
        authorization_specification=authorization_specification,
        authorization_checksum=authorization.authorization_checksum,
        profile_revision=profile_revision,
    )


def _insert_materialization(
    database_url: str,
    materialization: OperationalPaperSessionMaterialization,
) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            insert into public.operational_paper_session_materializations (
                materialization_id,
                schema_version,
                materialization_contract_version,
                state,
                record_version,
                authorization_id,
                authorization_checksum,
                profile_id,
                profile_approved_revision,
                profile_specification_checksum,
                mandate_id,
                mandate_approved_revision,
                mandate_specification_checksum,
                simulation_id,
                config_checksum,
                session_id,
                materialization_checksum,
                prepared_by,
                prepared_at,
                materialized_by,
                materialized_at
            )
            values (
                %s, %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s
            )
            """,
            (
                materialization.materialization_id,
                materialization.schema_version,
                materialization.materialization_contract_version,
                materialization.state.value,
                materialization.record_version,
                materialization.authorization_binding.authorization_id,
                materialization.authorization_binding.authorization_checksum,
                materialization.profile_binding.profile_id,
                materialization.profile_binding.approved_revision,
                materialization.profile_binding.specification_checksum,
                materialization.mandate_binding.mandate_id,
                materialization.mandate_binding.approved_revision,
                materialization.mandate_binding.specification_checksum,
                materialization.simulation_id,
                materialization.config_checksum,
                materialization.session_id,
                materialization.materialization_checksum,
                materialization.prepared_by,
                materialization.prepared_at,
                materialization.materialized_by,
                materialization.materialized_at,
            ),
        )


def _materialization_row(
    materialization: OperationalPaperSessionMaterialization,
) -> dict[str, object]:
    return {
        "materialization_id": materialization.materialization_id,
        "schema_version": materialization.schema_version,
        "materialization_contract_version": (materialization.materialization_contract_version),
        "state": materialization.state.value,
        "record_version": materialization.record_version,
        "authorization_id": materialization.authorization_binding.authorization_id,
        "authorization_checksum": (materialization.authorization_binding.authorization_checksum),
        "profile_id": materialization.profile_binding.profile_id,
        "profile_approved_revision": materialization.profile_binding.approved_revision,
        "profile_specification_checksum": (materialization.profile_binding.specification_checksum),
        "mandate_id": materialization.mandate_binding.mandate_id,
        "mandate_approved_revision": materialization.mandate_binding.approved_revision,
        "mandate_specification_checksum": (materialization.mandate_binding.specification_checksum),
        "simulation_id": materialization.simulation_id,
        "config_checksum": materialization.config_checksum,
        "session_id": materialization.session_id,
        "materialization_checksum": materialization.materialization_checksum,
        "prepared_by": materialization.prepared_by,
        "prepared_at": materialization.prepared_at,
        "materialized_by": materialization.materialized_by,
        "materialized_at": materialization.materialized_at,
    }


def _materialization_count(database_url: str, authorization_id: UUID) -> int:
    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """
            select count(*)
            from public.operational_paper_session_materializations
            where authorization_id = %s
            """,
            (authorization_id,),
        ).fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.asyncio
async def test_materialization_repository_get_round_trips_strict_prepared_row(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    expected = prepare_operational_paper_session_materialization(
        materialization_id=uuid4(),
        plan=plan,
        prepared_by=auth_user_id,
        prepared_at=PREPARED_AT,
    )
    _insert_materialization(database_url, expected)

    actual = await PostgresOperationalPaperSessionMaterializationRepository(database).get(
        expected.materialization_id
    )

    assert actual == expected


@pytest.mark.asyncio
async def test_materialization_repository_prepare_persists_exact_prepared_provenance(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    prepared = await PostgresOperationalPaperSessionMaterializationRepository(database).prepare(
        plan,
        actor_id=auth_user_id,
        now=PREPARED_AT,
    )
    specification = plan.specification

    assert prepared.state is OperationalPaperSessionMaterializationState.PREPARED
    assert prepared.record_version == 1
    assert prepared.materialized_by is None
    assert prepared.materialized_at is None
    assert prepared.authorization_binding == specification.authorization_binding
    assert prepared.profile_binding == specification.profile_binding
    assert prepared.mandate_binding == specification.mandate_binding
    assert prepared.simulation_id == specification.simulation_id
    assert prepared.config_checksum == specification.config_checksum
    assert prepared.session_id == specification.session_id
    assert prepared.materialization_checksum == (
        operational_paper_session_materialization_specification_checksum(specification)
    )
    assert prepared.prepared_by == auth_user_id
    assert prepared.prepared_at == PREPARED_AT
    assert str(prepared.materialization_id) != prepared.session_id
    assert (
        _materialization_count(
            database_url,
            specification.authorization_binding.authorization_id,
        )
        == 1
    )


@pytest.mark.asyncio
async def test_materialization_repository_prepare_has_no_filesystem_or_runtime_side_effect(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)

    def fail_path_write(*args: object, **kwargs: object) -> None:
        raise AssertionError("prepare attempted a filesystem write")

    monkeypatch.setattr(Path, "write_text", fail_path_write)
    monkeypatch.setattr(Path, "write_bytes", fail_path_write)
    monkeypatch.setattr(Path, "mkdir", fail_path_write)

    prepare_source = inspect.getsource(
        PostgresOperationalPaperSessionMaterializationRepository.prepare
    )
    for forbidden in (
        "PaperSessionRepository",
        "PaperRunner",
        "state.json",
        "Binance",
        "httpx",
        "requests",
    ):
        assert forbidden not in prepare_source

    prepared = await PostgresOperationalPaperSessionMaterializationRepository(database).prepare(
        plan,
        actor_id=auth_user_id,
        now=PREPARED_AT,
    )
    assert prepared.state is OperationalPaperSessionMaterializationState.PREPARED


@pytest.mark.asyncio
async def test_materialization_repository_exact_sequential_replay_preserves_original_metadata(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    repository = PostgresOperationalPaperSessionMaterializationRepository(database)
    created = await repository.prepare(
        plan,
        actor_id=auth_user_id,
        now=PREPARED_AT,
    )

    replay_actor = uuid4()
    replayed = await repository.prepare(
        plan,
        actor_id=replay_actor,
        now=PREPARED_AT + timedelta(hours=1),
    )

    assert replayed == created
    assert replayed.materialization_id == created.materialization_id
    assert replayed.prepared_by == auth_user_id
    assert replayed.prepared_at == PREPARED_AT
    assert (
        _materialization_count(
            database_url,
            plan.specification.authorization_binding.authorization_id,
        )
        == 1
    )


@pytest.mark.asyncio
async def test_materialization_repository_prepare_validates_all_input_before_database() -> None:
    plan = _domain_plan()
    actor_id = uuid4()

    class _DatabaseMustNotBeAccessed:
        def transaction(self) -> object:
            raise AssertionError("invalid prepare input reached PostgreSQL")

    repository = PostgresOperationalPaperSessionMaterializationRepository(
        cast(Database, _DatabaseMustNotBeAccessed())
    )
    forged_plan = object.__new__(OperationalPaperSessionMaterializationPlan)
    object.__setattr__(forged_plan, "specification", plan.specification)
    object.__setattr__(forged_plan, "config", object())

    invalid_calls = (
        (forged_plan, actor_id, PREPARED_AT),
        (plan, UUID(int=0), PREPARED_AT),
        (plan, actor_id, PREPARED_AT.replace(tzinfo=None)),
        (plan, actor_id, PREPARED_AT.astimezone(timezone(timedelta(hours=1)))),
    )
    for candidate_plan, actor_id, now in invalid_calls:
        with pytest.raises(InvalidOperationalPaperSessionMaterializationSpecificationError):
            await repository.prepare(candidate_plan, actor_id=actor_id, now=now)


class _RaceCursor:
    def __init__(
        self,
        cursor: object,
        observation: _RaceDatabase,
        generation: int,
    ) -> None:
        self._cursor = cursor
        self._observation = observation
        self._generation = generation

    async def fetchone(self) -> object:
        row = await self._cursor.fetchone()  # type: ignore[attr-defined]
        if self._generation in self._observation.initial_generations and row is None:
            self._observation.initial_misses += 1
            self._observation.select_results.append((self._generation, True, True))
            if self._observation.initial_misses == 2:
                self._observation.initial_reads_complete.set()
            await self._observation.initial_reads_complete.wait()
        else:
            initial = self._generation in self._observation.initial_generations
            self._observation.select_results.append((self._generation, initial, row is None))
            if row is not None:
                self._observation.events.append(("replay-select", self._generation))
        return row

    def __getattr__(self, name: str) -> object:
        return getattr(self._cursor, name)


class _RaceConnection:
    def __init__(
        self,
        connection: object,
        observation: _RaceDatabase,
        generation: int,
    ) -> None:
        self._connection = connection
        self._observation = observation
        self._generation = generation

    async def execute(
        self,
        query: object,
        params: object = None,
        **kwargs: object,
    ) -> object:
        cursor = await self._connection.execute(  # type: ignore[attr-defined]
            query,
            params,
            **kwargs,
        )
        normalized = " ".join(str(query).lower().split())
        authorization_select = (
            normalized.startswith("select")
            and "from public.operational_paper_session_materializations" in normalized
            and "where authorization_id = %s" in normalized
        )
        if not authorization_select:
            return cursor
        return _RaceCursor(cursor, self._observation, self._generation)


class _RaceDatabase:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._generation = 0
        self.initial_generations: list[int] = []
        self.initial_misses = 0
        self.initial_reads_complete = asyncio.Event()
        self.unique_violations: list[tuple[int, str]] = []
        self.select_results: list[tuple[int, bool, bool]] = []
        self.events: list[tuple[str, int]] = []

    def transaction(self) -> AbstractAsyncContextManager[_RaceConnection]:
        @asynccontextmanager
        async def scope() -> AsyncIterator[_RaceConnection]:
            self._generation += 1
            generation = self._generation
            if len(self.initial_generations) < 2:
                self.initial_generations.append(generation)
            self.events.append(("enter", generation))
            try:
                async with self._database.transaction() as connection:
                    yield _RaceConnection(connection, self, generation)
            except psycopg.errors.UniqueViolation as error:
                self.unique_violations.append((generation, error.diag.constraint_name or ""))
                self.events.append(("rollback", generation))
                raise
            finally:
                self.events.append(("exit", generation))

        return scope()


@pytest.mark.asyncio
async def test_materialization_prepare_race_replays_in_new_transaction(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    observation = _RaceDatabase(database)
    repository = PostgresOperationalPaperSessionMaterializationRepository(
        cast(Database, observation)
    )

    results = await asyncio.gather(
        repository.prepare(plan, actor_id=auth_user_id, now=PREPARED_AT),
        repository.prepare(plan, actor_id=auth_user_id, now=PREPARED_AT),
    )

    assert results[0] == results[1]
    assert len(observation.initial_generations) == 2
    assert len(set(observation.initial_generations)) == 2
    assert observation.initial_misses == 2
    assert len(observation.unique_violations) == 1
    losing_generation, constraint = observation.unique_violations[0]
    assert constraint == "op_ps_mat_authorization_key"
    assert ("rollback", losing_generation) in observation.events

    replay_generations = [
        generation
        for generation, initial, missed in observation.select_results
        if not initial and not missed
    ]
    assert len(replay_generations) == 1
    replay_generation = replay_generations[0]
    assert replay_generation != losing_generation
    assert observation.events.index(("exit", losing_generation)) < observation.events.index(
        ("replay-select", replay_generation)
    )
    assert (
        _materialization_count(
            database_url,
            plan.specification.authorization_binding.authorization_id,
        )
        == 1
    )


def _different_checksum(checksum: str) -> str:
    replacement = "0" if checksum[0] != "0" else "1"
    return replacement + checksum[1:]


def test_materialization_divergent_authorization_replay_fails_closed() -> None:
    plan = _domain_plan()
    actor_id = uuid4()
    divergent_specification = replace(
        plan.specification,
        config_checksum=_different_checksum(plan.specification.config_checksum),
    )
    divergent = OperationalPaperSessionMaterialization(
        materialization_id=uuid4(),
        schema_version=divergent_specification.schema_version,
        materialization_contract_version=(divergent_specification.materialization_contract_version),
        state=OperationalPaperSessionMaterializationState.PREPARED,
        record_version=1,
        authorization_binding=divergent_specification.authorization_binding,
        profile_binding=divergent_specification.profile_binding,
        mandate_binding=divergent_specification.mandate_binding,
        simulation_id=divergent_specification.simulation_id,
        config_checksum=divergent_specification.config_checksum,
        session_id=divergent_specification.session_id,
        materialization_checksum=(
            operational_paper_session_materialization_specification_checksum(
                divergent_specification
            )
        ),
        prepared_by=actor_id,
        prepared_at=PREPARED_AT,
        materialized_by=None,
        materialized_at=None,
    )
    with pytest.raises(OperationalPaperSessionMaterializationChecksumMismatchError):
        _replay_materialization(
            _materialization_row(divergent),
            plan.specification,
            operational_paper_session_materialization_specification_checksum(plan.specification),
        )


@pytest.mark.asyncio
async def test_materialization_existing_materialized_exact_row_is_valid_prepare_replay(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    plan = await _plan_context(database_url, database, auth_user_id)
    repository = PostgresOperationalPaperSessionMaterializationRepository(database)
    prepared = await repository.prepare(
        plan,
        actor_id=auth_user_id,
        now=PREPARED_AT,
    )
    materialized_at = PREPARED_AT + timedelta(minutes=1)
    with psycopg.connect(database_url, autocommit=True) as connection:
        row = connection.execute(
            """
            update public.operational_paper_session_materializations
            set state = 'MATERIALIZED',
                record_version = 2,
                materialized_by = %s,
                materialized_at = %s
            where materialization_id = %s
            returning materialization_id
            """,
            (auth_user_id, materialized_at, prepared.materialization_id),
        ).fetchone()
    assert row == (prepared.materialization_id,)

    replayed = await repository.prepare(
        plan,
        actor_id=uuid4(),
        now=PREPARED_AT + timedelta(hours=2),
    )

    assert replayed.state is OperationalPaperSessionMaterializationState.MATERIALIZED
    assert replayed.record_version == 2
    assert replayed.materialization_id == prepared.materialization_id
    assert replayed.prepared_by == prepared.prepared_by
    assert replayed.prepared_at == prepared.prepared_at
    assert replayed.materialized_by == auth_user_id
    assert replayed.materialized_at == materialized_at
    assert (
        _materialization_count(
            database_url,
            plan.specification.authorization_binding.authorization_id,
        )
        == 1
    )
