"""Pure worker-runtime observability domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.market_data.domain import require_utc
from app.market_data.errors import (
    InvalidWorkerRuntimeObservabilityError,
    MarketDataInconsistencyError,
)
from app.market_data.operations import MarketOperationState


class WorkerRuntimeLifecycleState(StrEnum):
    """Persisted lifecycle state for one worker runtime epoch."""

    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class WorkerRuntimeActivityState(StrEnum):
    """Current coarse activity of one running worker runtime."""

    IDLE = "IDLE"
    ACTIVE = "ACTIVE"


class WorkerRuntimeFailureCode(StrEnum):
    """Closed sanitized runtime-level failure taxonomy."""

    DATABASE_FAILURE = "DATABASE_FAILURE"
    LOCAL_STATE_FAILURE = "LOCAL_STATE_FAILURE"
    UNEXPECTED_FAILURE = "UNEXPECTED_FAILURE"


class WorkerRuntimeEventType(StrEnum):
    """Closed append-only operational event taxonomy."""

    RUNTIME_STARTED = "RUNTIME_STARTED"
    RUNTIME_STOPPED = "RUNTIME_STOPPED"
    RUNTIME_FAILED = "RUNTIME_FAILED"
    OPERATION_SETTLED = "OPERATION_SETTLED"


TERMINAL_WORKER_RUNTIME_STATES: frozenset[WorkerRuntimeLifecycleState] = frozenset(
    {
        WorkerRuntimeLifecycleState.STOPPED,
        WorkerRuntimeLifecycleState.FAILED,
    }
)

SETTLED_OPERATION_STATES: frozenset[MarketOperationState] = frozenset(
    {
        MarketOperationState.PAUSED,
        MarketOperationState.CANCELLED,
        MarketOperationState.COMPLETED,
        MarketOperationState.FAILED,
    }
)


def _invalid() -> InvalidWorkerRuntimeObservabilityError:
    return InvalidWorkerRuntimeObservabilityError()


def _require_nonzero_uuid(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise _invalid()
    return value


def _require_worker_utc(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise _invalid()
    try:
        return require_utc(value, field_name=field_name)
    except MarketDataInconsistencyError:
        raise _invalid() from None


@dataclass(frozen=True, slots=True)
class WorkerRuntimeSnapshot:
    """One immutable observation of a persisted worker runtime epoch."""

    runtime_id: UUID
    lifecycle_state: WorkerRuntimeLifecycleState
    activity_state: WorkerRuntimeActivityState
    started_at: datetime
    heartbeat_at: datetime
    stopped_at: datetime | None = None
    failure_code: WorkerRuntimeFailureCode | None = None

    def __post_init__(self) -> None:
        runtime_id = _require_nonzero_uuid(self.runtime_id)

        if not isinstance(self.lifecycle_state, WorkerRuntimeLifecycleState):
            raise _invalid()
        if not isinstance(self.activity_state, WorkerRuntimeActivityState):
            raise _invalid()
        if self.failure_code is not None and not isinstance(
            self.failure_code,
            WorkerRuntimeFailureCode,
        ):
            raise _invalid()

        started_at = _require_worker_utc(
            self.started_at,
            field_name="worker_runtime.started_at",
        )
        heartbeat_at = _require_worker_utc(
            self.heartbeat_at,
            field_name="worker_runtime.heartbeat_at",
        )
        stopped_at = (
            None
            if self.stopped_at is None
            else _require_worker_utc(
                self.stopped_at,
                field_name="worker_runtime.stopped_at",
            )
        )

        if started_at > heartbeat_at:
            raise _invalid()

        if self.lifecycle_state is WorkerRuntimeLifecycleState.RUNNING:
            if stopped_at is not None or self.failure_code is not None:
                raise _invalid()

        elif self.lifecycle_state is WorkerRuntimeLifecycleState.STOPPED:
            if (
                stopped_at is None
                or heartbeat_at > stopped_at
                or self.activity_state is not WorkerRuntimeActivityState.IDLE
                or self.failure_code is not None
            ):
                raise _invalid()

        elif self.lifecycle_state is WorkerRuntimeLifecycleState.FAILED:
            if (
                stopped_at is None
                or heartbeat_at > stopped_at
                or self.activity_state is not WorkerRuntimeActivityState.IDLE
                or self.failure_code is None
            ):
                raise _invalid()

        object.__setattr__(self, "runtime_id", runtime_id)
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "heartbeat_at", heartbeat_at)
        object.__setattr__(self, "stopped_at", stopped_at)


@dataclass(frozen=True, slots=True)
class WorkerRuntimeEvent:
    """One sanitized append-only worker operational event."""

    event_id: int
    runtime_id: UUID
    event_type: WorkerRuntimeEventType
    occurred_at: datetime
    operation_id: UUID | None = None
    operation_state: MarketOperationState | None = None

    def __post_init__(self) -> None:
        if type(self.event_id) is not int or self.event_id <= 0:
            raise _invalid()

        runtime_id = _require_nonzero_uuid(self.runtime_id)

        if not isinstance(self.event_type, WorkerRuntimeEventType):
            raise _invalid()

        occurred_at = _require_worker_utc(
            self.occurred_at,
            field_name="worker_runtime_event.occurred_at",
        )

        operation_id = self.operation_id
        if operation_id is not None:
            operation_id = _require_nonzero_uuid(operation_id)

        operation_state = self.operation_state
        if operation_state is not None and not isinstance(
            operation_state,
            MarketOperationState,
        ):
            raise _invalid()

        if self.event_type is WorkerRuntimeEventType.OPERATION_SETTLED:
            if operation_id is None or operation_state not in SETTLED_OPERATION_STATES:
                raise _invalid()
        elif operation_id is not None or operation_state is not None:
            raise _invalid()

        object.__setattr__(self, "runtime_id", runtime_id)
        object.__setattr__(self, "occurred_at", occurred_at)
        object.__setattr__(self, "operation_id", operation_id)
