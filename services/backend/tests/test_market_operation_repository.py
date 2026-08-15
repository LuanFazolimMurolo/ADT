"""Phase 2D repository integration tests against disposable PostgreSQL."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from app.database import Database
from app.market_data.domain import DataRange, Exchange, MarketType, TradingPair
from app.market_data.errors import (
    InvalidMarketOperationRequestError,
    InvalidOperationLeaseError,
    InvalidOperationTransitionError,
    InvalidPersistedOperationError,
    MarketOperationTerminalError,
    OperationIdempotencyConflictError,
    OperationProgressRegressionError,
    OperationVersionConflictError,
)
from app.market_data.operation_ports import MarketOperationRepository
from app.market_data.operations import (
    MarketDatasetSelector,
    MarketOperationFailureCode,
    MarketOperationRecoveryClaim,
    MarketOperationRequest,
    MarketOperationSnapshot,
    MarketOperationState,
    MarketOperationType,
    OperationPlanSummary,
    OperationProgress,
    OperationResult,
    SanitizedOperationFailure,
    WorkerLease,
    encode_dataset_id,
    operation_request_fingerprint,
)
from app.market_data.timeframes import TIMEFRAMES, get_timeframe
from app.repositories.market_operation_repository import (
    PostgresMarketOperationRepository,
    failure_message_for,
    market_operation_from_row,
)
from tests.postgres_support import add_auth_user

BASE_TIME = datetime(2026, 7, 1, tzinfo=UTC)
PLAN_CHECKSUM = "a" * 64
DATASET_VERSION = "b" * 64
DATASET_CHECKSUM = "c" * 64


def _request(
    admin_user_id: UUID,
    *,
    idempotency_key: str = "phase-2d-test",
    symbol: str = "BTC/USDT",
    operation_type: MarketOperationType = MarketOperationType.RAW_BACKFILL,
    plan_checksum: str = PLAN_CHECKSUM,
) -> MarketOperationRequest:
    return MarketOperationRequest(
        operation_type=operation_type,
        dataset=MarketDatasetSelector(
            exchange=Exchange.BINANCE,
            market_type=MarketType.SPOT,
            pair=TradingPair.parse(symbol),
            timeframe=get_timeframe("1m"),
        ),
        data_range=DataRange(
            start=BASE_TIME,
            end=BASE_TIME + timedelta(minutes=10),
        ),
        plan_checksum=plan_checksum,
        idempotency_key=idempotency_key,
        requested_by=admin_user_id,
    )


def _plan(*, checksum: str = PLAN_CHECKSUM) -> OperationPlanSummary:
    return OperationPlanSummary(
        checksum=checksum,
        chunks_planned=2,
        estimated_candles=10,
        estimated_requests=2,
        created_at=BASE_TIME,
    )


async def _create(
    repository: PostgresMarketOperationRepository,
    admin_user_id: UUID,
    *,
    operation_id: UUID | None = None,
    idempotency_key: str = "phase-2d-test",
    symbol: str = "BTC/USDT",
    now: datetime = BASE_TIME,
) -> MarketOperationSnapshot:
    return await repository.create_idempotently(
        operation_id=operation_id or uuid4(),
        request=_request(
            admin_user_id,
            idempotency_key=idempotency_key,
            symbol=symbol,
        ),
        plan=_plan(),
        now=now,
    )


async def _claim(
    repository: PostgresMarketOperationRepository,
    *,
    now: datetime = BASE_TIME + timedelta(seconds=1),
    duration: timedelta = timedelta(seconds=30),
    owner_id: UUID | None = None,
) -> MarketOperationSnapshot:
    claimed = await repository.claim_next(
        owner_id=owner_id or uuid4(),
        now=now,
        lease_expires_at=now + duration,
    )
    assert claimed is not None
    return claimed


async def _leased_operation(
    repository: PostgresMarketOperationRepository,
    admin_user_id: UUID,
    *,
    state: MarketOperationState,
    duration: timedelta = timedelta(seconds=1),
    operation_id: UUID | None = None,
    idempotency_key: str = "phase-2d-recovery",
    symbol: str = "BTC/USDT",
    owner_id: UUID | None = None,
    local_job_id: str = "recovery-job",
) -> MarketOperationSnapshot:
    await _create(
        repository,
        admin_user_id,
        operation_id=operation_id,
        idempotency_key=idempotency_key,
        symbol=symbol,
    )
    operation = await _claim(
        repository,
        duration=duration,
        owner_id=owner_id,
    )
    if state is MarketOperationState.RUNNING:
        operation = await repository.request_state(
            operation_id=operation.operation_id,
            target=state,
            expected_version=operation.record_version,
            now=BASE_TIME + timedelta(milliseconds=1250),
            owner_id=_owner(operation),
        )
        operation = await repository.update_progress(
            operation_id=operation.operation_id,
            owner_id=_owner(operation),
            now=BASE_TIME + timedelta(milliseconds=1500),
            progress=_progress(
                operation,
                now=BASE_TIME + timedelta(milliseconds=1500),
            ),
            local_job_id=local_job_id,
            expected_version=operation.record_version,
        )
    elif state is MarketOperationState.PAUSE_REQUESTED:
        operation = await repository.request_pause(
            operation_id=operation.operation_id,
            expected_version=operation.record_version,
            now=BASE_TIME + timedelta(milliseconds=1250),
        )
    elif state is MarketOperationState.CANCEL_REQUESTED:
        operation = await repository.request_cancel(
            operation_id=operation.operation_id,
            expected_version=operation.record_version,
            now=BASE_TIME + timedelta(milliseconds=1250),
        )
    else:
        assert state is MarketOperationState.CLAIMED
    return operation


async def _claim_recovery(
    repository: PostgresMarketOperationRepository,
    *,
    owner_id: UUID | None = None,
    now: datetime = BASE_TIME + timedelta(seconds=3),
    duration: timedelta = timedelta(seconds=30),
) -> MarketOperationRecoveryClaim:
    claim = await repository.claim_next_expired(
        owner_id=owner_id or uuid4(),
        now=now,
        lease_expires_at=now + duration,
    )
    assert claim is not None
    return claim


async def _leave_recovering_unleased(
    database: Database,
    repository: PostgresMarketOperationRepository,
    operation: MarketOperationSnapshot,
    *,
    now: datetime,
) -> MarketOperationSnapshot:
    """Emulate one legacy/crash-era RECOVERING row with no assigned lease."""
    async with database.transaction() as connection:
        await connection.execute(
            """
            update public.market_data_operations
            set status = 'RECOVERING',
                lease_owner = null,
                lease_claimed_at = null,
                lease_heartbeat_at = null,
                lease_expires_at = null,
                updated_at = %s,
                version = version + 1
            where id = %s and version = %s
            """,
            (now, operation.operation_id, operation.record_version),
        )
    recovering = await repository.get(operation.operation_id)
    assert recovering is not None
    assert recovering.state is MarketOperationState.RECOVERING
    assert recovering.lease is None
    return recovering


def _owner(operation: MarketOperationSnapshot) -> UUID:
    assert operation.lease is not None
    return operation.lease.owner_id


def _progress(
    operation: MarketOperationSnapshot,
    *,
    now: datetime,
    chunks_completed: int = 1,
    chunks_failed: int = 0,
    candles_received: int = 5,
    candles_persisted: int = 5,
    requests_completed: int = 1,
) -> OperationProgress:
    return OperationProgress(
        chunks_planned=operation.plan.chunks_planned,
        chunks_completed=chunks_completed,
        chunks_failed=chunks_failed,
        candles_estimated=operation.plan.estimated_candles,
        candles_received=candles_received,
        candles_persisted=candles_persisted,
        requests_completed=requests_completed,
        updated_at=now,
    )


async def _category_b_control(
    repository: PostgresMarketOperationRepository,
    admin_user_id: UUID,
    *,
    requested_state: MarketOperationState,
    operation_id: UUID | None = None,
    idempotency_key: str = "phase-2d-recovery",
    symbol: str = "BTC/USDT",
) -> MarketOperationSnapshot:
    resolved_operation_id = operation_id or uuid4()
    running = await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.RUNNING,
        duration=timedelta(seconds=30),
        operation_id=resolved_operation_id,
        idempotency_key=idempotency_key,
        symbol=symbol,
        local_job_id=str(resolved_operation_id),
    )
    pause_requested = await repository.request_pause(
        operation_id=running.operation_id,
        expected_version=running.record_version,
        now=BASE_TIME + timedelta(seconds=2),
    )
    paused = await repository.request_state(
        operation_id=running.operation_id,
        target=MarketOperationState.PAUSED,
        expected_version=pause_requested.record_version,
        now=BASE_TIME + timedelta(seconds=3),
        owner_id=_owner(pause_requested),
    )
    pending = await repository.resume(
        operation_id=paused.operation_id,
        expected_version=paused.record_version,
        now=BASE_TIME + timedelta(seconds=4),
    )
    return await repository.request_state(
        operation_id=pending.operation_id,
        target=requested_state,
        expected_version=pending.record_version,
        now=BASE_TIME + timedelta(seconds=5),
    )


async def _settle_or_claim_control(
    repository: PostgresMarketOperationRepository,
    *,
    now: datetime,
    owner_id: UUID | None = None,
    lease_duration: timedelta = timedelta(seconds=30),
) -> MarketOperationSnapshot | None:
    return await repository.settle_or_claim_next_unclaimed_control(
        owner_id=owner_id or uuid4(),
        now=now,
        lease_expires_at=now + lease_duration,
    )


def _add_admin(database_url: str) -> UUID:
    user_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, user_id)
        connection.execute(
            "insert into public.app_admins (user_id, created_by) values (%s, %s)",
            (user_id, user_id),
        )
    return user_id


def _valid_operation_row(admin_user_id: UUID) -> DictRow:
    operation_id = uuid4()
    request = _request(admin_user_id)
    local_timezone = timezone(timedelta(hours=-3))
    local_time = BASE_TIME.astimezone(local_timezone)
    return cast(
        DictRow,
        {
            "id": operation_id,
            "operation_type": request.operation_type.value,
            "exchange": request.dataset.exchange.value,
            "market": request.dataset.market_type.value,
            "symbol": request.dataset.pair.symbol,
            "timeframe": request.dataset.timeframe.code,
            "dataset_id": encode_dataset_id(request.dataset),
            "range_start": request.data_range.start.astimezone(local_timezone),
            "range_end": request.data_range.end.astimezone(local_timezone),
            "plan_checksum": request.plan_checksum,
            "request_fingerprint": operation_request_fingerprint(request),
            "idempotency_key": request.idempotency_key,
            "requested_by": admin_user_id,
            "contract_version": request.contract_version,
            "status": MarketOperationState.PENDING.value,
            "local_job_id": None,
            "chunks_planned": 2,
            "chunks_completed": 0,
            "chunks_failed": 0,
            "candles_estimated": 10,
            "candles_received": 0,
            "candles_persisted": 0,
            "requests_estimated": 2,
            "requests_completed": 0,
            "progress_updated_at": local_time,
            "lease_owner": None,
            "lease_claimed_at": None,
            "lease_heartbeat_at": None,
            "lease_expires_at": None,
            "result_dataset_version": None,
            "result_dataset_checksum": None,
            "failure_code": None,
            "failure_message": None,
            "version": 1,
            "plan_created_at": local_time,
            "created_at": local_time,
            "updated_at": local_time,
            "started_at": None,
            "finished_at": None,
        },
    )


def test_phase2d_schema_indexes_rls_and_privileges(database_url: str) -> None:
    """The migration creates the backend-only catalog and its queue indexes."""
    expected_columns = {
        "id": "uuid",
        "operation_type": "text",
        "dataset_id": "text",
        "range_start": "timestamp with time zone",
        "range_end": "timestamp with time zone",
        "requested_by": "uuid",
        "lease_owner": "uuid",
        "version": "bigint",
        "created_at": "timestamp with time zone",
        "finished_at": "timestamp with time zone",
    }
    with psycopg.connect(database_url, autocommit=True) as connection:
        columns: dict[str, str] = dict(
            connection.execute(
                """
                select column_name, data_type
                from information_schema.columns
                where table_schema = 'public'
                  and table_name = 'market_data_operations'
                """
            ).fetchall()
        )
        assert {name: columns[name] for name in expected_columns} == expected_columns

        relation = connection.execute(
            """
            select relrowsecurity, relforcerowsecurity
            from pg_catalog.pg_class
            where oid = 'public.market_data_operations'::regclass
            """
        ).fetchone()
        assert relation == (True, False)

        policies = connection.execute(
            """
            select count(*)
            from pg_catalog.pg_policy
            where polrelid = 'public.market_data_operations'::regclass
            """
        ).fetchone()
        assert policies == (0,)

        indexes = {
            row[0]
            for row in connection.execute(
                """
                select indexname
                from pg_catalog.pg_indexes
                where schemaname = 'public'
                  and tablename = 'market_data_operations'
                """
            ).fetchall()
        }
        assert {
            "market_data_operations_pkey",
            "market_data_operations_admin_idempotency_key_key",
            "market_data_operations_claim_idx",
            "market_data_operations_dataset_idx",
            "market_data_operations_requested_by_idx",
            "market_data_operations_active_idx",
            "market_data_operations_expired_lease_idx",
            "market_data_operations_created_at_idx",
            "market_data_operations_one_active_dataset_uidx",
            "market_data_operations_one_active_owner_uidx",
        } <= indexes

        foreign_key = connection.execute(
            """
            select confrelid::regclass::text
            from pg_catalog.pg_constraint
            where conname = 'market_data_operations_requested_by_fkey'
            """
        ).fetchone()
        assert foreign_key == ("auth.users",)

        identity_constraint = connection.execute(
            """
            select pg_get_constraintdef(oid)
            from pg_catalog.pg_constraint
            where conrelid = 'public.market_data_operations'::regclass
              and conname = 'market_data_operations_identity_check'
            """
        ).fetchone()
        assert identity_constraint is not None
        identity_definition = identity_constraint[0]
        assert isinstance(identity_definition, str)
        for timeframe_code in TIMEFRAMES:
            assert f"'{timeframe_code}'" in identity_definition

        for role in ("anon", "authenticated", "service_role"):
            privileges = connection.execute(
                """
                select
                    has_table_privilege(%s, 'public.market_data_operations', 'SELECT'),
                    has_table_privilege(%s, 'public.market_data_operations', 'INSERT'),
                    has_table_privilege(%s, 'public.market_data_operations', 'UPDATE'),
                    has_table_privilege(%s, 'public.market_data_operations', 'DELETE')
                """,
                (role, role, role, role),
            ).fetchone()
            assert privileges == (False, False, False, False)

        assert connection.execute(
            "select has_table_privilege(current_user, "
            "'public.market_data_operations', 'SELECT,INSERT,UPDATE,DELETE')"
        ).fetchone() == (True,)


def test_repository_matches_the_existing_operation_port(database: Database) -> None:
    """Static typing verifies compatibility; runtime keeps the port infrastructure-neutral."""
    repository: MarketOperationRepository = PostgresMarketOperationRepository(database)
    assert repository is not None


@pytest.mark.parametrize("role", ["anon", "authenticated", "service_role"])
def test_data_api_roles_cannot_read_or_write_operations(
    database_url: str,
    role: str,
) -> None:
    """RLS and grants leave no direct PostgREST table surface."""
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"set role {role}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("select count(*) from public.market_data_operations")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("delete from public.market_data_operations where false")


async def test_creation_round_trip_and_filtered_listing(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    created = await _create(repository, admin_user_id)

    assert created.state is MarketOperationState.PENDING
    assert created.record_version == 1
    assert created.request == _request(admin_user_id)
    assert created.plan == _plan()
    assert created.progress == _progress(
        created,
        now=BASE_TIME,
        chunks_completed=0,
        candles_received=0,
        candles_persisted=0,
        requests_completed=0,
    )
    assert await repository.get(created.operation_id) == created
    assert await repository.list(
        limit=10,
        offset=0,
        state=MarketOperationState.PENDING,
        requested_by=admin_user_id,
        dataset_id=encode_dataset_id(created.request.dataset),
    ) == (created,)
    assert (
        await repository.list(
            limit=10,
            offset=0,
            state=MarketOperationState.COMPLETED,
        )
        == ()
    )


async def test_create_requires_current_admin_membership(
    database: Database,
    auth_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    with pytest.raises(InvalidMarketOperationRequestError):
        await _create(repository, auth_user_id)


async def test_same_admin_idempotency_retry_and_fingerprint_conflict(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    first = await _create(repository, admin_user_id)
    retry = await _create(repository, admin_user_id, operation_id=uuid4())
    assert retry == first

    divergent_request = _request(
        admin_user_id,
        operation_type=MarketOperationType.RAW_INCREMENTAL_UPDATE,
    )
    with pytest.raises(OperationIdempotencyConflictError):
        await repository.create_idempotently(
            operation_id=uuid4(),
            request=divergent_request,
            plan=_plan(),
            now=BASE_TIME,
        )


async def test_idempotency_key_is_scoped_per_administrator(
    database: Database,
    database_url: str,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    other_admin = _add_admin(database_url)
    first = await _create(repository, admin_user_id)
    second = await _create(repository, other_admin)
    assert first.operation_id != second.operation_id
    assert first.request.idempotency_key == second.request.idempotency_key


async def test_concurrent_same_key_creates_exactly_one_operation(
    database: Database,
    database_url: str,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    first_id, second_id = uuid4(), uuid4()
    first, second = await asyncio.gather(
        _create(repository, admin_user_id, operation_id=first_id),
        _create(repository, admin_user_id, operation_id=second_id),
    )
    assert first.operation_id == second.operation_id
    assert first.operation_id in {first_id, second_id}
    with psycopg.connect(database_url, autocommit=True) as connection:
        count = connection.execute(
            """
            select count(*)
            from public.market_data_operations
            where requested_by = %s and idempotency_key = %s
            """,
            (admin_user_id, "phase-2d-test"),
        ).fetchone()
    assert count == (1,)


async def test_claim_is_oldest_first_and_persists_atomic_lease(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    older = await _create(
        repository,
        admin_user_id,
        idempotency_key="older",
        now=BASE_TIME,
    )
    await _create(
        repository,
        admin_user_id,
        idempotency_key="newer",
        symbol="ETH/USDT",
        now=BASE_TIME + timedelta(milliseconds=1),
    )
    owner_id = uuid4()
    claimed = await _claim(repository, owner_id=owner_id)

    assert claimed.operation_id == older.operation_id
    assert claimed.state is MarketOperationState.CLAIMED
    assert claimed.record_version == 2
    assert claimed.started_at == BASE_TIME + timedelta(seconds=1)
    assert claimed.lease == WorkerLease(
        operation_id=older.operation_id,
        owner_id=owner_id,
        claimed_at=BASE_TIME + timedelta(seconds=1),
        heartbeat_at=BASE_TIME + timedelta(seconds=1),
        lease_expires_at=BASE_TIME + timedelta(seconds=31),
    )


async def test_concurrent_claims_never_return_the_same_operation(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    created = await _create(repository, admin_user_id)
    claims = await asyncio.gather(
        repository.claim_next(
            owner_id=uuid4(),
            now=BASE_TIME + timedelta(seconds=1),
            lease_expires_at=BASE_TIME + timedelta(seconds=31),
        ),
        repository.claim_next(
            owner_id=uuid4(),
            now=BASE_TIME + timedelta(seconds=1),
            lease_expires_at=BASE_TIME + timedelta(seconds=31),
        ),
    )
    claimed = [operation for operation in claims if operation is not None]
    assert [operation.operation_id for operation in claimed] == [created.operation_id]


async def test_active_dataset_blocks_a_second_claim(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    await _create(repository, admin_user_id, idempotency_key="same-dataset-1")
    await _create(repository, admin_user_id, idempotency_key="same-dataset-2")
    first = await _claim(repository)
    assert first.state is MarketOperationState.CLAIMED
    assert (
        await repository.claim_next(
            owner_id=uuid4(),
            now=BASE_TIME + timedelta(seconds=2),
            lease_expires_at=BASE_TIME + timedelta(seconds=32),
        )
        is None
    )


async def test_one_worker_owner_cannot_hold_two_active_operations(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    await _create(repository, admin_user_id, idempotency_key="owner-1")
    await _create(
        repository,
        admin_user_id,
        idempotency_key="owner-2",
        symbol="ETH/USDT",
    )
    owner_id = uuid4()
    first = await _claim(repository, owner_id=owner_id)
    assert first.lease is not None and first.lease.owner_id == owner_id
    assert (
        await repository.claim_next(
            owner_id=owner_id,
            now=BASE_TIME + timedelta(seconds=2),
            lease_expires_at=BASE_TIME + timedelta(seconds=32),
        )
        is None
    )
    second = await repository.claim_next(
        owner_id=uuid4(),
        now=BASE_TIME + timedelta(seconds=2),
        lease_expires_at=BASE_TIME + timedelta(seconds=32),
    )
    assert second is not None
    assert second.operation_id != first.operation_id


async def test_ineligible_operation_is_not_claimed(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    created = await _create(repository, admin_user_id)
    paused_request = await repository.request_pause(
        operation_id=created.operation_id,
        expected_version=created.record_version,
        now=BASE_TIME + timedelta(seconds=1),
    )
    assert paused_request.state is MarketOperationState.PAUSE_REQUESTED
    assert (
        await repository.claim_next(
            owner_id=uuid4(),
            now=BASE_TIME + timedelta(seconds=2),
            lease_expires_at=BASE_TIME + timedelta(seconds=32),
        )
        is None
    )


@pytest.mark.parametrize(
    ("requested_state", "settled_state"),
    [
        (MarketOperationState.PAUSE_REQUESTED, MarketOperationState.PAUSED),
        (MarketOperationState.CANCEL_REQUESTED, MarketOperationState.CANCELLED),
    ],
)
async def test_never_started_control_is_settled_without_execution_evidence(
    database: Database,
    admin_user_id: UUID,
    requested_state: MarketOperationState,
    settled_state: MarketOperationState,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    created = await _create(repository, admin_user_id)
    requested = await repository.request_state(
        operation_id=created.operation_id,
        target=requested_state,
        expected_version=created.record_version,
        now=BASE_TIME + timedelta(seconds=1),
    )
    settlement_time = BASE_TIME + timedelta(seconds=2)

    settled = await _settle_or_claim_control(repository, now=settlement_time)

    assert settled is not None
    assert settled.operation_id == requested.operation_id
    assert settled.state is settled_state
    assert settled.record_version == requested.record_version + 1 == created.record_version + 2
    assert settled.updated_at == settlement_time
    assert settled.lease is None
    assert settled.started_at is None
    assert settled.local_job_id is None
    assert settled.progress == requested.progress
    assert settled.result is None
    if settled_state is MarketOperationState.PAUSED:
        assert settled.failure is None
        assert settled.finished_at is None
    else:
        assert settled.failure == SanitizedOperationFailure(
            code=MarketOperationFailureCode.CANCELLED_BY_ADMIN,
            failed_at=settlement_time,
        )
        assert settled.finished_at == settlement_time

    assert (
        await _settle_or_claim_control(
            repository,
            now=settlement_time + timedelta(seconds=1),
        )
        is None
    )
    assert await repository.get(settled.operation_id) == settled


async def test_unclaimed_control_settlement_is_oldest_first_with_stable_id_tiebreak(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    first = await _create(
        repository,
        admin_user_id,
        operation_id=UUID(int=1),
        idempotency_key="phase7-c2-order-first",
    )
    second = await _create(
        repository,
        admin_user_id,
        operation_id=UUID(int=2),
        idempotency_key="phase7-c2-order-second",
    )
    for operation in (second, first):
        await repository.request_pause(
            operation_id=operation.operation_id,
            expected_version=operation.record_version,
            now=BASE_TIME + timedelta(seconds=1),
        )

    first_settled = await _settle_or_claim_control(
        repository,
        now=BASE_TIME + timedelta(seconds=2),
    )
    second_settled = await _settle_or_claim_control(
        repository,
        now=BASE_TIME + timedelta(seconds=3),
    )

    assert first_settled is not None
    assert second_settled is not None
    assert (first_settled.operation_id, second_settled.operation_id) == (
        first.operation_id,
        second.operation_id,
    )


async def test_concurrent_workers_settle_one_control_at_most_once(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    created = await _create(repository, admin_user_id)
    requested = await repository.request_cancel(
        operation_id=created.operation_id,
        expected_version=created.record_version,
        now=BASE_TIME + timedelta(seconds=1),
    )
    settlement_time = BASE_TIME + timedelta(seconds=2)

    results = await asyncio.gather(
        _settle_or_claim_control(repository, now=settlement_time),
        _settle_or_claim_control(repository, now=settlement_time),
    )

    settled = [result for result in results if result is not None]
    assert len(settled) == 1
    assert settled[0].state is MarketOperationState.CANCELLED
    assert settled[0].record_version == requested.record_version + 1
    assert settled[0].lease is None
    stored = await repository.get(created.operation_id)
    assert stored == settled[0]


@pytest.mark.parametrize(
    "requested_state",
    [MarketOperationState.PAUSE_REQUESTED, MarketOperationState.CANCEL_REQUESTED],
)
async def test_previously_started_unleased_control_gets_settlement_ownership(
    database: Database,
    admin_user_id: UUID,
    requested_state: MarketOperationState,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    requested = await _category_b_control(
        repository,
        admin_user_id,
        requested_state=requested_state,
    )
    owner_id = uuid4()

    claimed = await repository.settle_or_claim_next_unclaimed_control(
        owner_id=owner_id,
        now=BASE_TIME + timedelta(seconds=6),
        lease_expires_at=BASE_TIME + timedelta(seconds=36),
    )

    assert requested.lease is None
    assert requested.started_at is not None
    assert requested.local_job_id == str(requested.operation_id)
    assert claimed is not None
    assert claimed.state is requested_state
    assert claimed.lease is not None
    assert claimed.lease.owner_id == owner_id
    assert claimed.started_at == requested.started_at
    assert claimed.local_job_id == requested.local_job_id
    assert claimed.progress == requested.progress
    assert claimed.record_version == requested.record_version + 1
    assert claimed.result is None
    assert claimed.failure is None
    assert claimed.finished_at is None
    assert (
        await repository.settle_or_claim_next_unclaimed_control(
            owner_id=uuid4(),
            now=BASE_TIME + timedelta(seconds=7),
            lease_expires_at=BASE_TIME + timedelta(seconds=37),
        )
        is None
    )


async def test_stale_admin_version_cannot_race_unclaimed_control_settlement(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    created = await _create(repository, admin_user_id)
    requested = await repository.request_pause(
        operation_id=created.operation_id,
        expected_version=created.record_version,
        now=BASE_TIME + timedelta(seconds=1),
    )

    with pytest.raises(OperationVersionConflictError):
        await repository.request_pause(
            operation_id=created.operation_id,
            expected_version=created.record_version,
            now=BASE_TIME + timedelta(seconds=2),
        )

    assert await repository.get(created.operation_id) == requested
    settled = await _settle_or_claim_control(
        repository,
        now=BASE_TIME + timedelta(seconds=3),
    )
    assert settled is not None
    assert settled.state is MarketOperationState.PAUSED
    assert settled.record_version == requested.record_version + 1


async def test_concurrent_category_b_claims_grant_exactly_one_settlement_lease(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    requested = await _category_b_control(
        repository,
        admin_user_id,
        requested_state=MarketOperationState.CANCEL_REQUESTED,
    )
    owners = (uuid4(), uuid4())

    results = await asyncio.gather(
        *(
            repository.settle_or_claim_next_unclaimed_control(
                owner_id=owner_id,
                now=BASE_TIME + timedelta(seconds=6),
                lease_expires_at=BASE_TIME + timedelta(seconds=36),
            )
            for owner_id in owners
        )
    )

    claimed = [result for result in results if result is not None]
    assert len(claimed) == 1
    assert claimed[0].lease is not None
    assert claimed[0].lease.owner_id in owners
    assert claimed[0].record_version == requested.record_version + 1
    stored = await repository.get(requested.operation_id)
    assert stored == claimed[0]


async def test_category_b_claim_excludes_noncanonical_local_job_reference(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    running = await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.RUNNING,
        duration=timedelta(seconds=30),
    )
    pause_requested = await repository.request_pause(
        operation_id=running.operation_id,
        expected_version=running.record_version,
        now=BASE_TIME + timedelta(seconds=2),
    )
    paused = await repository.request_state(
        operation_id=running.operation_id,
        target=MarketOperationState.PAUSED,
        expected_version=pause_requested.record_version,
        now=BASE_TIME + timedelta(seconds=3),
        owner_id=_owner(pause_requested),
    )
    pending = await repository.resume(
        operation_id=paused.operation_id,
        expected_version=paused.record_version,
        now=BASE_TIME + timedelta(seconds=4),
    )
    requested = await repository.request_cancel(
        operation_id=pending.operation_id,
        expected_version=pending.record_version,
        now=BASE_TIME + timedelta(seconds=5),
    )

    assert requested.local_job_id == "recovery-job"
    assert (
        await repository.settle_or_claim_next_unclaimed_control(
            owner_id=uuid4(),
            now=BASE_TIME + timedelta(seconds=6),
            lease_expires_at=BASE_TIME + timedelta(seconds=36),
        )
        is None
    )
    assert await repository.get(requested.operation_id) == requested


@pytest.mark.parametrize(
    "requested_state",
    [MarketOperationState.PAUSE_REQUESTED, MarketOperationState.CANCEL_REQUESTED],
)
async def test_expired_category_b_settlement_lease_enters_existing_c1_recovery(
    database: Database,
    admin_user_id: UUID,
    requested_state: MarketOperationState,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    requested = await _category_b_control(
        repository,
        admin_user_id,
        requested_state=requested_state,
    )
    claimed = await repository.settle_or_claim_next_unclaimed_control(
        owner_id=uuid4(),
        now=BASE_TIME + timedelta(seconds=6),
        lease_expires_at=BASE_TIME + timedelta(seconds=7),
    )
    assert claimed is not None

    recovery = await repository.claim_next_expired(
        owner_id=uuid4(),
        now=BASE_TIME + timedelta(seconds=8),
        lease_expires_at=BASE_TIME + timedelta(seconds=38),
    )

    assert recovery is not None
    assert recovery.recovered_from is requested_state
    assert recovery.operation.state is MarketOperationState.RECOVERING
    assert recovery.operation.lease is not None
    assert recovery.operation.started_at == requested.started_at
    assert recovery.operation.local_job_id == requested.local_job_id
    assert recovery.operation.progress == requested.progress
    assert recovery.operation.record_version == claimed.record_version + 2


async def test_category_b_settlement_claim_is_oldest_first_with_stable_id_tiebreak(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    first = await _category_b_control(
        repository,
        admin_user_id,
        requested_state=MarketOperationState.PAUSE_REQUESTED,
        operation_id=UUID(int=1),
        idempotency_key="phase7-c2b-order-first",
        symbol="BTC/USDT",
    )
    second = await _category_b_control(
        repository,
        admin_user_id,
        requested_state=MarketOperationState.CANCEL_REQUESTED,
        operation_id=UUID(int=2),
        idempotency_key="phase7-c2b-order-second",
        symbol="ETH/USDT",
    )

    claimed = await repository.settle_or_claim_next_unclaimed_control(
        owner_id=uuid4(),
        now=BASE_TIME + timedelta(seconds=6),
        lease_expires_at=BASE_TIME + timedelta(seconds=36),
    )

    assert claimed is not None
    assert claimed.operation_id == first.operation_id
    assert claimed.operation_id != second.operation_id


async def test_global_control_order_prevents_new_category_a_from_starving_category_b(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    older_category_b = await _category_b_control(
        repository,
        admin_user_id,
        requested_state=MarketOperationState.PAUSE_REQUESTED,
        idempotency_key="phase7-c2-global-order-b",
        symbol="BTC/USDT",
    )
    newer_category_a = await _create(
        repository,
        admin_user_id,
        idempotency_key="phase7-c2-global-order-a",
        symbol="ETH/USDT",
        now=BASE_TIME + timedelta(seconds=1),
    )
    await repository.request_cancel(
        operation_id=newer_category_a.operation_id,
        expected_version=newer_category_a.record_version,
        now=BASE_TIME + timedelta(seconds=6),
    )

    selected = await _settle_or_claim_control(
        repository,
        now=BASE_TIME + timedelta(seconds=7),
    )

    assert selected is not None
    assert selected.operation_id == older_category_b.operation_id
    assert selected.state is MarketOperationState.PAUSE_REQUESTED
    assert selected.lease is not None


async def test_normal_pending_claim_remains_available_after_control_settlement(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    controlled = await _create(
        repository,
        admin_user_id,
        idempotency_key="phase7-c2-controlled",
    )
    pending = await _create(
        repository,
        admin_user_id,
        idempotency_key="phase7-c2-pending",
        symbol="ETH/USDT",
        now=BASE_TIME + timedelta(seconds=1),
    )
    await repository.request_cancel(
        operation_id=controlled.operation_id,
        expected_version=controlled.record_version,
        now=BASE_TIME + timedelta(seconds=2),
    )

    settled = await _settle_or_claim_control(
        repository,
        now=BASE_TIME + timedelta(seconds=3),
    )
    claimed = await repository.claim_next(
        owner_id=uuid4(),
        now=BASE_TIME + timedelta(seconds=4),
        lease_expires_at=BASE_TIME + timedelta(seconds=34),
    )

    assert settled is not None
    assert settled.operation_id == controlled.operation_id
    assert claimed is not None
    assert claimed.operation_id == pending.operation_id
    assert claimed.state is MarketOperationState.CLAIMED


async def test_lease_renewal_rejects_wrong_owner_expiry_and_regression(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    await _create(repository, admin_user_id)
    claimed = await _claim(repository)
    assert claimed.lease is not None
    renewed_lease = WorkerLease(
        operation_id=claimed.operation_id,
        owner_id=_owner(claimed),
        claimed_at=claimed.lease.claimed_at,
        heartbeat_at=BASE_TIME + timedelta(seconds=2),
        lease_expires_at=BASE_TIME + timedelta(seconds=40),
    )
    renewed = await repository.renew_lease(
        operation_id=claimed.operation_id,
        owner_id=renewed_lease.owner_id,
        now=renewed_lease.heartbeat_at,
        lease=renewed_lease,
        expected_version=claimed.record_version,
    )
    assert renewed.lease == renewed_lease
    assert renewed.record_version == claimed.record_version + 1

    for invalid_lease in (
        replace(
            renewed_lease,
            owner_id=uuid4(),
            heartbeat_at=BASE_TIME + timedelta(seconds=3),
            lease_expires_at=BASE_TIME + timedelta(seconds=50),
        ),
        replace(
            renewed_lease,
            heartbeat_at=BASE_TIME + timedelta(seconds=1),
            lease_expires_at=BASE_TIME + timedelta(seconds=50),
        ),
    ):
        with pytest.raises(InvalidOperationLeaseError):
            await repository.renew_lease(
                operation_id=claimed.operation_id,
                owner_id=invalid_lease.owner_id,
                now=invalid_lease.heartbeat_at,
                lease=invalid_lease,
                expected_version=renewed.record_version,
            )

    expired = await _create(
        repository,
        admin_user_id,
        idempotency_key="expired-renewal",
        symbol="ETH/USDT",
    )
    expired_claim = await _claim(repository, duration=timedelta(seconds=1))
    assert expired_claim.operation_id == expired.operation_id
    assert expired_claim.lease is not None
    with pytest.raises(InvalidOperationLeaseError):
        await repository.renew_lease(
            operation_id=expired.operation_id,
            owner_id=_owner(expired_claim),
            now=BASE_TIME + timedelta(seconds=3),
            lease=WorkerLease(
                operation_id=expired.operation_id,
                owner_id=_owner(expired_claim),
                claimed_at=expired_claim.lease.claimed_at,
                heartbeat_at=BASE_TIME + timedelta(seconds=3),
                lease_expires_at=BASE_TIME + timedelta(seconds=40),
            ),
            expected_version=expired_claim.record_version,
        )


async def test_progress_is_monotonic_versioned_and_lease_bounded(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    await _create(repository, admin_user_id)
    claimed = await _claim(repository)
    progress = _progress(claimed, now=BASE_TIME + timedelta(seconds=2))
    updated = await repository.update_progress(
        operation_id=claimed.operation_id,
        owner_id=_owner(claimed),
        now=progress.updated_at,
        progress=progress,
        local_job_id="job-001",
        expected_version=claimed.record_version,
    )
    assert updated.progress == progress
    assert updated.local_job_id == "job-001"
    assert updated.record_version == claimed.record_version + 1

    with pytest.raises(OperationProgressRegressionError):
        await repository.update_progress(
            operation_id=updated.operation_id,
            owner_id=_owner(claimed),
            now=BASE_TIME + timedelta(seconds=3),
            progress=replace(
                progress,
                chunks_completed=0,
                updated_at=BASE_TIME + timedelta(seconds=3),
            ),
            local_job_id="job-001",
            expected_version=updated.record_version,
        )
    with pytest.raises(OperationVersionConflictError):
        version_before_conflict = updated.record_version
        await repository.update_progress(
            operation_id=updated.operation_id,
            owner_id=_owner(claimed),
            now=BASE_TIME + timedelta(seconds=3),
            progress=replace(progress, updated_at=BASE_TIME + timedelta(seconds=3)),
            local_job_id="job-001",
            expected_version=claimed.record_version,
        )
    persisted_after_conflict = await repository.get(updated.operation_id)
    assert persisted_after_conflict is not None
    assert persisted_after_conflict.record_version == version_before_conflict
    with pytest.raises(InvalidOperationLeaseError):
        await repository.update_progress(
            operation_id=updated.operation_id,
            owner_id=_owner(claimed),
            now=BASE_TIME + timedelta(seconds=40),
            progress=replace(progress, updated_at=BASE_TIME + timedelta(seconds=40)),
            local_job_id="job-001",
            expected_version=updated.record_version,
        )


async def test_pause_resume_and_cancel_lifecycle(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    await _create(repository, admin_user_id)
    claimed = await _claim(repository)
    running = await repository.request_state(
        operation_id=claimed.operation_id,
        target=MarketOperationState.RUNNING,
        expected_version=claimed.record_version,
        now=BASE_TIME + timedelta(seconds=2),
        owner_id=_owner(claimed),
    )
    pause_requested = await repository.request_pause(
        operation_id=running.operation_id,
        expected_version=running.record_version,
        now=BASE_TIME + timedelta(seconds=3),
    )
    paused = await repository.request_state(
        operation_id=running.operation_id,
        target=MarketOperationState.PAUSED,
        expected_version=pause_requested.record_version,
        now=BASE_TIME + timedelta(seconds=4),
        owner_id=_owner(claimed),
    )
    assert paused.lease is None
    pending = await repository.resume(
        operation_id=paused.operation_id,
        expected_version=paused.record_version,
        now=BASE_TIME + timedelta(seconds=5),
    )
    assert pending.state is MarketOperationState.PENDING

    cancel_requested = await repository.request_cancel(
        operation_id=pending.operation_id,
        expected_version=pending.record_version,
        now=BASE_TIME + timedelta(seconds=6),
    )
    cancelled = await repository.request_state(
        operation_id=pending.operation_id,
        target=MarketOperationState.CANCELLED,
        expected_version=cancel_requested.record_version,
        now=BASE_TIME + timedelta(seconds=7),
    )
    assert cancelled.state is MarketOperationState.CANCELLED
    assert cancelled.failure == SanitizedOperationFailure(
        code=MarketOperationFailureCode.CANCELLED_BY_ADMIN,
        failed_at=BASE_TIME + timedelta(seconds=7),
    )
    with pytest.raises(MarketOperationTerminalError):
        await repository.request_pause(
            operation_id=cancelled.operation_id,
            expected_version=cancelled.record_version,
            now=BASE_TIME + timedelta(seconds=8),
        )


async def test_completion_and_failure_round_trip_sanitized_outcomes(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    await _create(repository, admin_user_id, idempotency_key="completion")
    claimed = await _claim(repository)
    running = await repository.request_state(
        operation_id=claimed.operation_id,
        target=MarketOperationState.RUNNING,
        expected_version=claimed.record_version,
        now=BASE_TIME + timedelta(seconds=2),
        owner_id=_owner(claimed),
    )
    final_progress = _progress(
        running,
        now=BASE_TIME + timedelta(seconds=3),
        chunks_completed=2,
        candles_received=10,
        candles_persisted=10,
        requests_completed=2,
    )
    result = OperationResult(
        dataset_version=DATASET_VERSION,
        dataset_checksum=DATASET_CHECKSUM,
        completed_at=BASE_TIME + timedelta(seconds=4),
    )
    completed = await repository.complete(
        operation_id=running.operation_id,
        owner_id=_owner(claimed),
        now=result.completed_at,
        result=result,
        progress=final_progress,
        expected_version=running.record_version,
    )
    assert completed.result == result
    assert completed.failure is None
    assert completed.lease is None

    await _create(
        repository,
        admin_user_id,
        idempotency_key="failure",
        symbol="ETH/USDT",
    )
    failed_claim = await _claim(repository)
    failure = SanitizedOperationFailure(
        code=MarketOperationFailureCode.NETWORK_FAILURE,
        failed_at=BASE_TIME + timedelta(seconds=3),
    )
    failed = await repository.fail(
        operation_id=failed_claim.operation_id,
        owner_id=_owner(failed_claim),
        now=failure.failed_at,
        failure=failure,
        progress=_progress(
            failed_claim,
            now=BASE_TIME + timedelta(seconds=2),
            chunks_completed=0,
            chunks_failed=1,
            candles_received=0,
            candles_persisted=0,
            requests_completed=0,
        ),
        expected_version=failed_claim.record_version,
    )
    assert failed.failure == failure
    assert failed.result is None
    assert failed.lease is None


async def test_completion_rejects_lost_lease(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    await _create(repository, admin_user_id)
    claimed = await _claim(repository, duration=timedelta(seconds=1))
    running = await repository.request_state(
        operation_id=claimed.operation_id,
        target=MarketOperationState.RUNNING,
        expected_version=claimed.record_version,
        now=BASE_TIME + timedelta(seconds=1, milliseconds=500),
        owner_id=_owner(claimed),
    )
    with pytest.raises(InvalidOperationLeaseError):
        await repository.complete(
            operation_id=running.operation_id,
            owner_id=_owner(claimed),
            now=BASE_TIME + timedelta(seconds=3),
            result=OperationResult(
                dataset_version=DATASET_VERSION,
                dataset_checksum=DATASET_CHECKSUM,
                completed_at=BASE_TIME + timedelta(seconds=1, milliseconds=750),
            ),
            progress=_progress(
                running,
                now=BASE_TIME + timedelta(seconds=1, milliseconds=750),
            ),
            expected_version=running.record_version,
        )
    persisted = await repository.get(running.operation_id)
    assert persisted is not None
    assert persisted.state is MarketOperationState.RUNNING
    assert persisted.record_version == running.record_version


async def test_wrong_owner_cannot_run_progress_complete_or_fail(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    await _create(repository, admin_user_id)
    claimed = await _claim(repository)
    correct_owner = _owner(claimed)
    wrong_owner = uuid4()

    with pytest.raises(InvalidOperationLeaseError) as transition_error:
        await repository.request_state(
            operation_id=claimed.operation_id,
            target=MarketOperationState.RUNNING,
            expected_version=claimed.record_version,
            now=BASE_TIME + timedelta(seconds=2),
            owner_id=wrong_owner,
        )
    assert str(wrong_owner) not in str(transition_error.value)
    assert await repository.get(claimed.operation_id) == claimed

    running = await repository.request_state(
        operation_id=claimed.operation_id,
        target=MarketOperationState.RUNNING,
        expected_version=claimed.record_version,
        now=BASE_TIME + timedelta(seconds=2),
        owner_id=correct_owner,
    )
    progress = _progress(running, now=BASE_TIME + timedelta(seconds=3))
    result = OperationResult(
        dataset_version=DATASET_VERSION,
        dataset_checksum=DATASET_CHECKSUM,
        completed_at=BASE_TIME + timedelta(seconds=4),
    )
    failure = SanitizedOperationFailure(
        code=MarketOperationFailureCode.NETWORK_FAILURE,
        failed_at=BASE_TIME + timedelta(seconds=4),
    )

    for mutation in (
        repository.update_progress(
            operation_id=running.operation_id,
            owner_id=wrong_owner,
            now=progress.updated_at,
            progress=progress,
            local_job_id="job-wrong-owner",
            expected_version=running.record_version,
        ),
        repository.complete(
            operation_id=running.operation_id,
            owner_id=wrong_owner,
            now=result.completed_at,
            result=result,
            progress=progress,
            expected_version=running.record_version,
        ),
        repository.fail(
            operation_id=running.operation_id,
            owner_id=wrong_owner,
            now=failure.failed_at,
            failure=failure,
            progress=progress,
            expected_version=running.record_version,
        ),
    ):
        with pytest.raises(InvalidOperationLeaseError) as captured:
            await mutation
        assert str(wrong_owner) not in str(captured.value)
        persisted = await repository.get(running.operation_id)
        assert persisted == running

    updated = await repository.update_progress(
        operation_id=running.operation_id,
        owner_id=correct_owner,
        now=progress.updated_at,
        progress=progress,
        local_job_id="job-correct-owner",
        expected_version=running.record_version,
    )
    assert updated.record_version == running.record_version + 1


async def test_concurrent_recovery_claims_never_return_the_same_operation(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    expired = await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.CLAIMED,
    )
    first_owner, second_owner = uuid4(), uuid4()

    claims = await asyncio.gather(
        repository.claim_next_expired(
            owner_id=first_owner,
            now=BASE_TIME + timedelta(seconds=3),
            lease_expires_at=BASE_TIME + timedelta(seconds=33),
        ),
        repository.claim_next_expired(
            owner_id=second_owner,
            now=BASE_TIME + timedelta(seconds=3),
            lease_expires_at=BASE_TIME + timedelta(seconds=33),
        ),
    )

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    winner = winners[0]
    assert winner.operation.operation_id == expired.operation_id
    assert winner.recovered_from is MarketOperationState.CLAIMED
    assert winner.operation.lease is not None
    assert winner.operation.lease.owner_id in {first_owner, second_owner}
    assert await repository.get(expired.operation_id) == winner.operation


@pytest.mark.parametrize(
    "state",
    [
        MarketOperationState.CLAIMED,
        MarketOperationState.RUNNING,
        MarketOperationState.PAUSE_REQUESTED,
        MarketOperationState.CANCEL_REQUESTED,
    ],
)
async def test_healthy_active_lease_is_not_recovered(
    database: Database,
    admin_user_id: UUID,
    state: MarketOperationState,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    healthy = await _leased_operation(
        repository,
        admin_user_id,
        state=state,
        duration=timedelta(seconds=30),
    )

    claim = await repository.claim_next_expired(
        owner_id=uuid4(),
        now=BASE_TIME + timedelta(seconds=3),
        lease_expires_at=BASE_TIME + timedelta(seconds=33),
    )

    assert claim is None
    assert await repository.get(healthy.operation_id) == healthy


@pytest.mark.parametrize(
    "state",
    [
        MarketOperationState.CLAIMED,
        MarketOperationState.RUNNING,
        MarketOperationState.PAUSE_REQUESTED,
        MarketOperationState.CANCEL_REQUESTED,
    ],
)
async def test_expired_active_state_gets_atomic_recovery_owner(
    database: Database,
    admin_user_id: UUID,
    state: MarketOperationState,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    expired = await _leased_operation(
        repository,
        admin_user_id,
        state=state,
    )
    old_lease = expired.lease
    assert old_lease is not None
    new_owner = uuid4()

    claim = await _claim_recovery(repository, owner_id=new_owner)

    assert claim.recovered_from is state
    assert claim.operation.state is MarketOperationState.RECOVERING
    assert claim.operation.record_version == expired.record_version + 2
    assert claim.operation.started_at == expired.started_at
    assert claim.operation.progress == expired.progress
    assert claim.operation.local_job_id == expired.local_job_id
    assert claim.operation.lease == WorkerLease(
        operation_id=expired.operation_id,
        owner_id=new_owner,
        claimed_at=BASE_TIME + timedelta(seconds=3),
        heartbeat_at=BASE_TIME + timedelta(seconds=3),
        lease_expires_at=BASE_TIME + timedelta(seconds=33),
    )
    assert claim.operation.lease != old_lease


async def test_healthy_recovery_lease_is_not_stolen(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.CLAIMED,
    )
    recovery = await _claim_recovery(repository)

    stolen = await repository.claim_next_expired(
        owner_id=uuid4(),
        now=BASE_TIME + timedelta(seconds=4),
        lease_expires_at=BASE_TIME + timedelta(seconds=34),
    )

    assert stolen is None
    assert await repository.get(recovery.operation.operation_id) == recovery.operation


async def test_expired_recovery_is_reassigned_to_a_new_owner(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.CLAIMED,
    )
    first_owner = uuid4()
    first = await _claim_recovery(
        repository,
        owner_id=first_owner,
        duration=timedelta(seconds=1),
    )
    new_owner = uuid4()
    second = await repository.claim_next_expired(
        owner_id=new_owner,
        now=BASE_TIME + timedelta(seconds=5),
        lease_expires_at=BASE_TIME + timedelta(seconds=35),
    )

    assert second is not None
    assert second.recovered_from is MarketOperationState.RECOVERING
    assert second.operation.record_version == first.operation.record_version + 2
    assert second.operation.lease is not None
    assert second.operation.lease.owner_id == new_owner


async def test_unleased_recovery_gets_one_update_claim(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    expired = await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.CLAIMED,
    )
    unleased = await _leave_recovering_unleased(
        database,
        repository,
        expired,
        now=BASE_TIME + timedelta(seconds=3),
    )
    claim = await _claim_recovery(
        repository,
        owner_id=uuid4(),
        now=BASE_TIME + timedelta(seconds=4),
    )

    assert claim.recovered_from is MarketOperationState.RECOVERING
    assert claim.operation.record_version == unleased.record_version + 1
    assert claim.operation.lease is not None


@pytest.mark.parametrize(
    "invalid_owner",
    [
        cast(UUID, ""),
        cast(UUID, "x" * 256),
        UUID(int=0),
    ],
    ids=["empty", "excessive", "nil-uuid"],
)
async def test_invalid_owner_is_sanitized_and_cannot_mutate(
    database: Database,
    admin_user_id: UUID,
    invalid_owner: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    created = await _create(repository, admin_user_id)
    with pytest.raises(InvalidOperationLeaseError) as captured:
        await repository.claim_next(
            owner_id=invalid_owner,
            now=BASE_TIME + timedelta(seconds=1),
            lease_expires_at=BASE_TIME + timedelta(seconds=31),
        )
    rendered_owner = str(invalid_owner)
    if rendered_owner:
        assert rendered_owner not in str(captured.value)
    assert await repository.get(created.operation_id) == created


async def test_recovery_owner_cannot_hold_a_second_lease(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.CLAIMED,
        idempotency_key="recovery-owner-1",
        symbol="BTC/USDT",
    )
    await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.CLAIMED,
        idempotency_key="recovery-owner-2",
        symbol="ETH/USDT",
    )
    owner_id = uuid4()
    first = await _claim_recovery(repository, owner_id=owner_id)

    second = await repository.claim_next_expired(
        owner_id=owner_id,
        now=BASE_TIME + timedelta(seconds=4),
        lease_expires_at=BASE_TIME + timedelta(seconds=34),
    )

    assert second is None
    assert first.operation.lease is not None
    assert first.operation.lease.owner_id == owner_id


async def test_concurrent_recovery_distributes_two_expired_datasets(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    first = await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.CLAIMED,
        idempotency_key="recovery-dataset-1",
        symbol="BTC/USDT",
    )
    second = await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.CLAIMED,
        idempotency_key="recovery-dataset-2",
        symbol="ETH/USDT",
    )
    owners = (uuid4(), uuid4())

    claims = await asyncio.gather(
        *(
            repository.claim_next_expired(
                owner_id=owner,
                now=BASE_TIME + timedelta(seconds=3),
                lease_expires_at=BASE_TIME + timedelta(seconds=33),
            )
            for owner in owners
        )
    )

    assert all(claim is not None for claim in claims)
    claimed_operations = {claim.operation.operation_id for claim in claims if claim is not None}
    claimed_owners = {
        claim.operation.lease.owner_id
        for claim in claims
        if claim is not None and claim.operation.lease is not None
    }
    assert claimed_operations == {first.operation_id, second.operation_id}
    assert claimed_owners == set(owners)


async def test_recovery_ordering_prioritizes_abandoned_then_expiry_then_stable_id(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    latest = await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.CLAIMED,
        duration=timedelta(seconds=7),
        idempotency_key="order-latest",
        symbol="BTC/USDT",
    )
    oldest = await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.CLAIMED,
        duration=timedelta(seconds=3),
        idempotency_key="order-oldest",
        symbol="ETH/USDT",
    )
    smaller_id = UUID(int=100)
    larger_id = UUID(int=101)
    tied_smaller = await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.CLAIMED,
        duration=timedelta(seconds=5),
        operation_id=smaller_id,
        idempotency_key="order-tied-smaller",
        symbol="SOL/USDT",
    )
    tied_larger = await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.CLAIMED,
        duration=timedelta(seconds=5),
        operation_id=larger_id,
        idempotency_key="order-tied-larger",
        symbol="ADA/USDT",
    )
    abandoned_source = await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.CLAIMED,
        duration=timedelta(seconds=2),
        idempotency_key="order-abandoned",
        symbol="XRP/USDT",
    )
    abandoned = await _leave_recovering_unleased(
        database,
        repository,
        abandoned_source,
        now=BASE_TIME + timedelta(seconds=9),
    )

    claimed_ids: list[UUID] = []
    recovered_states: list[MarketOperationState] = []
    for second in range(10, 15):
        claim = await _claim_recovery(
            repository,
            now=BASE_TIME + timedelta(seconds=second),
        )
        claimed_ids.append(claim.operation.operation_id)
        recovered_states.append(claim.recovered_from)

    assert claimed_ids == [
        abandoned.operation_id,
        oldest.operation_id,
        tied_smaller.operation_id,
        tied_larger.operation_id,
        latest.operation_id,
    ]
    assert recovered_states[0] is MarketOperationState.RECOVERING
    assert recovered_states[1:] == [MarketOperationState.CLAIMED] * 4


async def test_old_owner_is_stale_for_every_worker_mutation_after_recovery(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    expired = await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.RUNNING,
    )
    old_owner = _owner(expired)
    new_owner = uuid4()
    recovery = await _claim_recovery(repository, owner_id=new_owner)
    recovered = recovery.operation
    running = await repository.reconcile(
        operation=replace(
            recovered,
            state=MarketOperationState.RUNNING,
            updated_at=BASE_TIME + timedelta(seconds=4),
            record_version=recovered.record_version + 1,
        ),
        owner_id=new_owner,
        now=BASE_TIME + timedelta(seconds=4),
        expected_version=recovered.record_version,
    )
    assert running.lease is not None
    forward_lease = replace(
        running.lease,
        heartbeat_at=BASE_TIME + timedelta(seconds=5),
        lease_expires_at=BASE_TIME + timedelta(seconds=40),
    )
    progress = replace(
        running.progress,
        updated_at=BASE_TIME + timedelta(seconds=5),
    )
    result = OperationResult(
        dataset_version=DATASET_VERSION,
        dataset_checksum=DATASET_CHECKSUM,
        completed_at=BASE_TIME + timedelta(seconds=5),
    )
    failure = SanitizedOperationFailure(
        code=MarketOperationFailureCode.LEASE_LOST,
        failed_at=BASE_TIME + timedelta(seconds=5),
    )
    mutations = (
        repository.renew_lease(
            operation_id=running.operation_id,
            owner_id=old_owner,
            now=BASE_TIME + timedelta(seconds=5),
            lease=forward_lease,
            expected_version=running.record_version,
        ),
        repository.update_progress(
            operation_id=running.operation_id,
            owner_id=old_owner,
            now=BASE_TIME + timedelta(seconds=5),
            progress=progress,
            local_job_id=running.local_job_id,
            expected_version=running.record_version,
        ),
        repository.complete(
            operation_id=running.operation_id,
            owner_id=old_owner,
            now=BASE_TIME + timedelta(seconds=5),
            result=result,
            progress=progress,
            expected_version=running.record_version,
        ),
        repository.fail(
            operation_id=running.operation_id,
            owner_id=old_owner,
            now=BASE_TIME + timedelta(seconds=5),
            failure=failure,
            progress=progress,
            expected_version=running.record_version,
        ),
    )
    for mutation in mutations:
        with pytest.raises(InvalidOperationLeaseError):
            await mutation
        assert await repository.get(running.operation_id) == running


async def test_reconcile_correct_owner_preserves_recovery_lease(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.RUNNING,
    )
    owner_id = uuid4()
    recovery = await _claim_recovery(repository, owner_id=owner_id)
    recovered = recovery.operation

    reconciled = await repository.reconcile(
        operation=replace(
            recovered,
            state=MarketOperationState.RUNNING,
            updated_at=BASE_TIME + timedelta(seconds=4),
            record_version=recovered.record_version + 1,
        ),
        owner_id=owner_id,
        now=BASE_TIME + timedelta(seconds=4),
        expected_version=recovered.record_version,
    )

    assert reconciled.state is MarketOperationState.RUNNING
    assert reconciled.lease == recovered.lease
    assert reconciled.record_version == recovered.record_version + 1


async def test_reconcile_terminal_clears_recovery_lease(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.RUNNING,
    )
    owner_id = uuid4()
    recovery = await _claim_recovery(repository, owner_id=owner_id)
    recovered = recovery.operation
    finished_at = BASE_TIME + timedelta(seconds=4)
    result = OperationResult(
        dataset_version=DATASET_VERSION,
        dataset_checksum=DATASET_CHECKSUM,
        completed_at=finished_at,
    )

    reconciled = await repository.reconcile(
        operation=replace(
            recovered,
            state=MarketOperationState.COMPLETED,
            lease=None,
            result=result,
            finished_at=finished_at,
            updated_at=finished_at,
            record_version=recovered.record_version + 1,
        ),
        owner_id=owner_id,
        now=finished_at,
        expected_version=recovered.record_version,
    )

    assert reconciled.state is MarketOperationState.COMPLETED
    assert reconciled.result == result
    assert reconciled.lease is None
    assert reconciled.record_version == recovered.record_version + 1


async def test_reconcile_wrong_owner_rejects_without_mutation(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.RUNNING,
    )
    recovery = await _claim_recovery(repository)
    recovered = recovery.operation

    with pytest.raises(InvalidOperationLeaseError):
        await repository.reconcile(
            operation=replace(
                recovered,
                state=MarketOperationState.RUNNING,
                updated_at=BASE_TIME + timedelta(seconds=4),
                record_version=recovered.record_version + 1,
            ),
            owner_id=uuid4(),
            now=BASE_TIME + timedelta(seconds=4),
            expected_version=recovered.record_version,
        )

    assert await repository.get(recovered.operation_id) == recovered


async def test_reconcile_expired_lease_rejects_without_mutation(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.RUNNING,
    )
    owner_id = uuid4()
    recovery = await _claim_recovery(
        repository,
        owner_id=owner_id,
        duration=timedelta(seconds=1),
    )
    recovered = recovery.operation

    with pytest.raises(InvalidOperationLeaseError):
        await repository.reconcile(
            operation=replace(
                recovered,
                state=MarketOperationState.RUNNING,
                updated_at=BASE_TIME + timedelta(seconds=5),
                record_version=recovered.record_version + 1,
            ),
            owner_id=owner_id,
            now=BASE_TIME + timedelta(seconds=5),
            expected_version=recovered.record_version,
        )

    assert await repository.get(recovered.operation_id) == recovered


async def test_reconcile_stale_version_rejects_without_mutation(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.RUNNING,
    )
    recovery = await _claim_recovery(repository)
    recovered = recovery.operation
    assert recovered.lease is not None

    with pytest.raises(OperationVersionConflictError):
        await repository.reconcile(
            operation=replace(
                recovered,
                state=MarketOperationState.RUNNING,
                updated_at=BASE_TIME + timedelta(seconds=4),
                record_version=recovered.record_version + 1,
            ),
            owner_id=recovered.lease.owner_id,
            now=BASE_TIME + timedelta(seconds=4),
            expected_version=recovered.record_version - 1,
        )

    assert await repository.get(recovered.operation_id) == recovered


async def test_reconcile_cannot_clear_an_active_recovery_lease(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.RUNNING,
    )
    recovery = await _claim_recovery(repository)
    recovered = recovery.operation
    assert recovered.lease is not None

    with pytest.raises(InvalidOperationLeaseError):
        await repository.reconcile(
            operation=replace(
                recovered,
                lease=None,
                updated_at=BASE_TIME + timedelta(seconds=4),
                record_version=recovered.record_version + 1,
            ),
            owner_id=recovered.lease.owner_id,
            now=BASE_TIME + timedelta(seconds=4),
            expected_version=recovered.record_version,
        )

    assert await repository.get(recovered.operation_id) == recovered


async def test_direct_recovering_request_is_no_longer_a_public_path(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    expired = await _leased_operation(
        repository,
        admin_user_id,
        state=MarketOperationState.CLAIMED,
    )

    with pytest.raises(InvalidOperationTransitionError):
        await repository.request_state(
            operation_id=expired.operation_id,
            target=MarketOperationState.RECOVERING,
            expected_version=expired.record_version,
            now=BASE_TIME + timedelta(seconds=3),
        )

    assert await repository.get(expired.operation_id) == expired


async def test_pending_operation_is_not_a_recovery_candidate(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    pending = await _create(repository, admin_user_id)

    claim = await repository.claim_next_expired(
        owner_id=uuid4(),
        now=BASE_TIME + timedelta(seconds=3),
        lease_expires_at=BASE_TIME + timedelta(seconds=33),
    )

    assert claim is None
    assert await repository.get(pending.operation_id) == pending


@pytest.mark.parametrize(
    "state",
    [
        MarketOperationState.PAUSED,
        MarketOperationState.CANCELLED,
        MarketOperationState.COMPLETED,
        MarketOperationState.FAILED,
    ],
)
async def test_unleased_or_terminal_state_is_not_a_recovery_candidate(
    database: Database,
    admin_user_id: UUID,
    state: MarketOperationState,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    created = await _create(repository, admin_user_id)
    if state is MarketOperationState.CANCELLED:
        cancel_requested = await repository.request_cancel(
            operation_id=created.operation_id,
            expected_version=created.record_version,
            now=BASE_TIME + timedelta(seconds=1),
        )
        settled = await repository.request_state(
            operation_id=created.operation_id,
            target=state,
            expected_version=cancel_requested.record_version,
            now=BASE_TIME + timedelta(seconds=2),
        )
    else:
        claimed = await _claim(repository, duration=timedelta(seconds=30))
        if state is MarketOperationState.PAUSED:
            pause_requested = await repository.request_pause(
                operation_id=claimed.operation_id,
                expected_version=claimed.record_version,
                now=BASE_TIME + timedelta(seconds=2),
            )
            settled = await repository.request_state(
                operation_id=claimed.operation_id,
                target=state,
                expected_version=pause_requested.record_version,
                now=BASE_TIME + timedelta(seconds=3),
                owner_id=_owner(claimed),
            )
        elif state is MarketOperationState.COMPLETED:
            running = await repository.request_state(
                operation_id=claimed.operation_id,
                target=MarketOperationState.RUNNING,
                expected_version=claimed.record_version,
                now=BASE_TIME + timedelta(seconds=2),
                owner_id=_owner(claimed),
            )
            completed_at = BASE_TIME + timedelta(seconds=3)
            settled = await repository.complete(
                operation_id=running.operation_id,
                owner_id=_owner(claimed),
                now=completed_at,
                result=OperationResult(
                    dataset_version=DATASET_VERSION,
                    dataset_checksum=DATASET_CHECKSUM,
                    completed_at=completed_at,
                ),
                progress=replace(running.progress, updated_at=completed_at),
                expected_version=running.record_version,
            )
        else:
            failed_at = BASE_TIME + timedelta(seconds=2)
            settled = await repository.fail(
                operation_id=claimed.operation_id,
                owner_id=_owner(claimed),
                now=failed_at,
                failure=SanitizedOperationFailure(
                    code=MarketOperationFailureCode.NETWORK_FAILURE,
                    failed_at=failed_at,
                ),
                progress=replace(claimed.progress, updated_at=failed_at),
                expected_version=claimed.record_version,
            )

    claim = await repository.claim_next_expired(
        owner_id=uuid4(),
        now=BASE_TIME + timedelta(seconds=10),
        lease_expires_at=BASE_TIME + timedelta(seconds=40),
    )

    assert settled.state is state
    assert claim is None
    assert await repository.get(settled.operation_id) == settled


async def test_invalid_transition_and_direct_terminal_or_immutable_update_are_rejected(
    database: Database,
    database_url: str,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    created = await _create(repository, admin_user_id)
    with pytest.raises(InvalidOperationTransitionError):
        await repository.request_state(
            operation_id=created.operation_id,
            target=MarketOperationState.RUNNING,
            expected_version=created.record_version,
            now=BASE_TIME + timedelta(seconds=1),
        )

    cancel_requested = await repository.request_cancel(
        operation_id=created.operation_id,
        expected_version=created.record_version,
        now=BASE_TIME + timedelta(seconds=1),
    )
    cancelled = await repository.request_state(
        operation_id=created.operation_id,
        target=MarketOperationState.CANCELLED,
        expected_version=cancel_requested.record_version,
        now=BASE_TIME + timedelta(seconds=2),
    )
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                """
                update public.market_data_operations
                set status = 'PENDING', version = version + 1
                where id = %s
                """,
                (cancelled.operation_id,),
            )

    immutable = await _create(
        repository,
        admin_user_id,
        idempotency_key="immutable",
        symbol="ETH/USDT",
    )
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                """
                update public.market_data_operations
                set symbol = 'SOL/USDT', version = version + 1
                where id = %s
                """,
                (immutable.operation_id,),
            )


async def test_database_constraints_reject_progress_limits_and_version_skips(
    database: Database,
    database_url: str,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    created = await _create(repository, admin_user_id)
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                update public.market_data_operations
                set chunks_completed = chunks_planned + 1, version = version + 1
                where id = %s
                """,
                (created.operation_id,),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                update public.market_data_operations
                set candles_persisted = candles_received + 1, version = version + 1
                where id = %s
                """,
                (created.operation_id,),
            )
        with pytest.raises(psycopg.errors.SerializationFailure):
            connection.execute(
                """
                update public.market_data_operations
                set updated_at = updated_at + interval '1 second',
                    version = version + 2
                where id = %s
                """,
                (created.operation_id,),
            )


async def test_database_rejects_invalid_inserted_catalog_states(
    database: Database,
    database_url: str,
    admin_user_id: UUID,
) -> None:
    """Closed enums, identity, range, hashes, lease and outcomes are DB invariants."""
    repository = PostgresMarketOperationRepository(database)
    source = await _create(repository, admin_user_id)
    invalid_patches: tuple[dict[str, object], ...] = (
        {"operation_type": "DERIVED_MATERIALIZATION"},
        {"range_end": BASE_TIME.isoformat()},
        {"plan_checksum": "A" * 64},
        {"chunks_completed": 3},
        {"candles_persisted": 1},
        {"version": 0},
        {"dataset_id": encode_dataset_id(_request(admin_user_id, symbol="ETH/USDT").dataset)},
        {
            "lease_owner": str(uuid4()),
            "status": MarketOperationState.CLAIMED.value,
        },
        {
            "status": MarketOperationState.COMPLETED.value,
            "finished_at": (BASE_TIME + timedelta(seconds=1)).isoformat(),
            "updated_at": (BASE_TIME + timedelta(seconds=1)).isoformat(),
        },
        {
            "status": MarketOperationState.FAILED.value,
            "failure_code": MarketOperationFailureCode.NETWORK_FAILURE.value,
            "failure_message": "raw remote diagnostic",
            "finished_at": (BASE_TIME + timedelta(seconds=1)).isoformat(),
            "updated_at": (BASE_TIME + timedelta(seconds=1)).isoformat(),
        },
    )
    with psycopg.connect(database_url, autocommit=True) as connection:
        for patch in invalid_patches:
            unique_patch = {
                "id": str(uuid4()),
                "idempotency_key": f"invalid-{uuid4().hex}",
                **patch,
            }
            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    insert into public.market_data_operations
                    select (
                        jsonb_populate_record(
                            null::public.market_data_operations,
                            to_jsonb(operation) || %s::jsonb
                        )
                    ).*
                    from public.market_data_operations as operation
                    where operation.id = %s
                    """,
                    (Jsonb(unique_patch), source.operation_id),
                )


async def test_database_trigger_rejects_progress_regression(
    database: Database,
    database_url: str,
    admin_user_id: UUID,
) -> None:
    repository = PostgresMarketOperationRepository(database)
    await _create(repository, admin_user_id)
    claimed = await _claim(repository)
    advanced = await repository.update_progress(
        operation_id=claimed.operation_id,
        owner_id=_owner(claimed),
        now=BASE_TIME + timedelta(seconds=2),
        progress=_progress(claimed, now=BASE_TIME + timedelta(seconds=2)),
        local_job_id=None,
        expected_version=claimed.record_version,
    )
    with psycopg.connect(database_url, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                update public.market_data_operations
                set chunks_completed = chunks_completed - 1,
                    updated_at = updated_at + interval '1 second',
                    version = version + 1
                where id = %s
                """,
                (advanced.operation_id,),
            )


def test_row_mapper_normalizes_timestamptz_to_utc_without_losing_fields() -> None:
    admin_id = uuid4()
    operation = market_operation_from_row(_valid_operation_row(admin_id))
    assert operation.request == _request(admin_id)
    assert operation.created_at == BASE_TIME
    assert operation.updated_at.tzinfo is UTC
    assert operation.progress.updated_at == BASE_TIME
    assert operation.started_at is None
    assert operation.record_version == 1


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("request_fingerprint", "0" * 64),
        ("dataset_id", "YnJva2Vu"),
        ("created_at", datetime(2026, 7, 1)),
        ("version", "1"),
    ],
)
def test_row_mapper_rejects_invalid_persisted_state(
    field: str,
    invalid_value: object,
) -> None:
    row = _valid_operation_row(uuid4())
    row[field] = invalid_value
    with pytest.raises(InvalidPersistedOperationError):
        market_operation_from_row(row)


def test_failure_messages_are_closed_and_do_not_echo_diagnostics() -> None:
    secret = "postgresql://user:password@example.invalid/database"
    for code in MarketOperationFailureCode:
        message = failure_message_for(code)
        assert 0 < len(message) <= 160
        assert secret not in message
        assert code.value not in message


def test_repository_validates_before_opening_a_transaction() -> None:
    class NoSqlDatabase:
        def transaction(self) -> Any:
            raise AssertionError("SQL must not be reached")

    repository = PostgresMarketOperationRepository(cast(Database, NoSqlDatabase()))
    with pytest.raises(InvalidMarketOperationRequestError):
        asyncio.run(repository.list(limit=0, offset=0))
    non_utc = BASE_TIME.astimezone(timezone(timedelta(hours=-3)))
    with pytest.raises(InvalidMarketOperationRequestError):
        asyncio.run(
            repository.create_idempotently(
                operation_id=uuid4(),
                request=_request(uuid4()),
                plan=_plan(),
                now=non_utc,
            )
        )
    with pytest.raises(InvalidMarketOperationRequestError):
        asyncio.run(
            repository.request_state(
                operation_id=uuid4(),
                target=MarketOperationState.PAUSE_REQUESTED,
                expected_version=1,
                now=non_utc,
            )
        )
    with pytest.raises(InvalidOperationLeaseError):
        asyncio.run(
            repository.claim_next_expired(
                owner_id=UUID(int=0),
                now=BASE_TIME,
                lease_expires_at=BASE_TIME + timedelta(seconds=30),
            )
        )
    with pytest.raises(InvalidMarketOperationRequestError):
        asyncio.run(
            repository.claim_next_expired(
                owner_id=uuid4(),
                now=non_utc,
                lease_expires_at=BASE_TIME + timedelta(seconds=30),
            )
        )


def test_request_fingerprint_excludes_secret_idempotency_key() -> None:
    admin_id = uuid4()
    first = _request(admin_id, idempotency_key="secret-like-client-value")
    second = _request(admin_id, idempotency_key="different-client-value")
    assert operation_request_fingerprint(first) == operation_request_fingerprint(second)
