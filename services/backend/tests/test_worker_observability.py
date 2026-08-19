"""Pure worker-runtime observability domain contract tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from uuid import UUID, uuid4

import pytest

from app.market_data.errors import InvalidWorkerRuntimeObservabilityError
from app.market_data.operations import MarketOperationState
from app.market_data.worker_observability import (
    TERMINAL_WORKER_RUNTIME_STATES,
    WorkerRuntimeActivityState,
    WorkerRuntimeEvent,
    WorkerRuntimeEventType,
    WorkerRuntimeFailureCode,
    WorkerRuntimeLifecycleState,
    WorkerRuntimeSnapshot,
)

BASE_TIME = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "activity_state",
    [
        WorkerRuntimeActivityState.IDLE,
        WorkerRuntimeActivityState.ACTIVE,
    ],
)
def test_running_runtime_accepts_idle_or_active(
    activity_state: WorkerRuntimeActivityState,
) -> None:
    runtime_id = uuid4()

    runtime = WorkerRuntimeSnapshot(
        runtime_id=runtime_id,
        lifecycle_state=WorkerRuntimeLifecycleState.RUNNING,
        activity_state=activity_state,
        started_at=BASE_TIME,
        heartbeat_at=BASE_TIME + timedelta(seconds=30),
    )

    assert runtime.runtime_id == runtime_id
    assert runtime.activity_state is activity_state
    assert runtime.stopped_at is None
    assert runtime.failure_code is None


@pytest.mark.parametrize(
    "runtime_id",
    [
        UUID(int=0),
        "not-a-uuid",
    ],
)
def test_runtime_rejects_invalid_identity(runtime_id: object) -> None:
    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        WorkerRuntimeSnapshot(
            runtime_id=runtime_id,  # type: ignore[arg-type]
            lifecycle_state=WorkerRuntimeLifecycleState.RUNNING,
            activity_state=WorkerRuntimeActivityState.IDLE,
            started_at=BASE_TIME,
            heartbeat_at=BASE_TIME,
        )


@pytest.mark.parametrize(
    "invalid_time",
    [
        datetime(2026, 8, 19, 12, 0),
        datetime(
            2026,
            8,
            19,
            12,
            0,
            tzinfo=timezone(timedelta(hours=1)),
        ),
    ],
)
def test_runtime_rejects_non_utc_timestamps(
    invalid_time: datetime,
) -> None:
    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        WorkerRuntimeSnapshot(
            runtime_id=uuid4(),
            lifecycle_state=WorkerRuntimeLifecycleState.RUNNING,
            activity_state=WorkerRuntimeActivityState.IDLE,
            started_at=invalid_time,
            heartbeat_at=invalid_time,
        )


def test_runtime_rejects_heartbeat_before_start() -> None:
    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        WorkerRuntimeSnapshot(
            runtime_id=uuid4(),
            lifecycle_state=WorkerRuntimeLifecycleState.RUNNING,
            activity_state=WorkerRuntimeActivityState.IDLE,
            started_at=BASE_TIME,
            heartbeat_at=BASE_TIME - timedelta(seconds=1),
        )


def test_stopped_runtime_requires_idle_terminal_shape() -> None:
    stopped_at = BASE_TIME + timedelta(minutes=1)

    runtime = WorkerRuntimeSnapshot(
        runtime_id=uuid4(),
        lifecycle_state=WorkerRuntimeLifecycleState.STOPPED,
        activity_state=WorkerRuntimeActivityState.IDLE,
        started_at=BASE_TIME,
        heartbeat_at=stopped_at,
        stopped_at=stopped_at,
    )

    assert runtime.lifecycle_state in TERMINAL_WORKER_RUNTIME_STATES
    assert runtime.failure_code is None

    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        WorkerRuntimeSnapshot(
            runtime_id=uuid4(),
            lifecycle_state=WorkerRuntimeLifecycleState.STOPPED,
            activity_state=WorkerRuntimeActivityState.ACTIVE,
            started_at=BASE_TIME,
            heartbeat_at=stopped_at,
            stopped_at=stopped_at,
        )


def test_failed_runtime_requires_closed_failure_code() -> None:
    failed_at = BASE_TIME + timedelta(minutes=1)

    runtime = WorkerRuntimeSnapshot(
        runtime_id=uuid4(),
        lifecycle_state=WorkerRuntimeLifecycleState.FAILED,
        activity_state=WorkerRuntimeActivityState.IDLE,
        started_at=BASE_TIME,
        heartbeat_at=failed_at,
        stopped_at=failed_at,
        failure_code=WorkerRuntimeFailureCode.DATABASE_FAILURE,
    )

    assert runtime.failure_code is WorkerRuntimeFailureCode.DATABASE_FAILURE

    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        WorkerRuntimeSnapshot(
            runtime_id=uuid4(),
            lifecycle_state=WorkerRuntimeLifecycleState.FAILED,
            activity_state=WorkerRuntimeActivityState.IDLE,
            started_at=BASE_TIME,
            heartbeat_at=failed_at,
            stopped_at=failed_at,
        )


def test_running_runtime_rejects_terminal_fields() -> None:
    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        WorkerRuntimeSnapshot(
            runtime_id=uuid4(),
            lifecycle_state=WorkerRuntimeLifecycleState.RUNNING,
            activity_state=WorkerRuntimeActivityState.IDLE,
            started_at=BASE_TIME,
            heartbeat_at=BASE_TIME,
            stopped_at=BASE_TIME,
        )

    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        WorkerRuntimeSnapshot(
            runtime_id=uuid4(),
            lifecycle_state=WorkerRuntimeLifecycleState.RUNNING,
            activity_state=WorkerRuntimeActivityState.IDLE,
            started_at=BASE_TIME,
            heartbeat_at=BASE_TIME,
            failure_code=WorkerRuntimeFailureCode.UNEXPECTED_FAILURE,
        )


def test_terminal_runtime_rejects_stop_before_heartbeat() -> None:
    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        WorkerRuntimeSnapshot(
            runtime_id=uuid4(),
            lifecycle_state=WorkerRuntimeLifecycleState.STOPPED,
            activity_state=WorkerRuntimeActivityState.IDLE,
            started_at=BASE_TIME,
            heartbeat_at=BASE_TIME + timedelta(seconds=2),
            stopped_at=BASE_TIME + timedelta(seconds=1),
        )


@pytest.mark.parametrize(
    "event_type",
    [
        WorkerRuntimeEventType.RUNTIME_STARTED,
        WorkerRuntimeEventType.RUNTIME_STOPPED,
        WorkerRuntimeEventType.RUNTIME_FAILED,
    ],
)
def test_runtime_events_have_no_operation_context(
    event_type: WorkerRuntimeEventType,
) -> None:
    event = WorkerRuntimeEvent(
        event_id=1,
        runtime_id=uuid4(),
        event_type=event_type,
        occurred_at=BASE_TIME,
    )

    assert event.operation_id is None
    assert event.operation_state is None

    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        WorkerRuntimeEvent(
            event_id=2,
            runtime_id=uuid4(),
            event_type=event_type,
            occurred_at=BASE_TIME,
            operation_id=uuid4(),
            operation_state=MarketOperationState.COMPLETED,
        )


@pytest.mark.parametrize(
    "operation_state",
    (
        MarketOperationState.PAUSED,
        MarketOperationState.CANCELLED,
        MarketOperationState.COMPLETED,
        MarketOperationState.FAILED,
    ),
)
def test_operation_settled_accepts_only_settled_states(
    operation_state: MarketOperationState,
) -> None:
    operation_id = uuid4()

    event = WorkerRuntimeEvent(
        event_id=1,
        runtime_id=uuid4(),
        event_type=WorkerRuntimeEventType.OPERATION_SETTLED,
        occurred_at=BASE_TIME,
        operation_id=operation_id,
        operation_state=operation_state,
    )

    assert event.operation_id == operation_id
    assert event.operation_state is operation_state


@pytest.mark.parametrize(
    "operation_state",
    [
        MarketOperationState.PENDING,
        MarketOperationState.CLAIMED,
        MarketOperationState.RUNNING,
        MarketOperationState.PAUSE_REQUESTED,
        MarketOperationState.CANCEL_REQUESTED,
        MarketOperationState.RECOVERING,
    ],
)
def test_operation_settled_rejects_non_settled_states(
    operation_state: MarketOperationState,
) -> None:
    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        WorkerRuntimeEvent(
            event_id=1,
            runtime_id=uuid4(),
            event_type=WorkerRuntimeEventType.OPERATION_SETTLED,
            occurred_at=BASE_TIME,
            operation_id=uuid4(),
            operation_state=operation_state,
        )


def test_operation_settled_requires_operation_identity() -> None:
    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        WorkerRuntimeEvent(
            event_id=1,
            runtime_id=uuid4(),
            event_type=WorkerRuntimeEventType.OPERATION_SETTLED,
            occurred_at=BASE_TIME,
            operation_state=MarketOperationState.COMPLETED,
        )


@pytest.mark.parametrize(
    ("event_id", "runtime_id"),
    [
        (0, uuid4()),
        (-1, uuid4()),
        (1, UUID(int=0)),
    ],
)
def test_event_rejects_invalid_persisted_identity(
    event_id: int,
    runtime_id: UUID,
) -> None:
    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        WorkerRuntimeEvent(
            event_id=event_id,
            runtime_id=runtime_id,
            event_type=WorkerRuntimeEventType.RUNTIME_STARTED,
            occurred_at=BASE_TIME,
        )


@pytest.mark.parametrize(
    ("enum_type", "invalid_value"),
    [
        (WorkerRuntimeLifecycleState, "DEAD"),
        (WorkerRuntimeActivityState, "BUSY"),
        (WorkerRuntimeFailureCode, "RAW_EXCEPTION"),
        (WorkerRuntimeEventType, "HEARTBEAT"),
    ],
)
def test_worker_observability_enums_are_closed(
    enum_type: type[StrEnum],
    invalid_value: str,
) -> None:
    with pytest.raises(ValueError):
        enum_type(invalid_value)
