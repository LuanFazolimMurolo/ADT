"""Worker-side lease and cooperative-control bridge for market operations."""

from __future__ import annotations

from asyncio import Lock
from datetime import datetime, timedelta
from uuid import UUID

from app.market_data.errors import (
    InvalidMarketOperationRequestError,
    MarketDataInconsistencyError,
    MarketOperationPlanConflictError,
)
from app.market_data.jobs import MarketJobRecord
from app.market_data.operation_ports import MarketOperationRepository, OperationClock
from app.market_data.operations import (
    MarketOperationSnapshot,
    MarketOperationState,
    MarketOperationType,
    OperationProgress,
    renew_lease,
)
from app.market_data.orchestration import BackfillControl
from app.market_data.planning import (
    BackfillChunk,
    BackfillPlan,
    MarketDataPlanner,
    MarketJobType,
    backfill_plan_checksum,
)


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

    async def heartbeat(
        self,
        operation: MarketOperationSnapshot,
    ) -> MarketOperationSnapshot:
        """Renew ownership during an in-flight chunk without consuming control."""
        self._require_same_operation(operation)

        current = await self._repository.get(operation.operation_id)
        if current is None:
            raise ValueError("claimed operation disappeared")

        self._require_owned(current)

        if current.state not in {
            MarketOperationState.CLAIMED,
            MarketOperationState.RUNNING,
            MarketOperationState.PAUSE_REQUESTED,
            MarketOperationState.CANCEL_REQUESTED,
        }:
            raise ValueError("operation does not accept worker heartbeat")

        lease = current.lease
        assert lease is not None

        now = self._clock()
        renewed = renew_lease(
            lease,
            owner_id=self._owner_id,
            now=now,
            lease_expires_at=now + self._lease_duration,
        )

        return await self._repository.renew_lease(
            operation_id=current.operation_id,
            owner_id=self._owner_id,
            now=now,
            lease=renewed,
            expected_version=current.record_version,
        )

    async def checkpoint(
        self,
        operation: MarketOperationSnapshot,
        record: MarketJobRecord,
    ) -> MarketOperationSnapshot:
        """Publish one durable local checkpoint into PostgreSQL progress."""
        self._require_owned(operation)
        now = self._clock()
        progress = _operation_progress_from_record(
            operation,
            record,
            updated_at=now,
        )

        return await self._repository.update_progress(
            operation_id=operation.operation_id,
            owner_id=self._owner_id,
            now=now,
            progress=progress,
            local_job_id=record.job_id,
            expected_version=operation.record_version,
        )

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


class MarketOperationExecutionObserver:
    """Serialize executor boundaries, checkpoints and lease heartbeats."""

    def __init__(
        self,
        *,
        session: MarketOperationWorkerSession,
        operation: MarketOperationSnapshot,
    ) -> None:
        if not isinstance(session, MarketOperationWorkerSession):
            raise TypeError("session must be a MarketOperationWorkerSession")
        session._require_owned(operation)
        self._session = session
        self._operation = operation
        self._lock = Lock()

    @property
    def operation(self) -> MarketOperationSnapshot:
        return self._operation

    async def before_chunk(
        self,
        record: MarketJobRecord,
        chunk: BackfillChunk,
    ) -> BackfillControl:
        """Observe administrator control only at a safe executor boundary."""
        _require_bound_record(self._operation, record)

        if record.next_chunk_index != chunk.index:
            raise MarketDataInconsistencyError("O boundary local divergiu do próximo chunk.")

        async with self._lock:
            current, control = await self._session.poll_control(self._operation)
            self._operation = current
            return control

    async def after_checkpoint(
        self,
        record: MarketJobRecord,
        chunk: BackfillChunk,
    ) -> None:
        """Mirror one already-durable local checkpoint into PostgreSQL."""
        _require_bound_record(self._operation, record)

        if record.chunks_completed != chunk.index + 1:
            raise MarketDataInconsistencyError(
                "O checkpoint confirmado divergiu do chunk executado."
            )

        async with self._lock:
            self._operation = await self._session.checkpoint(
                self._operation,
                record,
            )

    async def heartbeat(self) -> MarketOperationSnapshot:
        """Renew the lease while a chunk is in flight.

        PAUSE_REQUESTED and CANCEL_REQUESTED deliberately keep their lease here.
        They are consumed only by ``before_chunk`` at the next safe boundary.
        """
        async with self._lock:
            self._operation = await self._session.heartbeat(self._operation)
            return self._operation


def build_operation_backfill_plan(
    operation: MarketOperationSnapshot,
    *,
    planner: MarketDataPlanner,
    now: datetime,
) -> BackfillPlan:
    """Reconstruct the immutable submitted plan with a deterministic local job id."""
    if not isinstance(operation, MarketOperationSnapshot):
        raise InvalidMarketOperationRequestError()

    request = operation.request
    job_type = _operation_job_type(request.operation_type)
    local_job_id = str(operation.operation_id)

    if operation.local_job_id is not None and operation.local_job_id != local_job_id:
        raise MarketDataInconsistencyError("A operação já está vinculada a outro job local.")

    plan = planner.backfill(
        request.dataset.canonical_key,
        request.dataset.timeframe,
        request.data_range,
        job_type=job_type,
        job_id=local_job_id,
        latest_closed_at=now,
    )

    checksum = backfill_plan_checksum(plan)

    if (
        checksum != request.plan_checksum
        or checksum != operation.plan.checksum
        or len(plan.chunks) != operation.plan.chunks_planned
        or plan.expected_candles != operation.plan.estimated_candles
        or len(plan.chunks) != operation.plan.estimated_requests
    ):
        raise MarketOperationPlanConflictError()

    return plan


def _operation_job_type(
    operation_type: MarketOperationType,
) -> MarketJobType:
    if operation_type is MarketOperationType.RAW_BACKFILL:
        return MarketJobType.BACKFILL
    if operation_type is MarketOperationType.RAW_INCREMENTAL_UPDATE:
        return MarketJobType.INCREMENTAL
    raise InvalidMarketOperationRequestError()


def _require_bound_record(
    operation: MarketOperationSnapshot,
    record: MarketJobRecord,
) -> None:
    expected_job_id = operation.local_job_id or str(operation.operation_id)

    if (
        record.job_id != expected_job_id
        or record.dataset_key != operation.request.dataset.canonical_key
        or record.timeframe != operation.request.dataset.timeframe.code
        or record.job_type is not _operation_job_type(operation.request.operation_type)
        or len(record.chunk_ranges) != operation.plan.chunks_planned
        or record.candles_expected != operation.plan.estimated_candles
    ):
        raise MarketDataInconsistencyError("O checkpoint local divergiu da operação persistida.")

    if record.plan_checksum != operation.plan.checksum:
        raise MarketOperationPlanConflictError()


def _operation_progress_from_record(
    operation: MarketOperationSnapshot,
    record: MarketJobRecord,
    *,
    updated_at: datetime,
) -> OperationProgress:
    _require_bound_record(operation, record)

    return OperationProgress(
        chunks_planned=operation.plan.chunks_planned,
        chunks_completed=record.chunks_completed,
        chunks_failed=operation.progress.chunks_failed,
        candles_estimated=operation.plan.estimated_candles,
        candles_received=record.candles_fetched,
        candles_persisted=record.candles_stored,
        requests_completed=record.request_count,
        updated_at=updated_at,
    )
