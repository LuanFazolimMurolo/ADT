"""Worker-runtime observability repository tests against disposable PostgreSQL."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.database import Database
from app.domain.errors import PersistenceError
from app.market_data.domain import (
    DataRange,
    Exchange,
    MarketType,
    TradingPair,
)
from app.market_data.errors import (
    InvalidWorkerRuntimeObservabilityError,
    WorkerRuntimeNotFoundError,
    WorkerRuntimeTerminalError,
)
from app.market_data.operations import (
    MarketDatasetSelector,
    MarketOperationRequest,
    MarketOperationState,
    MarketOperationType,
    OperationPlanSummary,
)
from app.market_data.timeframes import get_timeframe
from app.market_data.worker_observability import (
    WorkerRuntimeActivityState,
    WorkerRuntimeEventType,
    WorkerRuntimeFailureCode,
    WorkerRuntimeLifecycleState,
)
from app.market_data.worker_observability_ports import (
    WorkerRuntimeObservabilityRepository,
)
from app.repositories.market_operation_repository import (
    PostgresMarketOperationRepository,
)
from app.repositories.worker_observability import (
    PostgresWorkerRuntimeObservabilityRepository,
)

BASE_TIME = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
PLAN_CHECKSUM = "d" * 64


def _as_port(
    repository: PostgresWorkerRuntimeObservabilityRepository,
) -> WorkerRuntimeObservabilityRepository:
    """Statically prove the PostgreSQL implementation satisfies the port."""
    return repository


async def _event_count(
    database: Database,
    runtime_id: UUID,
    event_type: WorkerRuntimeEventType,
) -> int:
    async with database.transaction() as connection:
        cursor = await connection.execute(
            """
            select count(*) as total
            from public.market_data_worker_events
            where runtime_id = %s
              and event_type = %s
            """,
            (runtime_id, event_type.value),
        )
        row = await cursor.fetchone()

    assert row is not None
    return int(row["total"])


async def _install_event_insert_failure(database: Database) -> None:
    """Install a test-only trigger that makes event INSERT fail."""

    async with database.transaction() as connection:
        await connection.execute(
            """
            create function public.reject_worker_event_insert_for_test()
            returns trigger
            language plpgsql
            set search_path = ''
            as $function$
            begin
                raise exception using
                    errcode = 'P0001',
                    message = 'worker_event_test_rejected';
            end;
            $function$
            """
        )
        await connection.execute(
            """
            create trigger reject_worker_event_insert_for_test
            before insert on public.market_data_worker_events
            for each row
            execute function public.reject_worker_event_insert_for_test()
            """
        )


def _operation_request(
    admin_user_id: UUID,
    *,
    idempotency_key: str,
) -> MarketOperationRequest:
    return MarketOperationRequest(
        operation_type=MarketOperationType.RAW_BACKFILL,
        dataset=MarketDatasetSelector(
            exchange=Exchange.BINANCE,
            market_type=MarketType.SPOT,
            pair=TradingPair.parse("BTC/USDT"),
            timeframe=get_timeframe("1m"),
        ),
        data_range=DataRange(
            start=BASE_TIME - timedelta(minutes=10),
            end=BASE_TIME,
        ),
        plan_checksum=PLAN_CHECKSUM,
        idempotency_key=idempotency_key,
        requested_by=admin_user_id,
    )


def _operation_plan() -> OperationPlanSummary:
    return OperationPlanSummary(
        checksum=PLAN_CHECKSUM,
        chunks_planned=2,
        estimated_candles=10,
        estimated_requests=2,
        created_at=BASE_TIME,
    )


async def _cancelled_operation(
    database: Database,
    admin_user_id: UUID,
) -> UUID:
    repository = PostgresMarketOperationRepository(database)

    created = await repository.create_idempotently(
        operation_id=uuid4(),
        request=_operation_request(
            admin_user_id,
            idempotency_key=f"worker-observability-{uuid4().hex}",
        ),
        plan=_operation_plan(),
        now=BASE_TIME,
    )

    cancel_requested = await repository.request_cancel(
        operation_id=created.operation_id,
        expected_version=created.record_version,
        now=BASE_TIME + timedelta(seconds=1),
    )

    cancelled = await repository.request_state(
        operation_id=cancel_requested.operation_id,
        target=MarketOperationState.CANCELLED,
        expected_version=cancel_requested.record_version,
        now=BASE_TIME + timedelta(seconds=2),
    )

    assert cancelled.state is MarketOperationState.CANCELLED
    return cancelled.operation_id


async def test_concurrent_start_is_idempotent_and_creates_one_event(
    database: Database,
) -> None:
    concrete = PostgresWorkerRuntimeObservabilityRepository(database)
    repository = _as_port(concrete)
    runtime_id = uuid4()

    first, second = await asyncio.gather(
        repository.start_idempotently(
            runtime_id=runtime_id,
            now=BASE_TIME,
        ),
        repository.start_idempotently(
            runtime_id=runtime_id,
            now=BASE_TIME,
        ),
    )

    assert first == second
    assert first.runtime_id == runtime_id
    assert first.lifecycle_state is WorkerRuntimeLifecycleState.RUNNING
    assert first.activity_state is WorkerRuntimeActivityState.IDLE
    assert first.started_at == BASE_TIME
    assert first.heartbeat_at == BASE_TIME

    assert (
        await _event_count(
            database,
            runtime_id,
            WorkerRuntimeEventType.RUNTIME_STARTED,
        )
        == 1
    )


async def test_start_retry_requires_same_authoritative_start_timestamp(
    database: Database,
) -> None:
    repository = PostgresWorkerRuntimeObservabilityRepository(database)
    runtime_id = uuid4()

    started = await repository.start_idempotently(
        runtime_id=runtime_id,
        now=BASE_TIME,
    )

    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        await repository.start_idempotently(
            runtime_id=runtime_id,
            now=BASE_TIME + timedelta(seconds=1),
        )

    assert await repository.get(runtime_id) == started
    assert (
        await _event_count(
            database,
            runtime_id,
            WorkerRuntimeEventType.RUNTIME_STARTED,
        )
        == 1
    )


async def test_heartbeat_is_monotonic_and_start_retry_returns_current_state(
    database: Database,
) -> None:
    repository = PostgresWorkerRuntimeObservabilityRepository(database)
    runtime_id = uuid4()

    await repository.start_idempotently(
        runtime_id=runtime_id,
        now=BASE_TIME,
    )

    heartbeat_at = BASE_TIME + timedelta(seconds=30)

    active = await repository.heartbeat(
        runtime_id=runtime_id,
        now=heartbeat_at,
        activity_state=WorkerRuntimeActivityState.ACTIVE,
    )

    assert active.activity_state is WorkerRuntimeActivityState.ACTIVE
    assert active.heartbeat_at == heartbeat_at

    retried_start = await repository.start_idempotently(
        runtime_id=runtime_id,
        now=BASE_TIME,
    )

    assert retried_start == active

    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        await repository.heartbeat(
            runtime_id=runtime_id,
            now=heartbeat_at - timedelta(seconds=1),
            activity_state=WorkerRuntimeActivityState.IDLE,
        )

    assert await repository.get(runtime_id) == active


async def test_stop_persists_terminal_state_event_and_blocks_future_mutation(
    database: Database,
) -> None:
    repository = PostgresWorkerRuntimeObservabilityRepository(database)
    runtime_id = uuid4()

    await repository.start_idempotently(
        runtime_id=runtime_id,
        now=BASE_TIME,
    )

    await repository.heartbeat(
        runtime_id=runtime_id,
        now=BASE_TIME + timedelta(seconds=10),
        activity_state=WorkerRuntimeActivityState.ACTIVE,
    )

    stopped_at = BASE_TIME + timedelta(seconds=20)

    stopped = await repository.stop(
        runtime_id=runtime_id,
        now=stopped_at,
    )

    assert stopped.lifecycle_state is WorkerRuntimeLifecycleState.STOPPED
    assert stopped.activity_state is WorkerRuntimeActivityState.IDLE
    assert stopped.heartbeat_at == stopped_at
    assert stopped.stopped_at == stopped_at
    assert stopped.failure_code is None

    assert (
        await _event_count(
            database,
            runtime_id,
            WorkerRuntimeEventType.RUNTIME_STOPPED,
        )
        == 1
    )

    with pytest.raises(WorkerRuntimeTerminalError):
        await repository.heartbeat(
            runtime_id=runtime_id,
            now=stopped_at + timedelta(seconds=1),
            activity_state=WorkerRuntimeActivityState.IDLE,
        )

    with pytest.raises(WorkerRuntimeTerminalError):
        await repository.stop(
            runtime_id=runtime_id,
            now=stopped_at + timedelta(seconds=1),
        )

    with pytest.raises(WorkerRuntimeTerminalError):
        await repository.fail(
            runtime_id=runtime_id,
            now=stopped_at + timedelta(seconds=1),
            failure_code=WorkerRuntimeFailureCode.UNEXPECTED_FAILURE,
        )


async def test_fail_persists_closed_failure_code_and_event(
    database: Database,
) -> None:
    repository = PostgresWorkerRuntimeObservabilityRepository(database)
    runtime_id = uuid4()

    await repository.start_idempotently(
        runtime_id=runtime_id,
        now=BASE_TIME,
    )

    failed_at = BASE_TIME + timedelta(seconds=15)

    failed = await repository.fail(
        runtime_id=runtime_id,
        now=failed_at,
        failure_code=WorkerRuntimeFailureCode.LOCAL_STATE_FAILURE,
    )

    assert failed.lifecycle_state is WorkerRuntimeLifecycleState.FAILED
    assert failed.activity_state is WorkerRuntimeActivityState.IDLE
    assert failed.heartbeat_at == failed_at
    assert failed.stopped_at == failed_at
    assert failed.failure_code is WorkerRuntimeFailureCode.LOCAL_STATE_FAILURE

    assert (
        await _event_count(
            database,
            runtime_id,
            WorkerRuntimeEventType.RUNTIME_FAILED,
        )
        == 1
    )


async def test_runtime_start_and_started_event_are_atomic(
    database: Database,
) -> None:
    repository = PostgresWorkerRuntimeObservabilityRepository(database)
    runtime_id = uuid4()

    await _install_event_insert_failure(database)

    with pytest.raises(PersistenceError):
        await repository.start_idempotently(
            runtime_id=runtime_id,
            now=BASE_TIME,
        )

    assert await repository.get(runtime_id) is None


async def test_runtime_terminalization_and_event_are_atomic(
    database: Database,
) -> None:
    repository = PostgresWorkerRuntimeObservabilityRepository(database)
    runtime_id = uuid4()

    started = await repository.start_idempotently(
        runtime_id=runtime_id,
        now=BASE_TIME,
    )

    await _install_event_insert_failure(database)

    with pytest.raises(PersistenceError):
        await repository.stop(
            runtime_id=runtime_id,
            now=BASE_TIME + timedelta(seconds=20),
        )

    persisted = await repository.get(runtime_id)

    assert persisted == started
    assert persisted is not None
    assert persisted.lifecycle_state is WorkerRuntimeLifecycleState.RUNNING

    assert (
        await _event_count(
            database,
            runtime_id,
            WorkerRuntimeEventType.RUNTIME_STOPPED,
        )
        == 0
    )


async def test_missing_runtime_is_reported_without_creating_state(
    database: Database,
) -> None:
    repository = PostgresWorkerRuntimeObservabilityRepository(database)
    runtime_id = uuid4()

    assert await repository.get(runtime_id) is None

    with pytest.raises(WorkerRuntimeNotFoundError):
        await repository.heartbeat(
            runtime_id=runtime_id,
            now=BASE_TIME,
            activity_state=WorkerRuntimeActivityState.IDLE,
        )

    with pytest.raises(WorkerRuntimeNotFoundError):
        await repository.stop(
            runtime_id=runtime_id,
            now=BASE_TIME,
        )

    with pytest.raises(WorkerRuntimeNotFoundError):
        await repository.fail(
            runtime_id=runtime_id,
            now=BASE_TIME,
            failure_code=WorkerRuntimeFailureCode.DATABASE_FAILURE,
        )

    assert await repository.get(runtime_id) is None


async def test_operation_settled_event_requires_matching_persisted_state(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresWorkerRuntimeObservabilityRepository(database)
    runtime_id = uuid4()

    await repository.start_idempotently(
        runtime_id=runtime_id,
        now=BASE_TIME + timedelta(seconds=10),
    )

    operation_id = await _cancelled_operation(
        database,
        admin_user_id,
    )

    event = await repository.record_operation_settled(
        runtime_id=runtime_id,
        operation_id=operation_id,
        operation_state=MarketOperationState.CANCELLED,
        now=BASE_TIME + timedelta(seconds=20),
    )

    assert event.runtime_id == runtime_id
    assert event.operation_id == operation_id
    assert event.event_type is WorkerRuntimeEventType.OPERATION_SETTLED
    assert event.operation_state is MarketOperationState.CANCELLED

    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        await repository.record_operation_settled(
            runtime_id=runtime_id,
            operation_id=operation_id,
            operation_state=MarketOperationState.COMPLETED,
            now=BASE_TIME + timedelta(seconds=21),
        )

    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        await repository.record_operation_settled(
            runtime_id=runtime_id,
            operation_id=operation_id,
            operation_state=MarketOperationState.RUNNING,
            now=BASE_TIME + timedelta(seconds=21),
        )

    assert (
        await _event_count(
            database,
            runtime_id,
            WorkerRuntimeEventType.OPERATION_SETTLED,
        )
        == 1
    )


async def test_recent_reads_are_bounded_and_stably_ordered(
    database: Database,
) -> None:
    repository = PostgresWorkerRuntimeObservabilityRepository(database)

    first_id = uuid4()
    second_id = uuid4()
    third_id = uuid4()

    await repository.start_idempotently(
        runtime_id=first_id,
        now=BASE_TIME,
    )
    await repository.start_idempotently(
        runtime_id=second_id,
        now=BASE_TIME + timedelta(seconds=1),
    )
    await repository.start_idempotently(
        runtime_id=third_id,
        now=BASE_TIME + timedelta(seconds=2),
    )

    await repository.heartbeat(
        runtime_id=first_id,
        now=BASE_TIME + timedelta(seconds=30),
        activity_state=WorkerRuntimeActivityState.ACTIVE,
    )

    runtimes = await repository.list_recent_runtimes(limit=2)

    assert tuple(runtime.runtime_id for runtime in runtimes) == (
        first_id,
        third_id,
    )

    events = await repository.list_recent_events(limit=2)

    assert tuple(event.runtime_id for event in events) == (
        third_id,
        second_id,
    )

    for invalid_limit in (0, -1, 101, True):
        with pytest.raises(InvalidWorkerRuntimeObservabilityError):
            await repository.list_recent_runtimes(
                limit=invalid_limit,
            )

        with pytest.raises(InvalidWorkerRuntimeObservabilityError):
            await repository.list_recent_events(
                limit=invalid_limit,
            )
