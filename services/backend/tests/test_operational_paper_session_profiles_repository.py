"""Gate 2C repository tests against disposable local PostgreSQL."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from app.backtesting.domain import (
    ExecutionAssumptions,
    FeeModel,
    InstrumentConstraints,
    IntrabarPolicy,
    PositionSizedExecutionAssumptions,
    PositionSizingKind,
    PositionSizingPolicy,
    SlippageModel,
    StopLossKind,
    StopLossPolicy,
    StopLossRiskLimits,
    StrategyParameters,
)
from app.database import Database
from app.database.pool import DatabaseConnection
from app.domain.errors import PersistenceError
from app.indicators.regime import MarketRegimePolicy
from app.market_data.domain import Exchange, MarketType, TradingPair
from app.market_data.timeframes import TIMEFRAMES
from app.operational_mandates import (
    OperationalMandateInstrument,
    OperationalMandateSpecification,
)
from app.operational_mandates.errors import (
    OperationalMandateChecksumMismatchError,
    OperationalMandateNotFoundError,
    OperationalMandateRevisionConflictError,
    OperationalMandateStateTransitionConflictError,
)
from app.operational_paper_session_profiles import (
    InvalidOperationalPaperSessionProfileSpecificationError,
    OperationalPaperSessionProfile,
    OperationalPaperSessionProfileChecksumMismatchError,
    OperationalPaperSessionProfileCreateIntent,
    OperationalPaperSessionProfileIdempotencyConflictError,
    OperationalPaperSessionProfileMandateBinding,
    OperationalPaperSessionProfileRecordVersionConflictError,
    OperationalPaperSessionProfileRevision,
    OperationalPaperSessionProfileRevisionConflictError,
    OperationalPaperSessionProfileState,
    OperationalPaperSessionProfileStateTransitionConflictError,
)
from app.repositories.operational_mandates import PostgresOperationalMandateRepository
from app.repositories.operational_paper_session_profiles import (
    PostgresOperationalPaperSessionProfileRepository,
    operational_paper_session_profile_from_row,
)
from app.repositories.strategy_definitions import PostgresStrategyDefinitionRepository
from app.strategies.definitions import (
    StoredStrategyParameter,
    StrategyDefinition,
    StrategyDefinitionSpec,
    strategy_parameter_checksum,
)
from app.strategies.domain import StrategyParameterKind
from app.strategies.errors import (
    StrategyDefinitionArchivedError,
    StrategyDefinitionCompatibilityError,
    StrategyDefinitionNotFoundError,
    StrategyDefinitionRevisionConflictError,
)

BASE_TIME = datetime(2026, 8, 23, 12, tzinfo=UTC)


def _instrument(base: str = "BTC") -> OperationalMandateInstrument:
    return OperationalMandateInstrument(
        Exchange.BINANCE,
        MarketType.SPOT,
        TradingPair(base, "USDT"),
    )


def _parameters() -> tuple[StoredStrategyParameter, ...]:
    return (
        StoredStrategyParameter("enabled", StrategyParameterKind.BOOLEAN, True),
        StoredStrategyParameter("label", StrategyParameterKind.STRING, "paper"),
        StoredStrategyParameter("period", StrategyParameterKind.INTEGER, 12),
        StoredStrategyParameter("ratio", StrategyParameterKind.DECIMAL, "1.5"),
    )


def _definition_spec(
    *,
    display_name: str = "Profile source",
    parameters: tuple[StoredStrategyParameter, ...] | None = None,
) -> StrategyDefinitionSpec:
    document = _parameters() if parameters is None else parameters
    return StrategyDefinitionSpec(
        display_name=display_name,
        plugin_name="test-profile-plugin",
        plugin_version="2",
        plugin_schema_version=1,
        lifecycle_version=2,
        parameters=document,
        parameters_checksum=strategy_parameter_checksum(document),
    )


def _resolver(definition: StrategyDefinition) -> StrategyParameters:
    values: list[tuple[str, object]] = []
    for item in definition.spec.parameters:
        if item.kind is StrategyParameterKind.DECIMAL:
            assert isinstance(item.value, str)
            value: object = Decimal(item.value)
        else:
            value = item.value
        values.append((item.name, value))
    return cast(StrategyParameters, tuple(values))


class _CountingResolver:
    def __init__(self) -> None:
        self.calls: list[StrategyDefinition] = []

    def __call__(self, definition: StrategyDefinition) -> StrategyParameters:
        self.calls.append(definition)
        return _resolver(definition)


async def _approved_mandate(
    database: Database,
    actor_id: UUID,
) -> tuple[UUID, str]:
    repository = PostgresOperationalMandateRepository(database)
    aggregate, revision = await repository.create(
        OperationalMandateSpecification(
            schema_version=1,
            name="Profile authority",
            description="Exact approved Binance Spot authority",
            instruments=(_instrument(),),
        ),
        actor_id=actor_id,
        idempotency_key=f"mandate-{uuid4().hex}",
        now=BASE_TIME,
    )
    approved = await repository.approve(
        aggregate.mandate_id,
        expected_revision=1,
        expected_checksum=revision.specification_checksum,
        expected_record_version=1,
        actor_id=actor_id,
        now=BASE_TIME + timedelta(seconds=1),
    )
    assert approved.approved_checksum is not None
    return approved.mandate_id, approved.approved_checksum


async def _strategy(database: Database, actor_id: UUID) -> StrategyDefinition:
    return await PostgresStrategyDefinitionRepository(database).create(
        _definition_spec(),
        actor_id=actor_id,
    )


def _intent(
    mandate_id: UUID,
    mandate_checksum: str,
    definition: StrategyDefinition,
    **changes: object,
) -> OperationalPaperSessionProfileCreateIntent:
    values: dict[str, object] = {
        "name": "Primary paper profile",
        "description": "Frozen deterministic non-capital policy",
        "mandate_binding": OperationalPaperSessionProfileMandateBinding(
            mandate_id,
            1,
            mandate_checksum,
        ),
        "selected_instrument": _instrument(),
        "timeframe": TIMEFRAMES["1h"],
        "start_at": BASE_TIME,
        "warmup_candles": 20,
        "strategy_definition_id": definition.id,
        "expected_strategy_definition_revision": definition.revision,
        "expected_strategy_parameters_checksum": definition.spec.parameters_checksum,
        "execution": PositionSizedExecutionAssumptions(
            fees=FeeModel(Decimal("1"), Decimal("2")),
            slippage=SlippageModel(fixed_bps=Decimal("3")),
            intrabar_policy=IntrabarPolicy.CONSERVATIVE,
            force_close_at_end=False,
            position_sizing=PositionSizingPolicy(
                PositionSizingKind.EQUITY_PERCENT,
                Decimal("25"),
                Decimal("10"),
            ),
        ),
        "instrument_constraints": InstrumentConstraints(
            Decimal("0.001"),
            Decimal("0.001"),
            Decimal("0.01"),
            Decimal("10"),
            Decimal("10000"),
        ),
        "risk_limits": StopLossRiskLimits(
            max_order_notional=Decimal("500"),
            max_position_notional=Decimal("1000"),
            max_open_orders=4,
            max_total_orders=50,
            max_drawdown_pct=Decimal("20"),
            minimum_quote_reserve=Decimal("20"),
            stop_loss=StopLossPolicy(StopLossKind.FIXED_PERCENT, Decimal("5")),
        ),
        "history_window": 512,
        "max_candles": 10_000,
        "max_orders": 1_000,
        "max_events": 10_000,
        "engine_version": "paper-engine-v1",
        "market_regime_policy": MarketRegimePolicy(),
    }
    values.update(changes)
    return OperationalPaperSessionProfileCreateIntent(**values)  # type: ignore[arg-type]


async def _sources(
    database: Database,
    actor_id: UUID,
) -> tuple[OperationalPaperSessionProfileCreateIntent, StrategyDefinition]:
    mandate_id, mandate_checksum = await _approved_mandate(database, actor_id)
    definition = await _strategy(database, actor_id)
    return _intent(mandate_id, mandate_checksum, definition), definition


async def _create(
    repository: PostgresOperationalPaperSessionProfileRepository,
    intent: OperationalPaperSessionProfileCreateIntent,
    actor_id: UUID,
    *,
    key: str = "profile-create",
    resolver: _CountingResolver | None = None,
) -> tuple[OperationalPaperSessionProfile, OperationalPaperSessionProfileRevision]:
    return await repository.create(
        intent,
        actor_id=actor_id,
        idempotency_key=key,
        now=BASE_TIME + timedelta(seconds=2),
        strategy_resolver=resolver or _resolver,
    )


async def _counts(database: Database, profile_id: UUID) -> tuple[int, int]:
    async with database.transaction() as connection:
        aggregate_cursor = await connection.execute(
            "select count(*) as total from public.operational_paper_session_profiles"
        )
        aggregate_row = await aggregate_cursor.fetchone()
        revision_cursor = await connection.execute(
            """
            select count(*) as total
            from public.operational_paper_session_profile_revisions
            where profile_id = %s
            """,
            (profile_id,),
        )
        revision_row = await revision_cursor.fetchone()
    assert aggregate_row is not None and revision_row is not None
    return int(aggregate_row["total"]), int(revision_row["total"])


class _DatabaseMustNotBeAccessed:
    def transaction(self) -> object:
        raise AssertionError("invalid public input reached PostgreSQL")


class _ReplayCursor:
    def __init__(
        self,
        cursor: Any,
        observation: _CreateRaceDatabase,
        generation: int,
        *,
        initial: bool,
    ) -> None:
        self._cursor = cursor
        self._observation = observation
        self._generation = generation
        self._initial = initial

    async def fetchone(self) -> Any:
        row = await self._cursor.fetchone()
        self._observation.replay_results.append((self._generation, self._initial, row is None))
        return row


class _CreateRaceConnection:
    def __init__(
        self,
        connection: DatabaseConnection,
        observation: _CreateRaceDatabase,
        generation: int,
    ) -> None:
        self._connection = connection
        self._observation = observation
        self._generation = generation

    async def execute(self, query: Any, params: Any = None, **kwargs: Any) -> Any:
        text = str(query).lower()
        try:
            cursor = await self._connection.execute(query, params, **kwargs)
        except psycopg.errors.UniqueViolation as error:
            if (
                "insert into public.operational_paper_session_profiles" in text
                and "profile_revisions" not in text
            ):
                self._observation.unique_violations.append(
                    (self._generation, error.diag.constraint_name)
                )
            raise
        if (
            "from public.operational_paper_session_profiles" in text
            and "created_by = %s and create_idempotency_key = %s" in text
        ):
            initial = await self._observation.replay_select_executed(self._generation)
            return _ReplayCursor(
                cursor,
                self._observation,
                self._generation,
                initial=initial,
            )
        return cursor


class _CreateRaceDatabase:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._generation = 0
        self._barrier_lock = asyncio.Lock()
        self._barrier_release = asyncio.Event()
        self.events: list[tuple[str, int]] = []
        self.initial_generations: list[int] = []
        self.replay_results: list[tuple[int, bool, bool]] = []
        self.unique_violations: list[tuple[int, str | None]] = []

    async def replay_select_executed(self, generation: int) -> bool:
        initial = False
        async with self._barrier_lock:
            if len(self.initial_generations) < 2:
                initial = True
                self.initial_generations.append(generation)
                if len(self.initial_generations) == 2:
                    self._barrier_release.set()
        self.events.append(("replay-select", generation))
        if initial:
            await self._barrier_release.wait()
        return initial

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[DatabaseConnection]:
        self._generation += 1
        generation = self._generation
        self.events.append(("enter", generation))
        try:
            async with self._database.transaction() as connection:
                yield cast(
                    DatabaseConnection,
                    _CreateRaceConnection(connection, self, generation),
                )
            self.events.append(("commit", generation))
        except BaseException:
            self.events.append(("rollback", generation))
            raise
        finally:
            self.events.append(("exit", generation))


class _QueryAuditConnection:
    def __init__(self, connection: DatabaseConnection, queries: list[str]) -> None:
        self._connection = connection
        self._queries = queries

    async def execute(self, query: Any, params: Any = None, **kwargs: Any) -> Any:
        self._queries.append(" ".join(str(query).lower().split()))
        return await self._connection.execute(query, params, **kwargs)


class _QueryAuditDatabase:
    def __init__(self, database: Database) -> None:
        self._database = database
        self.queries: list[str] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[DatabaseConnection]:
        async with self._database.transaction() as connection:
            yield cast(DatabaseConnection, _QueryAuditConnection(connection, self.queries))


class _ApprovalLockObservation:
    def __init__(self) -> None:
        self.transaction_active = False
        self.mandate_lock_acquired = False
        self.strategy_lock_acquired = False
        self.all_locks_acquired = asyncio.Event()
        self.release = asyncio.Event()
        self.backend_pid: int | None = None


class _PauseAfterAuthorityLocksConnection:
    def __init__(
        self,
        connection: DatabaseConnection,
        observation: _ApprovalLockObservation,
    ) -> None:
        self._connection = connection
        self._observation = observation

    async def execute(self, query: Any, params: Any = None, **kwargs: Any) -> Any:
        result = await self._connection.execute(query, params, **kwargs)
        text = str(query).lower()
        if "from public.operational_mandates" in text and "for share" in text:
            self._observation.mandate_lock_acquired = True
        if "from public.strategy_definitions" in text and "for share" in text:
            self._observation.strategy_lock_acquired = True
            self._observation.all_locks_acquired.set()
            await self._observation.release.wait()
        return result


class _PauseAfterAuthorityLocksDatabase:
    def __init__(
        self,
        database: Database,
        observation: _ApprovalLockObservation,
    ) -> None:
        self._database = database
        self._observation = observation

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[DatabaseConnection]:
        async with self._database.transaction() as connection:
            pid_cursor = await connection.execute("select pg_backend_pid() as pid")
            pid_row = await pid_cursor.fetchone()
            assert pid_row is not None
            self._observation.backend_pid = int(pid_row["pid"])
            self._observation.transaction_active = True
            try:
                yield cast(
                    DatabaseConnection,
                    _PauseAfterAuthorityLocksConnection(connection, self._observation),
                )
            finally:
                self._observation.transaction_active = False


class _ObservedMutationConnection:
    def __init__(
        self,
        connection: DatabaseConnection,
        observation: _MutationObservation,
    ) -> None:
        self._connection = connection
        self._observation = observation

    async def execute(self, query: Any, params: Any = None, **kwargs: Any) -> Any:
        text = str(query).lower()
        if self._observation.blocked_sql_fragment in text:
            self._observation.statement_submitted.set()
        return await self._connection.execute(query, params, **kwargs)


class _MutationObservation:
    def __init__(self, blocked_sql_fragment: str) -> None:
        self.blocked_sql_fragment = blocked_sql_fragment
        self.backend_pid: int | None = None
        self.backend_ready = asyncio.Event()
        self.statement_submitted = asyncio.Event()
        self.committed = False


class _ObservedMutationDatabase:
    def __init__(self, database: Database, observation: _MutationObservation) -> None:
        self._database = database
        self._observation = observation

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[DatabaseConnection]:
        async with self._database.transaction() as connection:
            pid_cursor = await connection.execute("select pg_backend_pid() as pid")
            pid_row = await pid_cursor.fetchone()
            assert pid_row is not None
            self._observation.backend_pid = int(pid_row["pid"])
            self._observation.backend_ready.set()
            yield cast(
                DatabaseConnection,
                _ObservedMutationConnection(connection, self._observation),
            )
        self._observation.committed = True


async def _server_observed_blockers(database: Database, backend_pid: int) -> tuple[int, ...]:
    async with asyncio.timeout(5):
        while True:
            async with database.transaction() as connection:
                cursor = await connection.execute(
                    """
                    select pg_blocking_pids(%s) as blockers, wait_event_type
                    from pg_stat_activity
                    where pid = %s
                    """,
                    (backend_pid, backend_pid),
                )
                row = await cursor.fetchone()
            if row is not None and row["wait_event_type"] == "Lock" and row["blockers"]:
                return tuple(int(pid) for pid in row["blockers"])
            await asyncio.sleep(0.01)


def _assert_no_source_or_write_queries(queries: list[str]) -> None:
    assert not any("public.operational_mandates" in query for query in queries)
    assert not any("public.strategy_definitions" in query for query in queries)
    assert not any(query.startswith("insert ") for query in queries)
    assert not any(query.startswith("update ") for query in queries)


def test_strict_aggregate_reconstruction_rejects_coercion() -> None:
    row: dict[str, object] = {
        "profile_id": uuid4(),
        "state": "DRAFT",
        "current_revision": 1,
        "record_version": 1,
        "approved_revision": None,
        "approved_checksum": None,
        "created_by": uuid4(),
        "created_at": BASE_TIME,
        "approved_by": None,
        "approved_at": None,
        "archived_by": None,
        "archived_at": None,
        "create_idempotency_key": "create-1",
        "create_intent_fingerprint": "a" * 64,
    }
    assert operational_paper_session_profile_from_row(row).current_revision == 1
    row["current_revision"] = True
    with pytest.raises(PersistenceError):
        operational_paper_session_profile_from_row(row)


async def test_create_and_point_reads_round_trip_complete_jsonb(
    database: Database,
    admin_user_id: UUID,
) -> None:
    intent, definition = await _sources(database, admin_user_id)
    repository = PostgresOperationalPaperSessionProfileRepository(database)

    aggregate, revision = await _create(repository, intent, admin_user_id)

    assert aggregate.state is OperationalPaperSessionProfileState.DRAFT
    assert aggregate.current_revision == aggregate.record_version == 1
    assert revision.revision == 1
    assert revision.specification.strategy_snapshot.strategy_definition_id == definition.id
    assert revision.specification.strategy_snapshot.parameters == _resolver(definition)
    assert revision.specification.execution == intent.execution
    assert revision.specification.instrument_constraints == intent.instrument_constraints
    assert revision.specification.risk_limits == intent.risk_limits
    assert revision.specification.market_regime_policy == intent.market_regime_policy
    assert await repository.get(aggregate.profile_id) == aggregate
    assert await repository.get_revision(aggregate.profile_id, 1) == revision
    assert await repository.get_current(aggregate.profile_id) == (aggregate, revision)


async def test_base_execution_assumptions_variant_round_trips(
    database: Database,
    admin_user_id: UUID,
) -> None:
    intent, _ = await _sources(database, admin_user_id)
    execution = ExecutionAssumptions(
        fees=FeeModel(Decimal("1"), Decimal("2")),
        slippage=SlippageModel(fixed_bps=Decimal("3")),
        intrabar_policy=IntrabarPolicy.CONSERVATIVE,
        force_close_at_end=False,
    )
    intent = replace(intent, execution=execution)
    repository = PostgresOperationalPaperSessionProfileRepository(database)

    aggregate, revision = await _create(repository, intent, admin_user_id)

    assert type(revision.specification.execution) is ExecutionAssumptions
    assert revision.specification.execution == execution
    assert await repository.get_current(aggregate.profile_id) == (aggregate, revision)


async def test_missing_reads_return_none(database: Database) -> None:
    repository = PostgresOperationalPaperSessionProfileRepository(database)
    profile_id = uuid4()
    assert await repository.get(profile_id) is None
    assert await repository.get_revision(profile_id, 1) is None
    assert await repository.get_current(profile_id) is None


@pytest.mark.parametrize("method", ["get", "get_revision", "get_current"])
async def test_invalid_ids_are_rejected_before_database(method: str) -> None:
    repository = PostgresOperationalPaperSessionProfileRepository(
        cast(Database, _DatabaseMustNotBeAccessed())
    )
    with pytest.raises(InvalidOperationalPaperSessionProfileSpecificationError):
        if method == "get_revision":
            await repository.get_revision(cast(UUID, "bad"), 1)
        else:
            await getattr(repository, method)(cast(UUID, "bad"))


async def test_create_replay_precedes_source_resolution_and_divergence_conflicts(
    database: Database,
    admin_user_id: UUID,
) -> None:
    intent, _ = await _sources(database, admin_user_id)
    repository = PostgresOperationalPaperSessionProfileRepository(database)
    first_resolver = _CountingResolver()
    first = await _create(repository, intent, admin_user_id, resolver=first_resolver)
    replay_resolver = _CountingResolver()
    replay = await _create(repository, intent, admin_user_id, resolver=replay_resolver)

    assert replay == first
    assert len(first_resolver.calls) == 1
    assert replay_resolver.calls == []
    with pytest.raises(OperationalPaperSessionProfileIdempotencyConflictError):
        await _create(
            repository,
            replace(intent, name="Different intent"),
            admin_user_id,
        )
    assert await _counts(database, first[0].profile_id) == (1, 1)


@pytest.mark.parametrize(
    "source_mutation",
    ["strategy_replace", "strategy_archive", "mandate_archive"],
)
async def test_exact_create_replay_survives_committed_source_mutation_without_resolution(
    database: Database,
    admin_user_id: UUID,
    source_mutation: str,
) -> None:
    intent, definition = await _sources(database, admin_user_id)
    repository = PostgresOperationalPaperSessionProfileRepository(database)
    original = await _create(
        repository,
        intent,
        admin_user_id,
        key=f"replay-after-{source_mutation}",
    )
    if source_mutation == "strategy_replace":
        await PostgresStrategyDefinitionRepository(database).replace(
            definition.id,
            _definition_spec(display_name="Committed replacement"),
            expected_revision=1,
            actor_id=admin_user_id,
        )
    elif source_mutation == "strategy_archive":
        await PostgresStrategyDefinitionRepository(database).archive(
            definition.id,
            expected_revision=1,
            actor_id=admin_user_id,
        )
    else:
        await PostgresOperationalMandateRepository(database).archive(
            intent.mandate_binding.mandate_id,
            expected_record_version=2,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=3),
        )
    audit_database = _QueryAuditDatabase(database)
    replay_repository = PostgresOperationalPaperSessionProfileRepository(
        cast(Database, audit_database)
    )
    resolver = _CountingResolver()

    replay = await _create(
        replay_repository,
        intent,
        admin_user_id,
        key=f"replay-after-{source_mutation}",
        resolver=resolver,
    )

    assert replay == original
    assert resolver.calls == []
    assert await _counts(database, original[0].profile_id) == (1, 1)
    _assert_no_source_or_write_queries(audit_database.queries)


async def test_same_intent_create_race_uses_named_unique_violation_and_new_replay_transaction(
    database: Database,
    admin_user_id: UUID,
) -> None:
    intent, _ = await _sources(database, admin_user_id)
    observation = _CreateRaceDatabase(database)
    repository = PostgresOperationalPaperSessionProfileRepository(cast(Database, observation))
    resolver = _CountingResolver()

    results = await asyncio.gather(
        _create(repository, intent, admin_user_id, key="equal-race", resolver=resolver),
        _create(repository, intent, admin_user_id, key="equal-race", resolver=resolver),
    )

    assert results[0] == results[1]
    assert len(observation.initial_generations) == 2
    assert len(set(observation.initial_generations)) == 2
    assert sum(initial for _, initial, _ in observation.replay_results) == 2
    assert all(initial and missed for _, initial, missed in observation.replay_results if initial)
    assert len(observation.unique_violations) == 1
    losing_generation = observation.unique_violations[0][0]
    assert observation.unique_violations[0][1] == (
        "operational_paper_session_profiles_actor_idempotency_key"
    )
    assert ("rollback", losing_generation) in observation.events
    replay_generations = [
        generation
        for generation, initial, missed in observation.replay_results
        if not initial and not missed
    ]
    assert len(replay_generations) == 1
    replay_generation = replay_generations[0]
    assert replay_generation != losing_generation
    assert observation.events.index(("exit", losing_generation)) < observation.events.index(
        ("replay-select", replay_generation)
    )
    assert len(resolver.calls) == 2
    assert await _counts(database, results[0][0].profile_id) == (1, 1)


async def test_divergent_create_race_uses_named_unique_violation_without_orphan_revision(
    database: Database,
    admin_user_id: UUID,
) -> None:
    intent, _ = await _sources(database, admin_user_id)
    observation = _CreateRaceDatabase(database)
    repository = PostgresOperationalPaperSessionProfileRepository(cast(Database, observation))
    resolver = _CountingResolver()

    results = await asyncio.gather(
        _create(
            repository,
            intent,
            admin_user_id,
            key="different-race",
            resolver=resolver,
        ),
        _create(
            repository,
            replace(intent, description="Different"),
            admin_user_id,
            key="different-race",
            resolver=resolver,
        ),
        return_exceptions=True,
    )

    assert len(observation.initial_generations) == 2
    assert len(set(observation.initial_generations)) == 2
    assert sum(initial for _, initial, _ in observation.replay_results) == 2
    assert all(initial and missed for _, initial, missed in observation.replay_results if initial)
    assert len(observation.unique_violations) == 1
    losing_generation, constraint = observation.unique_violations[0]
    assert constraint == "operational_paper_session_profiles_actor_idempotency_key"
    assert ("rollback", losing_generation) in observation.events
    replay_generations = [
        generation
        for generation, initial, missed in observation.replay_results
        if not initial and not missed
    ]
    assert len(replay_generations) == 1
    assert observation.events.index(("exit", losing_generation)) < observation.events.index(
        ("replay-select", replay_generations[0])
    )
    assert sum(isinstance(item, tuple) for item in results) == 1
    assert (
        sum(
            isinstance(item, OperationalPaperSessionProfileIdempotencyConflictError)
            for item in results
        )
        == 1
    )
    committed = next(item for item in results if isinstance(item, tuple))
    assert len(resolver.calls) == 2
    assert await _counts(database, committed[0].profile_id) == (1, 1)


@pytest.mark.parametrize(
    ("mutation", "error_type"),
    [
        ("missing_mandate", OperationalMandateNotFoundError),
        ("wrong_mandate_revision", OperationalMandateRevisionConflictError),
        ("wrong_mandate_checksum", OperationalMandateChecksumMismatchError),
        ("wrong_instrument", OperationalMandateRevisionConflictError),
        ("missing_strategy", StrategyDefinitionNotFoundError),
        ("stale_strategy", StrategyDefinitionRevisionConflictError),
        ("wrong_strategy_checksum", StrategyDefinitionCompatibilityError),
    ],
)
async def test_new_create_rejects_invalid_source_authority(
    database: Database,
    admin_user_id: UUID,
    mutation: str,
    error_type: type[Exception],
) -> None:
    intent, _ = await _sources(database, admin_user_id)
    if mutation == "missing_mandate":
        intent = replace(
            intent,
            mandate_binding=replace(intent.mandate_binding, mandate_id=uuid4()),
        )
    elif mutation == "wrong_mandate_revision":
        intent = replace(
            intent,
            mandate_binding=replace(intent.mandate_binding, approved_revision=2),
        )
    elif mutation == "wrong_mandate_checksum":
        intent = replace(
            intent,
            mandate_binding=replace(intent.mandate_binding, specification_checksum="f" * 64),
        )
    elif mutation == "wrong_instrument":
        intent = replace(intent, selected_instrument=_instrument("ETH"))
    elif mutation == "missing_strategy":
        intent = replace(intent, strategy_definition_id=uuid4())
    elif mutation == "stale_strategy":
        intent = replace(intent, expected_strategy_definition_revision=2)
    else:
        intent = replace(intent, expected_strategy_parameters_checksum="f" * 64)

    repository = PostgresOperationalPaperSessionProfileRepository(database)
    with pytest.raises(error_type):
        await _create(repository, intent, admin_user_id)


async def test_archived_sources_reject_new_create(
    database: Database,
    admin_user_id: UUID,
) -> None:
    intent, definition = await _sources(database, admin_user_id)
    await PostgresStrategyDefinitionRepository(database).archive(
        definition.id,
        expected_revision=definition.revision,
        actor_id=admin_user_id,
    )
    with pytest.raises(StrategyDefinitionArchivedError):
        await _create(
            PostgresOperationalPaperSessionProfileRepository(database),
            intent,
            admin_user_id,
        )


async def test_draft_noop_precedes_resolution_and_stale_tokens(
    database: Database,
    admin_user_id: UUID,
) -> None:
    intent, _ = await _sources(database, admin_user_id)
    ordinary_repository = PostgresOperationalPaperSessionProfileRepository(database)
    created = await _create(ordinary_repository, intent, admin_user_id)
    audit_database = _QueryAuditDatabase(database)
    repository = PostgresOperationalPaperSessionProfileRepository(cast(Database, audit_database))
    resolver = _CountingResolver()

    with pytest.raises(OperationalPaperSessionProfileRevisionConflictError):
        await repository.replace_draft(
            created[0].profile_id,
            intent,
            expected_revision=2,
            expected_record_version=1,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=3),
            strategy_resolver=resolver,
        )
    assert not any("profile_revisions" in query for query in audit_database.queries)
    _assert_no_source_or_write_queries(audit_database.queries)
    audit_database.queries.clear()
    with pytest.raises(OperationalPaperSessionProfileRecordVersionConflictError):
        await repository.replace_draft(
            created[0].profile_id,
            intent,
            expected_revision=1,
            expected_record_version=2,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=3),
            strategy_resolver=resolver,
        )
    assert not any("profile_revisions" in query for query in audit_database.queries)
    _assert_no_source_or_write_queries(audit_database.queries)
    audit_database.queries.clear()
    noop = await repository.replace_draft(
        created[0].profile_id,
        intent,
        expected_revision=1,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=3),
        strategy_resolver=resolver,
    )
    assert noop == created
    assert resolver.calls == []
    assert await _counts(database, created[0].profile_id) == (1, 1)
    assert sum("profile_revisions" in query for query in audit_database.queries) == 1
    _assert_no_source_or_write_queries(audit_database.queries)


async def test_replace_draft_tokens_precede_corrupt_current_revision_reconstruction(
    database: Database,
    database_url: str,
    admin_user_id: UUID,
) -> None:
    intent, _ = await _sources(database, admin_user_id)
    repository = PostgresOperationalPaperSessionProfileRepository(database)
    aggregate, _ = await _create(repository, intent, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            alter table public.operational_paper_session_profile_revisions
            disable trigger op_ps_profile_revisions_reject_update_delete
            """
        )
        connection.execute(
            """
            update public.operational_paper_session_profile_revisions
            set execution = %s
            where profile_id = %s and revision = 1
            """,
            (Jsonb({"fees": {}}), aggregate.profile_id),
        )
    audit_database = _QueryAuditDatabase(database)
    audited_repository = PostgresOperationalPaperSessionProfileRepository(
        cast(Database, audit_database)
    )

    with pytest.raises(OperationalPaperSessionProfileRevisionConflictError):
        await audited_repository.replace_draft(
            aggregate.profile_id,
            intent,
            expected_revision=2,
            expected_record_version=1,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=3),
            strategy_resolver=_resolver,
        )
    assert not any("profile_revisions" in query for query in audit_database.queries)
    audit_database.queries.clear()
    with pytest.raises(OperationalPaperSessionProfileRecordVersionConflictError):
        await audited_repository.replace_draft(
            aggregate.profile_id,
            intent,
            expected_revision=1,
            expected_record_version=2,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=3),
            strategy_resolver=_resolver,
        )
    assert not any("profile_revisions" in query for query in audit_database.queries)
    audit_database.queries.clear()
    with pytest.raises(PersistenceError):
        await audited_repository.replace_draft(
            aggregate.profile_id,
            intent,
            expected_revision=1,
            expected_record_version=1,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=3),
            strategy_resolver=_resolver,
        )
    assert any("profile_revisions" in query for query in audit_database.queries)
    await repository.archive(
        aggregate.profile_id,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=4),
    )
    audit_database.queries.clear()
    with pytest.raises(OperationalPaperSessionProfileStateTransitionConflictError):
        await audited_repository.replace_draft(
            aggregate.profile_id,
            intent,
            expected_revision=1,
            expected_record_version=2,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=5),
            strategy_resolver=_resolver,
        )
    assert not any("profile_revisions" in query for query in audit_database.queries)


async def test_changed_draft_appends_once_and_revalidates_sources(
    database: Database,
    admin_user_id: UUID,
) -> None:
    intent, _ = await _sources(database, admin_user_id)
    repository = PostgresOperationalPaperSessionProfileRepository(database)
    original = await _create(repository, intent, admin_user_id)
    changed_intent = replace(intent, name="Revision two")
    resolver = _CountingResolver()
    changed = await repository.replace_draft(
        original[0].profile_id,
        changed_intent,
        expected_revision=1,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=3),
        strategy_resolver=resolver,
    )
    assert (changed[0].current_revision, changed[0].record_version) == (2, 2)
    assert changed[1].revision == 2
    assert len(resolver.calls) == 1
    assert await repository.get_revision(changed[0].profile_id, 1) == original[1]
    assert await _counts(database, changed[0].profile_id) == (1, 2)


@pytest.mark.parametrize(
    ("source_failure", "error_type"),
    [
        ("mandate_archived", OperationalMandateStateTransitionConflictError),
        ("mandate_binding_stale", OperationalMandateRevisionConflictError),
        ("strategy_archived", StrategyDefinitionArchivedError),
        ("strategy_revision_stale", StrategyDefinitionRevisionConflictError),
        ("strategy_checksum_stale", StrategyDefinitionCompatibilityError),
    ],
)
async def test_changed_draft_source_failures_preserve_profile_history(
    database: Database,
    admin_user_id: UUID,
    source_failure: str,
    error_type: type[Exception],
) -> None:
    intent, definition = await _sources(database, admin_user_id)
    repository = PostgresOperationalPaperSessionProfileRepository(database)
    aggregate, revision = await _create(repository, intent, admin_user_id)
    changed = replace(intent, name="Changed profile intent")
    if source_failure == "mandate_archived":
        await PostgresOperationalMandateRepository(database).archive(
            intent.mandate_binding.mandate_id,
            expected_record_version=2,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=3),
        )
    elif source_failure == "mandate_binding_stale":
        changed = replace(
            changed,
            mandate_binding=replace(changed.mandate_binding, approved_revision=2),
        )
    elif source_failure == "strategy_archived":
        await PostgresStrategyDefinitionRepository(database).archive(
            definition.id,
            expected_revision=1,
            actor_id=admin_user_id,
        )
    elif source_failure == "strategy_revision_stale":
        changed = replace(changed, expected_strategy_definition_revision=2)
    else:
        changed = replace(changed, expected_strategy_parameters_checksum="f" * 64)
    resolver = _CountingResolver()

    with pytest.raises(error_type):
        await repository.replace_draft(
            aggregate.profile_id,
            changed,
            expected_revision=1,
            expected_record_version=1,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=4),
            strategy_resolver=resolver,
        )

    assert resolver.calls == []
    assert await repository.get_current(aggregate.profile_id) == (aggregate, revision)
    assert await _counts(database, aggregate.profile_id) == (1, 1)


async def test_approval_exact_success_and_replay_skip_mutable_sources(
    database: Database,
    admin_user_id: UUID,
) -> None:
    intent, definition = await _sources(database, admin_user_id)
    repository = PostgresOperationalPaperSessionProfileRepository(database)
    aggregate, revision = await _create(repository, intent, admin_user_id)
    resolver = _CountingResolver()
    approved = await repository.approve(
        aggregate.profile_id,
        expected_revision=1,
        expected_checksum=revision.specification_checksum,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=3),
        strategy_resolver=resolver,
    )
    assert approved.state is OperationalPaperSessionProfileState.APPROVED
    assert approved.record_version == 2
    assert len(resolver.calls) == 1

    await PostgresStrategyDefinitionRepository(database).replace(
        definition.id,
        _definition_spec(display_name="Later source revision"),
        expected_revision=1,
        actor_id=admin_user_id,
    )
    audit_database = _QueryAuditDatabase(database)
    replay_repository = PostgresOperationalPaperSessionProfileRepository(
        cast(Database, audit_database)
    )
    replay_resolver = _CountingResolver()
    replay = await replay_repository.approve(
        aggregate.profile_id,
        expected_revision=1,
        expected_checksum=revision.specification_checksum,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(days=1),
        strategy_resolver=replay_resolver,
    )
    assert replay == approved
    assert replay_resolver.calls == []
    _assert_no_source_or_write_queries(audit_database.queries)
    assert not any("profile_revisions" in query for query in audit_database.queries)
    assert await repository.get_revision(aggregate.profile_id, 1) == revision


@pytest.mark.parametrize(
    "variation",
    ["different_actor", "wrong_checksum", "wrong_revision", "wrong_version"],
)
async def test_approved_nonexact_replay_conflicts_without_source_access(
    database: Database,
    admin_user_id: UUID,
    variation: str,
) -> None:
    intent, _ = await _sources(database, admin_user_id)
    ordinary_repository = PostgresOperationalPaperSessionProfileRepository(database)
    aggregate, revision = await _create(ordinary_repository, intent, admin_user_id)
    approved = await ordinary_repository.approve(
        aggregate.profile_id,
        expected_revision=1,
        expected_checksum=revision.specification_checksum,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=3),
        strategy_resolver=_resolver,
    )
    audit_database = _QueryAuditDatabase(database)
    repository = PostgresOperationalPaperSessionProfileRepository(cast(Database, audit_database))
    actor_id = uuid4() if variation == "different_actor" else admin_user_id
    checksum = "f" * 64 if variation == "wrong_checksum" else revision.specification_checksum
    expected_revision = 2 if variation == "wrong_revision" else 1
    expected_version = 2 if variation == "wrong_version" else 1

    with pytest.raises(OperationalPaperSessionProfileStateTransitionConflictError):
        await repository.approve(
            aggregate.profile_id,
            expected_revision=expected_revision,
            expected_checksum=checksum,
            expected_record_version=expected_version,
            actor_id=actor_id,
            now=BASE_TIME + timedelta(days=1),
            strategy_resolver=_resolver,
        )

    assert await ordinary_repository.get(aggregate.profile_id) == approved
    _assert_no_source_or_write_queries(audit_database.queries)
    assert not any("profile_revisions" in query for query in audit_database.queries)


@pytest.mark.parametrize(
    ("revision_delta", "version_delta", "checksum", "error_type"),
    [
        (1, 0, None, OperationalPaperSessionProfileRevisionConflictError),
        (0, 1, None, OperationalPaperSessionProfileRecordVersionConflictError),
        (0, 0, "f" * 64, OperationalPaperSessionProfileChecksumMismatchError),
    ],
)
async def test_approval_guards_are_specific(
    database: Database,
    admin_user_id: UUID,
    revision_delta: int,
    version_delta: int,
    checksum: str | None,
    error_type: type[Exception],
) -> None:
    intent, _ = await _sources(database, admin_user_id)
    repository = PostgresOperationalPaperSessionProfileRepository(database)
    aggregate, revision = await _create(repository, intent, admin_user_id)
    with pytest.raises(error_type):
        await repository.approve(
            aggregate.profile_id,
            expected_revision=1 + revision_delta,
            expected_checksum=checksum or revision.specification_checksum,
            expected_record_version=1 + version_delta,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=3),
            strategy_resolver=_resolver,
        )


async def test_source_change_before_approval_fails_without_rewriting_history(
    database: Database,
    admin_user_id: UUID,
) -> None:
    intent, definition = await _sources(database, admin_user_id)
    repository = PostgresOperationalPaperSessionProfileRepository(database)
    aggregate, revision = await _create(repository, intent, admin_user_id)
    await PostgresStrategyDefinitionRepository(database).replace(
        definition.id,
        _definition_spec(display_name="Changed before approval"),
        expected_revision=1,
        actor_id=admin_user_id,
    )
    with pytest.raises(StrategyDefinitionRevisionConflictError):
        await repository.approve(
            aggregate.profile_id,
            expected_revision=1,
            expected_checksum=revision.specification_checksum,
            expected_record_version=1,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=3),
            strategy_resolver=_resolver,
        )
    assert await repository.get_revision(aggregate.profile_id, 1) == revision


async def test_mandate_archive_before_approval_fails(
    database: Database,
    admin_user_id: UUID,
) -> None:
    intent, _ = await _sources(database, admin_user_id)
    repository = PostgresOperationalPaperSessionProfileRepository(database)
    aggregate, revision = await _create(repository, intent, admin_user_id)
    await PostgresOperationalMandateRepository(database).archive(
        intent.mandate_binding.mandate_id,
        expected_record_version=2,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=3),
    )
    with pytest.raises(OperationalMandateStateTransitionConflictError):
        await repository.approve(
            aggregate.profile_id,
            expected_revision=1,
            expected_checksum=revision.specification_checksum,
            expected_record_version=1,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=4),
            strategy_resolver=_resolver,
        )
    assert await repository.get_current(aggregate.profile_id) == (aggregate, revision)


async def test_strategy_archive_commits_before_approval_and_fails_closed(
    database: Database,
    admin_user_id: UUID,
) -> None:
    intent, definition = await _sources(database, admin_user_id)
    repository = PostgresOperationalPaperSessionProfileRepository(database)
    aggregate, revision = await _create(repository, intent, admin_user_id)
    archived = await PostgresStrategyDefinitionRepository(database).archive(
        definition.id,
        expected_revision=1,
        actor_id=admin_user_id,
    )
    assert archived.state.value == "ARCHIVED"

    with pytest.raises(StrategyDefinitionArchivedError):
        await repository.approve(
            aggregate.profile_id,
            expected_revision=1,
            expected_checksum=revision.specification_checksum,
            expected_record_version=1,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=3),
            strategy_resolver=_resolver,
        )

    assert await repository.get_current(aggregate.profile_id) == (aggregate, revision)


async def test_approval_snapshot_mismatch_fails_closed_without_metadata(
    database: Database,
    admin_user_id: UUID,
) -> None:
    intent, _ = await _sources(database, admin_user_id)
    repository = PostgresOperationalPaperSessionProfileRepository(database)
    aggregate, revision = await _create(repository, intent, admin_user_id)

    def mismatching_resolver(definition: StrategyDefinition) -> StrategyParameters:
        parameters = list(_resolver(definition))
        parameters[-1] = ("ratio", Decimal("2.5"))
        return cast(StrategyParameters, tuple(parameters))

    with pytest.raises(StrategyDefinitionCompatibilityError):
        await repository.approve(
            aggregate.profile_id,
            expected_revision=1,
            expected_checksum=revision.specification_checksum,
            expected_record_version=1,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=3),
            strategy_resolver=mismatching_resolver,
        )

    assert await repository.get_current(aggregate.profile_id) == (aggregate, revision)


@pytest.mark.parametrize(
    ("source_mutation", "blocked_sql_fragment"),
    [
        ("mandate_archive", "from public.operational_mandates"),
        ("strategy_replace", "update public.strategy_definitions"),
        ("strategy_archive", "update public.strategy_definitions"),
    ],
)
async def test_approval_authority_locks_server_block_each_source_mutation(
    database: Database,
    admin_user_id: UUID,
    source_mutation: str,
    blocked_sql_fragment: str,
) -> None:
    intent, definition = await _sources(database, admin_user_id)
    ordinary_repository = PostgresOperationalPaperSessionProfileRepository(database)
    aggregate, revision = await _create(ordinary_repository, intent, admin_user_id)
    approval_observation = _ApprovalLockObservation()
    approval_repository = PostgresOperationalPaperSessionProfileRepository(
        cast(
            Database,
            _PauseAfterAuthorityLocksDatabase(database, approval_observation),
        )
    )
    mutation_observation = _MutationObservation(blocked_sql_fragment)
    mutation_database = cast(
        Database,
        _ObservedMutationDatabase(database, mutation_observation),
    )

    approval_task = asyncio.create_task(
        approval_repository.approve(
            aggregate.profile_id,
            expected_revision=1,
            expected_checksum=revision.specification_checksum,
            expected_record_version=1,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=3),
            strategy_resolver=_resolver,
        )
    )
    mutation_task: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(approval_observation.all_locks_acquired.wait(), timeout=5)
        assert approval_observation.transaction_active
        assert approval_observation.mandate_lock_acquired
        assert approval_observation.strategy_lock_acquired
        if source_mutation == "mandate_archive":
            mutation_task = asyncio.create_task(
                PostgresOperationalMandateRepository(mutation_database).archive(
                    intent.mandate_binding.mandate_id,
                    expected_record_version=2,
                    actor_id=admin_user_id,
                    now=BASE_TIME + timedelta(seconds=4),
                )
            )
        elif source_mutation == "strategy_replace":
            mutation_task = asyncio.create_task(
                PostgresStrategyDefinitionRepository(mutation_database).replace(
                    definition.id,
                    _definition_spec(display_name="Waits for profile approval"),
                    expected_revision=1,
                    actor_id=admin_user_id,
                )
            )
        else:
            mutation_task = asyncio.create_task(
                PostgresStrategyDefinitionRepository(mutation_database).archive(
                    definition.id,
                    expected_revision=1,
                    actor_id=admin_user_id,
                )
            )
        await asyncio.wait_for(mutation_observation.backend_ready.wait(), timeout=5)
        await asyncio.wait_for(mutation_observation.statement_submitted.wait(), timeout=5)
        assert mutation_observation.backend_pid is not None
        blockers = await _server_observed_blockers(
            database,
            mutation_observation.backend_pid,
        )
        assert approval_observation.backend_pid in blockers
        assert not mutation_observation.committed
        assert not mutation_task.done()
        approval_observation.release.set()
        approved, mutation = await asyncio.gather(approval_task, mutation_task)
    finally:
        approval_observation.release.set()
        tasks = [approval_task]
        if mutation_task is not None:
            tasks.append(mutation_task)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    assert approved.state is OperationalPaperSessionProfileState.APPROVED
    assert mutation_observation.committed
    if source_mutation.startswith("strategy"):
        assert mutation.revision == 2
    else:
        assert mutation.state.value == "ARCHIVED"
    assert await ordinary_repository.get_revision(aggregate.profile_id, 1) == revision
    assert await ordinary_repository.get_current(aggregate.profile_id) == (approved, revision)


async def test_approval_resolver_runs_inside_transaction_after_both_source_locks(
    database: Database,
    admin_user_id: UUID,
) -> None:
    intent, _ = await _sources(database, admin_user_id)
    ordinary_repository = PostgresOperationalPaperSessionProfileRepository(database)
    aggregate, revision = await _create(ordinary_repository, intent, admin_user_id)
    observation = _ApprovalLockObservation()
    observation.release.set()
    repository = PostgresOperationalPaperSessionProfileRepository(
        cast(Database, _PauseAfterAuthorityLocksDatabase(database, observation))
    )
    resolver_called = False

    def observing_resolver(definition: StrategyDefinition) -> StrategyParameters:
        nonlocal resolver_called
        resolver_called = True
        assert observation.transaction_active
        assert observation.mandate_lock_acquired
        assert observation.strategy_lock_acquired
        return _resolver(definition)

    approved = await repository.approve(
        aggregate.profile_id,
        expected_revision=1,
        expected_checksum=revision.specification_checksum,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=3),
        strategy_resolver=observing_resolver,
    )

    assert resolver_called
    assert approved.state is OperationalPaperSessionProfileState.APPROVED
    assert not observation.transaction_active


async def test_exact_approval_replay_survives_later_mandate_and_strategy_archive(
    database: Database,
    admin_user_id: UUID,
) -> None:
    intent, definition = await _sources(database, admin_user_id)
    repository = PostgresOperationalPaperSessionProfileRepository(database)
    aggregate, revision = await _create(repository, intent, admin_user_id)
    approved = await repository.approve(
        aggregate.profile_id,
        expected_revision=1,
        expected_checksum=revision.specification_checksum,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=3),
        strategy_resolver=_resolver,
    )
    await PostgresOperationalMandateRepository(database).archive(
        intent.mandate_binding.mandate_id,
        expected_record_version=2,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=4),
    )
    await PostgresStrategyDefinitionRepository(database).archive(
        definition.id,
        expected_revision=1,
        actor_id=admin_user_id,
    )
    resolver = _CountingResolver()
    replay = await repository.approve(
        aggregate.profile_id,
        expected_revision=1,
        expected_checksum=revision.specification_checksum,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(days=1),
        strategy_resolver=resolver,
    )
    assert replay == approved
    assert resolver.calls == []
    assert await repository.get_revision(aggregate.profile_id, 1) == revision


async def test_draft_and_approved_archive_preserve_history_and_exact_replay(
    database: Database,
    admin_user_id: UUID,
) -> None:
    first_intent, _ = await _sources(database, admin_user_id)
    repository = PostgresOperationalPaperSessionProfileRepository(database)
    draft, draft_revision = await _create(
        repository,
        first_intent,
        admin_user_id,
        key="draft-archive",
    )
    archived_draft = await repository.archive(
        draft.profile_id,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=3),
    )
    assert archived_draft.state is OperationalPaperSessionProfileState.ARCHIVED
    assert await repository.get_revision(draft.profile_id, 1) == draft_revision
    audit_database = _QueryAuditDatabase(database)
    replay_repository = PostgresOperationalPaperSessionProfileRepository(
        cast(Database, audit_database)
    )
    assert (
        await replay_repository.archive(
            draft.profile_id,
            expected_record_version=1,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(days=1),
        )
        == archived_draft
    )
    with pytest.raises(OperationalPaperSessionProfileStateTransitionConflictError):
        await replay_repository.archive(
            draft.profile_id,
            expected_record_version=1,
            actor_id=uuid4(),
            now=BASE_TIME + timedelta(days=1),
        )
    with pytest.raises(OperationalPaperSessionProfileStateTransitionConflictError):
        await replay_repository.archive(
            draft.profile_id,
            expected_record_version=2,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(days=1),
        )
    _assert_no_source_or_write_queries(audit_database.queries)
    assert not any("profile_revisions" in query for query in audit_database.queries)

    second_intent = replace(first_intent, name="Approved archive")
    approved_aggregate, approved_revision = await _create(
        repository,
        second_intent,
        admin_user_id,
        key="approved-archive",
    )
    approved = await repository.approve(
        approved_aggregate.profile_id,
        expected_revision=1,
        expected_checksum=approved_revision.specification_checksum,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=3),
        strategy_resolver=_resolver,
    )
    archived = await repository.archive(
        approved.profile_id,
        expected_record_version=2,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=4),
    )
    assert archived.approved_revision == approved.approved_revision
    assert archived.approved_checksum == approved.approved_checksum
    assert archived.approved_at == approved.approved_at
    assert await _counts(database, approved.profile_id) == (2, 1)
    assert await _counts(database, draft.profile_id) == (2, 1)


@pytest.mark.parametrize(
    ("column", "malformed"),
    [
        ("strategy_parameters", [{"name": "x", "type": "decimal", "value": 1.0}]),
        ("instrument_constraints", {"minimum_quantity": "1"}),
        ("market_regime_policy", {"schema_version": 1}),
        ("market_regime_policy", None),
    ],
)
async def test_malformed_persisted_jsonb_becomes_persistence_error(
    database: Database,
    database_url: str,
    admin_user_id: UUID,
    column: str,
    malformed: object,
) -> None:
    intent, _ = await _sources(database, admin_user_id)
    repository = PostgresOperationalPaperSessionProfileRepository(database)
    aggregate, _ = await _create(repository, intent, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            alter table public.operational_paper_session_profile_revisions
            disable trigger op_ps_profile_revisions_reject_update_delete
            """
        )
        if column == "market_regime_policy" and malformed is None:
            connection.execute(
                """
                alter table public.operational_paper_session_profile_revisions
                drop constraint
                    operational_paper_session_profile_revisions_market_regime_check
                """
            )
        connection.execute(
            f"""
            update public.operational_paper_session_profile_revisions
            set {column} = %s
            where profile_id = %s and revision = 1
            """,  # noqa: S608 - column is a closed parametrized test constant.
            (Jsonb(malformed), aggregate.profile_id),
        )
    with pytest.raises(PersistenceError):
        await repository.get_revision(aggregate.profile_id, 1)


@pytest.mark.parametrize(
    ("column", "target"),
    [
        ("risk_limits", "integer_bool"),
        ("execution", "slippage_discriminator"),
        ("risk_limits", "stop_loss_value_type"),
    ],
)
async def test_single_field_nested_json_corruption_is_rejected_precisely(
    database: Database,
    database_url: str,
    admin_user_id: UUID,
    column: str,
    target: str,
) -> None:
    intent, _ = await _sources(database, admin_user_id)
    repository = PostgresOperationalPaperSessionProfileRepository(database)
    aggregate, _ = await _create(repository, intent, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        row = connection.execute(
            f"""
            select {column}
            from public.operational_paper_session_profile_revisions
            where profile_id = %s and revision = 1
            """,  # noqa: S608 - column is a closed parametrized test constant.
            (aggregate.profile_id,),
        ).fetchone()
        assert row is not None
        payload = deepcopy(row[0])
        if target == "integer_bool":
            payload["max_open_orders"] = True
        elif target == "slippage_discriminator":
            payload["slippage"]["kind"] = "unsupported"
        else:
            payload["stop_loss"]["value"] = 5
        connection.execute(
            """
            alter table public.operational_paper_session_profile_revisions
            disable trigger op_ps_profile_revisions_reject_update_delete
            """
        )
        connection.execute(
            f"""
            update public.operational_paper_session_profile_revisions
            set {column} = %s
            where profile_id = %s and revision = 1
            """,  # noqa: S608 - column is a closed parametrized test constant.
            (Jsonb(payload), aggregate.profile_id),
        )

    with pytest.raises(PersistenceError):
        await repository.get_revision(aggregate.profile_id, 1)


async def test_persisted_checksum_disagreement_becomes_persistence_error(
    database: Database,
    database_url: str,
    admin_user_id: UUID,
) -> None:
    intent, _ = await _sources(database, admin_user_id)
    repository = PostgresOperationalPaperSessionProfileRepository(database)
    aggregate, _ = await _create(repository, intent, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            alter table public.operational_paper_session_profile_revisions
            disable trigger op_ps_profile_revisions_reject_update_delete
            """
        )
        connection.execute(
            """
            update public.operational_paper_session_profile_revisions
            set specification_checksum = repeat('f', 64)
            where profile_id = %s and revision = 1
            """,
            (aggregate.profile_id,),
        )
    with pytest.raises(PersistenceError):
        await repository.get_current(aggregate.profile_id)


@pytest.mark.parametrize(
    ("expected_revision", "expected_version", "now", "error_type"),
    [
        (True, 1, BASE_TIME, OperationalPaperSessionProfileRevisionConflictError),
        (1, True, BASE_TIME, OperationalPaperSessionProfileRecordVersionConflictError),
        (1, 1, datetime(2026, 8, 23, 12), InvalidOperationalPaperSessionProfileSpecificationError),
    ],
)
async def test_mutation_input_validation_precedes_database(
    expected_revision: int,
    expected_version: int,
    now: datetime,
    error_type: type[Exception],
) -> None:
    repository = PostgresOperationalPaperSessionProfileRepository(
        cast(Database, _DatabaseMustNotBeAccessed())
    )
    with pytest.raises(error_type):
        await repository.approve(
            uuid4(),
            expected_revision=expected_revision,
            expected_checksum="a" * 64,
            expected_record_version=expected_version,
            actor_id=uuid4(),
            now=now,
            strategy_resolver=_resolver,
        )


async def test_unknown_database_error_is_safely_translated_and_rolls_back(
    database: Database,
    admin_user_id: UUID,
) -> None:
    intent, _ = await _sources(database, admin_user_id)
    repository = PostgresOperationalPaperSessionProfileRepository(database)
    async with database.transaction() as connection:
        await connection.execute(
            """
            alter table public.operational_paper_session_profiles
            add constraint gate_2c_forced_unknown_failure check (false) not valid
            """
        )
    with pytest.raises(PersistenceError):
        await _create(repository, intent, admin_user_id)
    async with database.transaction() as connection:
        cursor = await connection.execute(
            "select count(*) as total from public.operational_paper_session_profile_revisions"
        )
        row = await cursor.fetchone()
    assert row is not None and row["total"] == 0


def test_gate_2c_contains_no_runtime_or_capital_surface() -> None:
    public_methods = {
        name
        for name in vars(PostgresOperationalPaperSessionProfileRepository)
        if not name.startswith("_")
    }
    assert public_methods == {
        "get",
        "get_revision",
        "get_current",
        "create",
        "replace_draft",
        "approve",
        "archive",
    }
