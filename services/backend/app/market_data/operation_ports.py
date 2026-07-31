"""Infrastructure-neutral Phase 2D operation ports."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.market_data.operations import (
    MarketOperationRequest,
    MarketOperationSnapshot,
    MarketOperationState,
    OperationPlanSummary,
    OperationProgress,
    OperationResult,
    SanitizedOperationFailure,
    WorkerLease,
)


class OperationClock(Protocol):
    """Injectable UTC clock."""

    def __call__(self) -> datetime: ...


class OperationIdGenerator(Protocol):
    """Injectable operation/worker identifier generator."""

    def __call__(self) -> UUID: ...


class MarketOperationRepository(Protocol):
    """Durable operational-control contract with no database coupling."""

    async def create_idempotently(
        self,
        *,
        operation_id: UUID,
        request: MarketOperationRequest,
        plan: OperationPlanSummary,
        now: datetime,
    ) -> MarketOperationSnapshot: ...

    async def get(self, operation_id: UUID) -> MarketOperationSnapshot | None: ...

    async def list(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[MarketOperationSnapshot, ...]: ...

    async def request_state(
        self,
        *,
        operation_id: UUID,
        target: MarketOperationState,
        expected_version: int,
        now: datetime,
    ) -> MarketOperationSnapshot: ...

    async def claim_next(
        self,
        *,
        owner_id: UUID,
        now: datetime,
        lease_expires_at: datetime,
    ) -> MarketOperationSnapshot | None: ...

    async def renew_lease(
        self,
        *,
        operation_id: UUID,
        lease: WorkerLease,
        expected_version: int,
    ) -> MarketOperationSnapshot: ...

    async def update_progress(
        self,
        *,
        operation_id: UUID,
        progress: OperationProgress,
        local_job_id: str | None,
        expected_version: int,
    ) -> MarketOperationSnapshot: ...

    async def complete(
        self,
        *,
        operation_id: UUID,
        result: OperationResult,
        progress: OperationProgress,
        expected_version: int,
    ) -> MarketOperationSnapshot: ...

    async def fail(
        self,
        *,
        operation_id: UUID,
        failure: SanitizedOperationFailure,
        progress: OperationProgress,
        expected_version: int,
    ) -> MarketOperationSnapshot: ...

    async def reconcile(
        self,
        *,
        operation: MarketOperationSnapshot,
        expected_version: int,
    ) -> MarketOperationSnapshot: ...


class MarketOperationExecutor(Protocol):
    """One-operation executor/recovery contract with no worker loop details."""

    async def execute(
        self,
        operation: MarketOperationSnapshot,
    ) -> MarketOperationSnapshot: ...

    async def recover(
        self,
        operation: MarketOperationSnapshot,
    ) -> MarketOperationSnapshot: ...
