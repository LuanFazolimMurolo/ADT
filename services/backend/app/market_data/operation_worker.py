"""Worker-side lease and cooperative-control bridge for market operations."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from app.market_data.operation_ports import MarketOperationRepository, OperationClock
from app.market_data.operations import (
    MarketOperationSnapshot,
    MarketOperationState,
    renew_lease,
)
from app.market_data.orchestration import BackfillControl


class MarketOperationWorkerSession:
    """Own one PostgreSQL market operation under an explicit renewable lease."""

    def __init__(
        self,
        *,
        repository: MarketOperationRepository,
        owner_id: UUID,
        clock: OperationClock,
        lease_duration: timedelta,
    ) -> None:
        if not isinstance(owner_id, UUID) or owner_id.int == 0:
            raise ValueError("owner_id must be a non-zero UUID")
        if not isinstance(lease_duration, timedelta) or lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")

        self._repository = repository
        self._owner_id = owner_id
        self._clock = clock
        self._lease_duration = lease_duration

    @property
    def owner_id(self) -> UUID:
        return self._owner_id

    async def claim_next(self) -> MarketOperationSnapshot | None:
        """Claim the oldest eligible operation using one bounded worker lease."""
        now = self._clock()
        return await self._repository.claim_next(
            owner_id=self._owner_id,
            now=now,
            lease_expires_at=now + self._lease_duration,
        )

    async def start(
        self,
        operation: MarketOperationSnapshot,
    ) -> MarketOperationSnapshot:
        """Move an owned claimed operation into RUNNING."""
        self._require_owned(operation)

        if operation.state is MarketOperationState.RUNNING:
            return operation

        if operation.state is not MarketOperationState.CLAIMED:
            raise ValueError("operation must be CLAIMED before start")

        return await self._repository.request_state(
            operation_id=operation.operation_id,
            target=MarketOperationState.RUNNING,
            expected_version=operation.record_version,
            now=self._clock(),
            owner_id=self._owner_id,
        )

    async def poll_control(
        self,
        operation: MarketOperationSnapshot,
    ) -> tuple[MarketOperationSnapshot, BackfillControl]:
        """Reload control state and renew the lease when work may continue."""
        self._require_same_operation(operation)

        current = await self._repository.get(operation.operation_id)
        if current is None:
            raise ValueError("claimed operation disappeared")

        self._require_owned(current)

        if current.state is MarketOperationState.PAUSE_REQUESTED:
            return current, BackfillControl.PAUSE

        if current.state is MarketOperationState.CANCEL_REQUESTED:
            return current, BackfillControl.CANCEL

        if current.state not in {
            MarketOperationState.CLAIMED,
            MarketOperationState.RUNNING,
        }:
            raise ValueError("operation is not executable")

        lease = current.lease
        assert lease is not None

        now = self._clock()
        renewed = renew_lease(
            lease,
            owner_id=self._owner_id,
            now=now,
            lease_expires_at=now + self._lease_duration,
        )

        current = await self._repository.renew_lease(
            operation_id=current.operation_id,
            owner_id=self._owner_id,
            now=now,
            lease=renewed,
            expected_version=current.record_version,
        )

        return current, BackfillControl.CONTINUE

    async def finish_pause(
        self,
        operation: MarketOperationSnapshot,
    ) -> MarketOperationSnapshot:
        """Acknowledge a cooperative pause and release active execution state."""
        self._require_owned(operation)

        if operation.state is MarketOperationState.PAUSED:
            return operation

        if operation.state is not MarketOperationState.PAUSE_REQUESTED:
            raise ValueError("operation is not awaiting pause acknowledgement")

        return await self._repository.request_state(
            operation_id=operation.operation_id,
            target=MarketOperationState.PAUSED,
            expected_version=operation.record_version,
            now=self._clock(),
            owner_id=self._owner_id,
        )

    async def finish_cancel(
        self,
        operation: MarketOperationSnapshot,
    ) -> MarketOperationSnapshot:
        """Acknowledge cooperative cancellation."""
        self._require_owned(operation)

        if operation.state is MarketOperationState.CANCELLED:
            return operation

        if operation.state is not MarketOperationState.CANCEL_REQUESTED:
            raise ValueError("operation is not awaiting cancellation")

        return await self._repository.request_state(
            operation_id=operation.operation_id,
            target=MarketOperationState.CANCELLED,
            expected_version=operation.record_version,
            now=self._clock(),
            owner_id=self._owner_id,
        )

    def _require_same_operation(
        self,
        operation: MarketOperationSnapshot,
    ) -> None:
        if not isinstance(operation, MarketOperationSnapshot):
            raise TypeError("operation must be a MarketOperationSnapshot")

    def _require_owned(
        self,
        operation: MarketOperationSnapshot,
    ) -> None:
        self._require_same_operation(operation)
        lease = operation.lease
        if lease is None or lease.owner_id != self._owner_id:
            raise ValueError("operation lease is not owned by this worker")
