"""Repository tests for Phase 7-10 activation persistence and races."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from app.database import Database
from app.domain.errors import (
    PersistenceError,
    SimulationNotFoundError,
    SimulationTerminalError,
)
from app.operational_mandates.errors import (
    OperationalMandateNotFoundError,
    OperationalMandateStateTransitionConflictError,
)
from app.operational_paper_capital_authorizations.errors import (
    OperationalPaperCapitalAuthorizationChecksumMismatchError,
    OperationalPaperCapitalAuthorizationCurrencyMismatchError,
    OperationalPaperCapitalAuthorizationNotFoundError,
    OperationalPaperCapitalAuthorizationStateTransitionConflictError,
)
from app.operational_paper_session_activations import (
    InvalidOperationalPaperSessionActivationSpecificationError,
    OperationalPaperSessionActivation,
    OperationalPaperSessionActivationBoundsExceededError,
    OperationalPaperSessionActivationCreateIntent,
    OperationalPaperSessionActivationCurrentGrantConflictError,
    OperationalPaperSessionActivationIdempotencyConflictError,
    OperationalPaperSessionActivationNotFoundError,
    OperationalPaperSessionActivationRecordVersionConflictError,
    OperationalPaperSessionActivationSpecification,
    OperationalPaperSessionActivationState,
    OperationalPaperSessionActivationStateTransitionConflictError,
    build_operational_paper_session_activation_specification,
    operational_paper_session_activation_create_intent_fingerprint,
)
from app.operational_paper_session_materializations import (
    OperationalPaperSessionMaterializationChecksumMismatchError,
    OperationalPaperSessionMaterializationNotFoundError,
    OperationalPaperSessionMaterializationProfileBindingConflictError,
    OperationalPaperSessionMaterializationQuoteAssetConflictError,
    OperationalPaperSessionMaterializationStateTransitionConflictError,
)
from app.operational_paper_session_profiles.errors import (
    OperationalPaperSessionProfileNotFoundError,
    OperationalPaperSessionProfileStateTransitionConflictError,
)
from app.repositories.operational_paper_session_activations import (
    PostgresOperationalPaperSessionActivationRepository,
    _raise_activation_database_error,
    operational_paper_session_activation_from_row,
)
from app.repositories.operational_paper_session_materializations import (
    PostgresOperationalPaperSessionMaterializationRepository,
)
from tests.test_operational_paper_session_activations_domain import (
    _aggregate as _domain_aggregate,
)
from tests.test_operational_paper_session_activations_migration import (
    AUTHORIZED_AT,
    _row,
)
from tests.test_operational_paper_session_materializations_repository import (
    PREPARED_AT,
    _plan_context,
)


async def _specification(
    database_url: str,
    database: Database,
    actor_id: UUID,
) -> OperationalPaperSessionActivationSpecification:
    plan = await _plan_context(database_url, database, actor_id)
    repository = PostgresOperationalPaperSessionMaterializationRepository(database)
    prepared = await repository.prepare(
        plan,
        actor_id=actor_id,
        now=PREPARED_AT,
    )
    materialized = await repository.mark_materialized(
        prepared.materialization_id,
        expected_record_version=1,
        actor_id=actor_id,
        now=AUTHORIZED_AT - timedelta(minutes=1),
    )
    return build_operational_paper_session_activation_specification(materialized)


async def _create(
    database_url: str,
    database: Database,
    actor_id: UUID,
    *,
    key: str,
    now: datetime = AUTHORIZED_AT,
) -> tuple[
    PostgresOperationalPaperSessionActivationRepository,
    OperationalPaperSessionActivationSpecification,
    OperationalPaperSessionActivation,
]:
    specification = await _specification(database_url, database, actor_id)
    repository = PostgresOperationalPaperSessionActivationRepository(database)
    activation = await repository.create(
        specification,
        actor_id=actor_id,
        idempotency_key=key,
        now=now,
    )
    return repository, specification, activation


def _stored_rows(database_url: str) -> list[dict[str, object]]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            "select * from public.operational_paper_session_activations"
        ).fetchall()
    return [dict(row) for row in rows]


@pytest.mark.parametrize("state", list(OperationalPaperSessionActivationState))
def test_strict_reconstruction_accepts_canonical_state_versions(
    state: OperationalPaperSessionActivationState,
) -> None:
    aggregate = _domain_aggregate()
    if state is OperationalPaperSessionActivationState.REVOKED:
        aggregate = replace(
            aggregate,
            state=state,
            record_version=2,
            revoked_by=uuid4(),
            revoked_at=aggregate.authorized_at + timedelta(seconds=1),
        )
    assert operational_paper_session_activation_from_row(_row(aggregate)) == aggregate


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("record_version", 2),
        ("activation_id", UUID(int=0)),
        ("materialization_checksum", "bad"),
        ("create_intent_fingerprint", "bad"),
        ("authorized_at", datetime(2026, 9, 3, 18)),
        ("revoked_by", uuid4()),
    ),
)
def test_strict_reconstruction_rejects_corrupt_persisted_rows(
    field: str,
    value: object,
) -> None:
    row = _row(_domain_aggregate()) | {field: value}
    with pytest.raises(PersistenceError):
        operational_paper_session_activation_from_row(row)


def test_strict_reconstruction_rejects_revoked_version_one() -> None:
    aggregate = _domain_aggregate()
    row = _row(aggregate) | {
        "state": "REVOKED",
        "record_version": 1,
        "revoked_by": uuid4(),
        "revoked_at": aggregate.authorized_at,
    }
    with pytest.raises(PersistenceError):
        operational_paper_session_activation_from_row(row)


@pytest.mark.asyncio
async def test_create_get_and_actor_lookup_round_trip_exact_internal_evidence(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    repository, specification, activation = await _create(
        database_url,
        database,
        auth_user_id,
        key="activation:round-trip",
    )
    expected_fingerprint = operational_paper_session_activation_create_intent_fingerprint(
        OperationalPaperSessionActivationCreateIntent(
            materialization_id=specification.materialization_id,
            materialization_checksum=specification.materialization_checksum,
        )
    )

    assert activation.state is OperationalPaperSessionActivationState.AUTHORIZED
    assert activation.record_version == 1
    assert activation.authorized_by == auth_user_id
    assert activation.create_intent_fingerprint == expected_fingerprint
    assert await repository.get(activation.activation_id) == activation
    assert await repository.get(uuid4()) is None
    assert (
        await repository.get_by_actor_idempotency(
            actor_id=auth_user_id,
            idempotency_key="activation:round-trip",
        )
        == activation
    )
    assert (
        await repository.get_by_actor_idempotency(
            actor_id=auth_user_id,
            idempotency_key="activation:absent",
        )
        is None
    )
    assert (
        await repository.get_current_for_materialization(specification.materialization_id)
        == activation
    )
    assert _stored_rows(database_url) == [_row(activation)]


@pytest.mark.asyncio
async def test_lookup_validation_rejects_invalid_inputs_before_database() -> None:
    class DatabaseMustNotBeAccessed:
        def transaction(self) -> object:
            raise AssertionError("invalid input reached PostgreSQL")

    repository = PostgresOperationalPaperSessionActivationRepository(DatabaseMustNotBeAccessed())  # type: ignore[arg-type]
    for operation in (
        repository.get(UUID(int=0)),
        repository.get_current_for_materialization(UUID(int=0)),
        repository.get_by_actor_idempotency(actor_id=UUID(int=0), idempotency_key="valid"),
        repository.get_by_actor_idempotency(actor_id=uuid4(), idempotency_key=" invalid"),
    ):
        with pytest.raises(InvalidOperationalPaperSessionActivationSpecificationError):
            await operation


@pytest.mark.asyncio
async def test_revoked_history_is_not_current_and_exact_replay_is_early(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    repository, specification, activation = await _create(
        database_url,
        database,
        auth_user_id,
        key="activation:historical-replay",
    )
    revoked = await repository.revoke(
        activation.activation_id,
        expected_record_version=1,
        actor_id=auth_user_id,
        now=AUTHORIZED_AT + timedelta(minutes=1),
    )
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("set session_replication_role = replica")
        connection.execute(
            "update public.simulation_runs set currency = 'EUR' where id = %s",
            (activation.simulation_id,),
        )
        connection.execute("set session_replication_role = origin")

    replay = await repository.create(
        specification,
        actor_id=auth_user_id,
        idempotency_key="activation:historical-replay",
        now=AUTHORIZED_AT + timedelta(hours=1),
    )

    assert replay == revoked
    assert replay.state is OperationalPaperSessionActivationState.REVOKED
    assert await repository.get_current_for_materialization(activation.materialization_id) is None
    assert len(_stored_rows(database_url)) == 1


@pytest.mark.asyncio
async def test_authorized_replay_and_divergent_idempotency_conflict(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    repository, specification, activation = await _create(
        database_url,
        database,
        auth_user_id,
        key="activation:authorized-replay",
    )
    replay = await repository.create(
        specification,
        actor_id=auth_user_id,
        idempotency_key="activation:authorized-replay",
        now=AUTHORIZED_AT + timedelta(hours=1),
    )
    assert replay == activation

    other = replace(specification, materialization_id=uuid4())
    with pytest.raises(OperationalPaperSessionActivationIdempotencyConflictError):
        await repository.create(
            other,
            actor_id=auth_user_id,
            idempotency_key="activation:authorized-replay",
            now=AUTHORIZED_AT + timedelta(hours=1),
        )
    assert len(_stored_rows(database_url)) == 1


@pytest.mark.asyncio
async def test_list_order_pagination_totals_and_combined_filters(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    repository, specification, first = await _create(
        database_url, database, auth_user_id, key="activation:list:1"
    )
    first_revoked = await repository.revoke(
        first.activation_id,
        expected_record_version=1,
        actor_id=auth_user_id,
        now=AUTHORIZED_AT + timedelta(seconds=1),
    )
    second = await repository.create(
        specification,
        actor_id=auth_user_id,
        idempotency_key="activation:list:2",
        now=AUTHORIZED_AT + timedelta(minutes=10),
    )

    page, total = await repository.list(limit=1, offset=0)
    empty, empty_total = await repository.list(limit=10, offset=20)
    revoked, revoked_total = await repository.list(
        limit=10,
        offset=0,
        state=OperationalPaperSessionActivationState.REVOKED,
    )
    combined, combined_total = await repository.list(
        limit=10,
        offset=0,
        state=OperationalPaperSessionActivationState.AUTHORIZED,
        materialization_id=second.materialization_id,
    )
    materialization_history, materialization_total = await repository.list(
        limit=10,
        offset=0,
        materialization_id=first.materialization_id,
    )

    assert page == [second]
    assert total == 2
    assert empty == [] and empty_total == 2
    assert [item.activation_id for item in revoked] == [first.activation_id]
    assert revoked_total == 1
    assert combined == [second] and combined_total == 1
    assert materialization_history == [second, first_revoked]
    assert materialization_total == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limit", "offset", "error"),
    (
        (0, 0, OperationalPaperSessionActivationBoundsExceededError),
        (101, 0, OperationalPaperSessionActivationBoundsExceededError),
        (1, -1, OperationalPaperSessionActivationBoundsExceededError),
        (True, 0, InvalidOperationalPaperSessionActivationSpecificationError),
    ),
)
async def test_list_validation(
    limit: int,
    offset: int,
    error: type[Exception],
) -> None:
    class DatabaseMustNotBeAccessed:
        def transaction(self) -> object:
            raise AssertionError("invalid input reached PostgreSQL")

    repository = PostgresOperationalPaperSessionActivationRepository(DatabaseMustNotBeAccessed())  # type: ignore[arg-type]
    with pytest.raises(error):
        await repository.list(limit=limit, offset=offset)
    with pytest.raises(InvalidOperationalPaperSessionActivationSpecificationError):
        await repository.list(limit=1, offset=0, materialization_id=UUID(int=0))


class _RaceCursor:
    def __init__(self, cursor: object, observation: _RaceDatabase, initial: bool) -> None:
        self._cursor = cursor
        self._observation = observation
        self._initial = initial

    async def fetchone(self) -> object:
        row = await self._cursor.fetchone()  # type: ignore[attr-defined]
        if self._initial and row is None:
            async with self._observation.arrival_lock:
                self._observation.arrivals += 1
                if self._observation.arrivals == 2:
                    self._observation.release.set()
            await self._observation.release.wait()
        return row

    def __getattr__(self, name: str) -> object:
        return getattr(self._cursor, name)


class _RaceConnection:
    def __init__(self, connection: object, observation: _RaceDatabase) -> None:
        self._connection = connection
        self._observation = observation
        self._actor_lookups = 0

    async def execute(self, query: object, params: object = None, **kwargs: object) -> object:
        cursor = await self._connection.execute(query, params, **kwargs)  # type: ignore[attr-defined]
        normalized = " ".join(str(query).lower().split())
        actor_lookup = (
            normalized.startswith("select")
            and "from public.operational_paper_session_activations" in normalized
            and "authorized_by = %s" in normalized
            and "create_idempotency_key = %s" in normalized
        )
        if actor_lookup:
            self._actor_lookups += 1
        return _RaceCursor(cursor, self._observation, actor_lookup and self._actor_lookups == 1)


class _RaceDatabase:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.arrival_lock = asyncio.Lock()
        self.release = asyncio.Event()
        self.arrivals = 0

    def transaction(self) -> AbstractAsyncContextManager[_RaceConnection]:
        @asynccontextmanager
        async def scope() -> AsyncIterator[_RaceConnection]:
            async with self.database.transaction() as connection:
                yield _RaceConnection(connection, self)

        return scope()


@pytest.mark.asyncio
async def test_real_concurrency_same_actor_key_and_intent_replays_one_row(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    specification = await _specification(database_url, database, auth_user_id)
    repository = PostgresOperationalPaperSessionActivationRepository(_RaceDatabase(database))  # type: ignore[arg-type]
    results = await asyncio.gather(
        *(
            repository.create(
                specification,
                actor_id=auth_user_id,
                idempotency_key="activation:race:same",
                now=AUTHORIZED_AT,
            )
            for _ in range(2)
        )
    )
    assert results[0].activation_id == results[1].activation_id
    assert len(_stored_rows(database_url)) == 1


@pytest.mark.asyncio
async def test_real_concurrency_same_materialization_different_keys_has_current_conflict(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    specification = await _specification(database_url, database, auth_user_id)
    repository = PostgresOperationalPaperSessionActivationRepository(_RaceDatabase(database))  # type: ignore[arg-type]
    results = await asyncio.gather(
        repository.create(
            specification,
            actor_id=auth_user_id,
            idempotency_key="activation:race:key-a",
            now=AUTHORIZED_AT,
        ),
        repository.create(
            specification,
            actor_id=auth_user_id,
            idempotency_key="activation:race:key-b",
            now=AUTHORIZED_AT,
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(item, OperationalPaperSessionActivation) for item in results) == 1
    assert (
        sum(
            isinstance(item, OperationalPaperSessionActivationCurrentGrantConflictError)
            for item in results
        )
        == 1
    )
    assert len(_stored_rows(database_url)) == 1


@pytest.mark.asyncio
async def test_deterministic_same_key_different_fingerprint_has_idempotency_conflict(
    database_url: str,
) -> None:
    winner = _domain_aggregate()
    fake = _RecoveryDatabase([_row(winner)])
    repository = PostgresOperationalPaperSessionActivationRepository(fake)  # type: ignore[arg-type]
    with pytest.raises(OperationalPaperSessionActivationIdempotencyConflictError):
        await repository._recover_create_error(
            original_error=_database_error(database_url, message="post-insert failure"),
            actor_id=winner.authorized_by,
            idempotency_key=winner.create_idempotency_key,
            fingerprint="f" * 64,
            materialization_id=uuid4(),
        )
    assert len(fake.connection.queries) == 1


class _RowsCursor:
    def __init__(self, row: Mapping[str, object] | None) -> None:
        self.row = row

    async def fetchone(self) -> Mapping[str, object] | None:
        return self.row


class _RecoveryConnection:
    def __init__(self, rows: list[Mapping[str, object] | None]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    async def execute(self, query: object, params: object = None) -> _RowsCursor:
        self.queries.append(" ".join(str(query).lower().split()))
        return _RowsCursor(self.rows.pop(0))


class _RecoveryDatabase:
    def __init__(self, rows: list[Mapping[str, object] | None]) -> None:
        self.connection = _RecoveryConnection(rows)

    def transaction(self) -> AbstractAsyncContextManager[_RecoveryConnection]:
        @asynccontextmanager
        async def scope() -> AsyncIterator[_RecoveryConnection]:
            yield self.connection

        return scope()


def _database_error(
    database_url: str,
    *,
    message: str,
    sqlstate: str = "55000",
    constraint: str | None = None,
) -> psycopg.Error:
    if constraint is None:
        statement = sql.SQL(
            "do $$ begin raise exception using errcode = {}, message = {}; end $$"
        ).format(sql.Literal(sqlstate), sql.Literal(message))
        with psycopg.connect(database_url, autocommit=True) as connection:
            with pytest.raises(psycopg.Error) as caught:
                connection.execute(statement)
        return caught.value

    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            sql.SQL("create table pg_temp.regression (value integer constraint {} unique)").format(
                sql.Identifier(constraint)
            )
        )
        connection.execute("insert into pg_temp.regression values (1)")
        with pytest.raises(psycopg.Error) as caught:
            connection.execute("insert into pg_temp.regression values (1)")
    return caught.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "constraint",
    (
        "op_ps_activation_actor_idempotency_key",
        "op_ps_activation_one_authorized_per_materialization_uidx",
    ),
)
async def test_constraint_name_cannot_override_fresh_lookup_absence(
    database_url: str,
    constraint: str,
) -> None:
    fake = _RecoveryDatabase([None, None])
    repository = PostgresOperationalPaperSessionActivationRepository(fake)  # type: ignore[arg-type]
    with pytest.raises(PersistenceError):
        await repository._recover_create_error(
            original_error=_database_error(
                database_url,
                message="duplicate",
                sqlstate="23505",
                constraint=constraint,
            ),
            actor_id=uuid4(),
            idempotency_key="activation:recovery",
            fingerprint="a" * 64,
            materialization_id=uuid4(),
        )
    assert len(fake.connection.queries) == 2


@pytest.mark.asyncio
async def test_non_unique_error_fresh_winner_replays_before_original_mapping(
    database_url: str,
) -> None:
    winner = _domain_aggregate()
    fake = _RecoveryDatabase([_row(winner)])
    repository = PostgresOperationalPaperSessionActivationRepository(fake)  # type: ignore[arg-type]
    replay = await repository._recover_create_error(
        original_error=_database_error(database_url, message="unrelated database failure"),
        actor_id=winner.authorized_by,
        idempotency_key=winner.create_idempotency_key,
        fingerprint=winner.create_intent_fingerprint,
        materialization_id=winner.materialization_id,
    )
    assert replay == winner
    assert len(fake.connection.queries) == 1


class _ObservedConnection:
    def __init__(self, connection: object, queries: list[str]) -> None:
        self.connection = connection
        self.queries = queries

    async def execute(self, query: object, params: object = None, **kwargs: object) -> object:
        self.queries.append(" ".join(str(query).lower().split()))
        return await self.connection.execute(query, params, **kwargs)  # type: ignore[attr-defined]


class _ObservedDatabase:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.queries: list[str] = []

    def transaction(self) -> AbstractAsyncContextManager[_ObservedConnection]:
        @asynccontextmanager
        async def scope() -> AsyncIterator[_ObservedConnection]:
            async with self.database.transaction() as connection:
                yield _ObservedConnection(connection, self.queries)

        return scope()


@pytest.mark.asyncio
async def test_create_has_no_preinsert_current_lookup_and_revoke_locks_only_activation(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    specification = await _specification(database_url, database, auth_user_id)
    observed = _ObservedDatabase(database)
    repository = PostgresOperationalPaperSessionActivationRepository(observed)  # type: ignore[arg-type]
    activation = await repository.create(
        specification,
        actor_id=auth_user_id,
        idempotency_key="activation:sql-audit",
        now=AUTHORIZED_AT,
    )
    insert_index = next(
        index for index, query in enumerate(observed.queries) if query.startswith("insert into")
    )
    before_insert = observed.queries[:insert_index]
    assert any("authorized_by = %s" in query for query in before_insert)
    assert not any("state = 'authorized'" in query for query in before_insert)

    observed.queries.clear()
    await repository.revoke(
        activation.activation_id,
        expected_record_version=1,
        actor_id=auth_user_id,
        now=AUTHORIZED_AT + timedelta(seconds=1),
    )
    locking = [query for query in observed.queries if "for update" in query]
    assert len(locking) == 1
    assert "operational_paper_session_activations" in locking[0]
    assert all(
        upstream not in locking[0]
        for upstream in (
            "simulation_runs",
            "operational_paper_capital_authorizations",
            "operational_paper_session_profiles",
            "operational_mandates",
            "operational_paper_session_materializations",
        )
    )
    update = next(query for query in observed.queries if query.startswith("update public"))
    assert "where activation_id = %s" in update
    assert "and state = 'authorized'" in update
    assert "and record_version = %s" in update


@pytest.mark.parametrize(
    ("message", "expected"),
    (
        ("operational_paper_session_activation_simulation_missing", SimulationNotFoundError),
        ("operational_paper_session_activation_simulation_not_active", SimulationTerminalError),
        (
            "operational_paper_session_activation_authorization_missing",
            OperationalPaperCapitalAuthorizationNotFoundError,
        ),
        (
            "operational_paper_session_activation_authorization_not_authorized",
            OperationalPaperCapitalAuthorizationStateTransitionConflictError,
        ),
        (
            "operational_paper_session_activation_authorization_checksum_mismatch",
            OperationalPaperCapitalAuthorizationChecksumMismatchError,
        ),
        (
            "operational_paper_session_activation_profile_missing",
            OperationalPaperSessionProfileNotFoundError,
        ),
        (
            "operational_paper_session_activation_profile_not_approved",
            OperationalPaperSessionProfileStateTransitionConflictError,
        ),
        ("operational_paper_session_activation_mandate_missing", OperationalMandateNotFoundError),
        (
            "operational_paper_session_activation_mandate_not_approved",
            OperationalMandateStateTransitionConflictError,
        ),
        (
            "operational_paper_session_activation_materialization_missing",
            OperationalPaperSessionMaterializationNotFoundError,
        ),
        (
            "operational_paper_session_activation_materialization_not_materialized",
            OperationalPaperSessionMaterializationStateTransitionConflictError,
        ),
        (
            "operational_paper_session_activation_materialization_checksum_mismatch",
            OperationalPaperSessionMaterializationChecksumMismatchError,
        ),
        (
            "operational_paper_session_activation_materialization_profile_binding_mismatch",
            PersistenceError,
        ),
        (
            "operational_paper_session_activation_currency_mismatch",
            OperationalPaperCapitalAuthorizationCurrencyMismatchError,
        ),
        (
            "operational_paper_session_activation_authorization_profile_binding_mismatch",
            OperationalPaperSessionMaterializationProfileBindingConflictError,
        ),
        (
            "operational_paper_session_activation_authorization_quote_asset_mismatch",
            OperationalPaperSessionMaterializationQuoteAssetConflictError,
        ),
    ),
)
def test_database_error_mapping_regressions(
    database_url: str,
    message: str,
    expected: type[Exception],
) -> None:
    with pytest.raises(expected):
        _raise_activation_database_error(
            _database_error(database_url, message=message, sqlstate="23514")
        )


@pytest.mark.asyncio
async def test_revoke_not_found_normal_stale_and_exact_retry(
    database_url: str,
    database: Database,
    auth_user_id: UUID,
) -> None:
    repository = PostgresOperationalPaperSessionActivationRepository(database)
    with pytest.raises(OperationalPaperSessionActivationNotFoundError):
        await repository.revoke(
            uuid4(), expected_record_version=1, actor_id=auth_user_id, now=AUTHORIZED_AT
        )

    repository, _, activation = await _create(
        database_url, database, auth_user_id, key="activation:revoke"
    )
    with pytest.raises(OperationalPaperSessionActivationRecordVersionConflictError):
        await repository.revoke(
            activation.activation_id,
            expected_record_version=2,
            actor_id=auth_user_id,
            now=AUTHORIZED_AT + timedelta(seconds=1),
        )
    revoked = await repository.revoke(
        activation.activation_id,
        expected_record_version=1,
        actor_id=auth_user_id,
        now=AUTHORIZED_AT + timedelta(seconds=1),
    )
    retry = await repository.revoke(
        activation.activation_id,
        expected_record_version=1,
        actor_id=auth_user_id,
        now=AUTHORIZED_AT + timedelta(minutes=1),
    )
    assert revoked.state is OperationalPaperSessionActivationState.REVOKED
    assert revoked.record_version == 2
    assert revoked.revoked_by == auth_user_id
    assert retry == revoked

    other_actor = uuid4()
    with pytest.raises(OperationalPaperSessionActivationStateTransitionConflictError):
        await repository.revoke(
            activation.activation_id,
            expected_record_version=1,
            actor_id=other_actor,
            now=AUTHORIZED_AT + timedelta(minutes=1),
        )
    with pytest.raises(OperationalPaperSessionActivationStateTransitionConflictError):
        await repository.revoke(
            activation.activation_id,
            expected_record_version=2,
            actor_id=auth_user_id,
            now=AUTHORIZED_AT + timedelta(minutes=1),
        )
