"""Read-only application service for persistent worker observability."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from app.market_data.domain import require_utc
from app.market_data.errors import (
    InvalidWorkerRuntimeObservabilityError,
    MarketDataInconsistencyError,
)
from app.market_data.operations import MarketOperationState
from app.market_data.worker_observability import (
    WorkerRuntimeActivityState,
    WorkerRuntimeEvent,
    WorkerRuntimeEventType,
    WorkerRuntimeFailureCode,
    WorkerRuntimeLifecycleState,
    WorkerRuntimeSnapshot,
)
from app.market_data.worker_observability_ports import (
    WorkerRuntimeObservabilityRepository,
)

WorkerRuntimeObservabilityClock = Callable[[], datetime]

WORKER_RUNTIME_STALE_AFTER_SECONDS: Final = 120
WORKER_RUNTIME_READ_LIMIT_MAX: Final = 100

_WORKER_RUNTIME_STALE_AFTER: Final = timedelta(seconds=WORKER_RUNTIME_STALE_AFTER_SECONDS)


class WorkerRuntimeHealthState(StrEnum):
    """Derived presentation health; never a persisted fencing state."""

    HEALTHY = "HEALTHY"
    STALE = "STALE"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class WorkerRuntimeObservation:
    """Sanitized runtime observation with no internal runtime identifier."""

    health_state: WorkerRuntimeHealthState
    lifecycle_state: WorkerRuntimeLifecycleState
    activity_state: WorkerRuntimeActivityState
    started_at: datetime
    heartbeat_at: datetime
    stopped_at: datetime | None
    failure_code: WorkerRuntimeFailureCode | None


@dataclass(frozen=True, slots=True)
class WorkerRuntimeListObservation:
    """Bounded recent runtime view derived at one server instant."""

    observed_at: datetime
    stale_after_seconds: int
    items: tuple[WorkerRuntimeObservation, ...]


@dataclass(frozen=True, slots=True)
class WorkerRuntimeEventObservation:
    """Sanitized event view with the internal runtime UUID removed."""

    event_id: int
    event_type: WorkerRuntimeEventType
    occurred_at: datetime
    operation_id: UUID | None
    operation_state: MarketOperationState | None


@dataclass(frozen=True, slots=True)
class WorkerRuntimeEventListObservation:
    """Bounded recent operational-event view."""

    observed_at: datetime
    items: tuple[WorkerRuntimeEventObservation, ...]


class WorkerRuntimeObservabilityService:
    """Read worker presence without controlling or executing the worker."""

    def __init__(
        self,
        *,
        repository: WorkerRuntimeObservabilityRepository,
        clock: WorkerRuntimeObservabilityClock,
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def list_runtimes(
        self,
        *,
        limit: int,
    ) -> WorkerRuntimeListObservation:
        """Return bounded sanitized runtime health observations."""

        limit = _require_read_limit(limit)
        runtimes = await self._repository.list_recent_runtimes(limit=limit)
        observed_at = self._observed_at()

        return WorkerRuntimeListObservation(
            observed_at=observed_at,
            stale_after_seconds=(WORKER_RUNTIME_STALE_AFTER_SECONDS),
            items=tuple(
                _runtime_observation(
                    runtime,
                    observed_at=observed_at,
                )
                for runtime in runtimes
            ),
        )

    async def list_events(
        self,
        *,
        limit: int,
    ) -> WorkerRuntimeEventListObservation:
        """Return bounded sanitized runtime events."""

        limit = _require_read_limit(limit)
        events = await self._repository.list_recent_events(limit=limit)

        return WorkerRuntimeEventListObservation(
            observed_at=self._observed_at(),
            items=tuple(_event_observation(event) for event in events),
        )

    def _observed_at(self) -> datetime:
        value = self._clock()

        if not isinstance(value, datetime):
            raise InvalidWorkerRuntimeObservabilityError()

        try:
            return require_utc(
                value,
                field_name="worker_runtime_observed_at",
            )
        except MarketDataInconsistencyError:
            raise InvalidWorkerRuntimeObservabilityError() from None


def _require_read_limit(limit: int) -> int:
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= WORKER_RUNTIME_READ_LIMIT_MAX
    ):
        raise InvalidWorkerRuntimeObservabilityError()

    return limit


def _runtime_health(
    runtime: WorkerRuntimeSnapshot,
    *,
    observed_at: datetime,
) -> WorkerRuntimeHealthState:
    if runtime.lifecycle_state is WorkerRuntimeLifecycleState.STOPPED:
        return WorkerRuntimeHealthState.STOPPED

    if runtime.lifecycle_state is WorkerRuntimeLifecycleState.FAILED:
        return WorkerRuntimeHealthState.FAILED

    age = observed_at - runtime.heartbeat_at

    if age > _WORKER_RUNTIME_STALE_AFTER:
        return WorkerRuntimeHealthState.STALE

    return WorkerRuntimeHealthState.HEALTHY


def _runtime_observation(
    runtime: WorkerRuntimeSnapshot,
    *,
    observed_at: datetime,
) -> WorkerRuntimeObservation:
    return WorkerRuntimeObservation(
        health_state=_runtime_health(
            runtime,
            observed_at=observed_at,
        ),
        lifecycle_state=runtime.lifecycle_state,
        activity_state=runtime.activity_state,
        started_at=runtime.started_at,
        heartbeat_at=runtime.heartbeat_at,
        stopped_at=runtime.stopped_at,
        failure_code=runtime.failure_code,
    )


def _event_observation(
    event: WorkerRuntimeEvent,
) -> WorkerRuntimeEventObservation:
    return WorkerRuntimeEventObservation(
        event_id=event.event_id,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        operation_id=event.operation_id,
        operation_state=event.operation_state,
    )
