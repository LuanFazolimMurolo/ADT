"""Infrastructure-neutral worker-runtime observability ports."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.market_data.operations import MarketOperationState
from app.market_data.worker_observability import (
    WorkerRuntimeActivityState,
    WorkerRuntimeEvent,
    WorkerRuntimeFailureCode,
    WorkerRuntimeSnapshot,
)


class WorkerRuntimeObservabilityRepository(Protocol):
    """Durable worker presence and event contract with no database coupling."""

    async def start_idempotently(
        self,
        *,
        runtime_id: UUID,
        now: datetime,
    ) -> WorkerRuntimeSnapshot:
        """Create one runtime epoch and its STARTED event atomically."""
        ...

    async def get(
        self,
        runtime_id: UUID,
    ) -> WorkerRuntimeSnapshot | None: ...

    async def heartbeat(
        self,
        *,
        runtime_id: UUID,
        now: datetime,
        activity_state: WorkerRuntimeActivityState,
    ) -> WorkerRuntimeSnapshot: ...

    async def stop(
        self,
        *,
        runtime_id: UUID,
        now: datetime,
    ) -> WorkerRuntimeSnapshot:
        """Terminalize one runtime and append STOPPED atomically."""
        ...

    async def fail(
        self,
        *,
        runtime_id: UUID,
        now: datetime,
        failure_code: WorkerRuntimeFailureCode,
    ) -> WorkerRuntimeSnapshot:
        """Terminalize one runtime and append FAILED atomically."""
        ...

    async def record_operation_settled(
        self,
        *,
        runtime_id: UUID,
        operation_id: UUID,
        operation_state: MarketOperationState,
        now: datetime,
    ) -> WorkerRuntimeEvent: ...

    async def list_recent_runtimes(
        self,
        *,
        limit: int,
    ) -> tuple[WorkerRuntimeSnapshot, ...]: ...

    async def list_recent_events(
        self,
        *,
        limit: int,
    ) -> tuple[WorkerRuntimeEvent, ...]: ...
