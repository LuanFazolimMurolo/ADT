"""Infrastructure-neutral Phase 2D operation ports."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.market_data.operations import (
    MarketOperationRecoveryClaim,
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
        state: MarketOperationState | None = None,
        requested_by: UUID | None = None,
        dataset_id: str | None = None,
    ) -> tuple[MarketOperationSnapshot, ...]: ...

    async def request_state(
        self,
        *,
        operation_id: UUID,
        target: MarketOperationState,
        expected_version: int,
        now: datetime,
        owner_id: UUID | None = None,
    ) -> MarketOperationSnapshot: ...

    async def claim_next(
        self,
        *,
        owner_id: UUID,
        now: datetime,
        lease_expires_at: datetime,
    ) -> MarketOperationSnapshot | None: ...

    async def claim_next_expired(
        self,
        *,
        owner_id: UUID,
        now: datetime,
        lease_expires_at: datetime,
    ) -> MarketOperationRecoveryClaim | None: ...

    async def settle_or_claim_next_unclaimed_control(
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
        owner_id: UUID,
        now: datetime,
        lease: WorkerLease,
        expected_version: int,
    ) -> MarketOperationSnapshot: ...

    async def update_progress(
        self,
        *,
        operation_id: UUID,
        owner_id: UUID,
        now: datetime,
        progress: OperationProgress,
        local_job_id: str | None,
        expected_version: int,
    ) -> MarketOperationSnapshot: ...

    async def complete(
        self,
        *,
        operation_id: UUID,
        owner_id: UUID,
        now: datetime,
        result: OperationResult,
        progress: OperationProgress,
        expected_version: int,
    ) -> MarketOperationSnapshot: ...

    async def fail(
        self,
        *,
        operation_id: UUID,
        owner_id: UUID,
        now: datetime,
        failure: SanitizedOperationFailure,
        progress: OperationProgress,
        expected_version: int,
    ) -> MarketOperationSnapshot: ...

    async def reconcile(
        self,
        *,
        operation: MarketOperationSnapshot,
        owner_id: UUID,
        now: datetime,
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
