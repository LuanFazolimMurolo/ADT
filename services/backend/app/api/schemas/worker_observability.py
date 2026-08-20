"""Sanitized administrator worker-runtime observability contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Self
from uuid import UUID

from app.api.schemas.common import ApiSchema
from app.market_data.operations import MarketOperationState
from app.market_data.worker_observability import (
    WorkerRuntimeActivityState,
    WorkerRuntimeEventType,
    WorkerRuntimeFailureCode,
    WorkerRuntimeLifecycleState,
)
from app.services.worker_observability import (
    WorkerRuntimeEventListObservation,
    WorkerRuntimeEventObservation,
    WorkerRuntimeHealthState,
    WorkerRuntimeListObservation,
    WorkerRuntimeObservation,
)


class WorkerRuntimeResponse(ApiSchema):
    """One sanitized runtime observation without the internal runtime UUID."""

    health_state: WorkerRuntimeHealthState
    lifecycle_state: WorkerRuntimeLifecycleState
    activity_state: WorkerRuntimeActivityState
    started_at: datetime
    heartbeat_at: datetime
    stopped_at: datetime | None
    failure_code: WorkerRuntimeFailureCode | None

    @classmethod
    def from_domain(
        cls,
        observation: WorkerRuntimeObservation,
    ) -> Self:
        return cls(
            health_state=observation.health_state,
            lifecycle_state=observation.lifecycle_state,
            activity_state=observation.activity_state,
            started_at=observation.started_at,
            heartbeat_at=observation.heartbeat_at,
            stopped_at=observation.stopped_at,
            failure_code=observation.failure_code,
        )


class WorkerRuntimeListResponse(ApiSchema):
    """Bounded recent runtime observations at one server instant."""

    observed_at: datetime
    stale_after_seconds: int
    count: int
    items: list[WorkerRuntimeResponse]

    @classmethod
    def from_domain(
        cls,
        observation: WorkerRuntimeListObservation,
    ) -> Self:
        return cls(
            observed_at=observation.observed_at,
            stale_after_seconds=observation.stale_after_seconds,
            count=len(observation.items),
            items=[WorkerRuntimeResponse.from_domain(item) for item in observation.items],
        )


class WorkerRuntimeEventResponse(ApiSchema):
    """One sanitized event without the internal runtime UUID."""

    event_id: int
    event_type: WorkerRuntimeEventType
    occurred_at: datetime
    operation_id: UUID | None
    operation_state: MarketOperationState | None

    @classmethod
    def from_domain(
        cls,
        observation: WorkerRuntimeEventObservation,
    ) -> Self:
        return cls(
            event_id=observation.event_id,
            event_type=observation.event_type,
            occurred_at=observation.occurred_at,
            operation_id=observation.operation_id,
            operation_state=observation.operation_state,
        )


class WorkerRuntimeEventListResponse(ApiSchema):
    """Bounded recent sanitized worker operational events."""

    observed_at: datetime
    count: int
    items: list[WorkerRuntimeEventResponse]

    @classmethod
    def from_domain(
        cls,
        observation: WorkerRuntimeEventListObservation,
    ) -> Self:
        return cls(
            observed_at=observation.observed_at,
            count=len(observation.items),
            items=[WorkerRuntimeEventResponse.from_domain(item) for item in observation.items],
        )
