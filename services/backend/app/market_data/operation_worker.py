"""Worker-side lease and cooperative-control bridge for market operations."""

from __future__ import annotations

import asyncio
from asyncio import Lock
from datetime import datetime, timedelta
from uuid import UUID

from app.market_data.errors import (
    InvalidMarketOperationRequestError,
    InvalidMarketResponseError,
    InvalidOperationLeaseError,
    MarketDataInconsistencyError,
    MarketDataStorageError,
    MarketDataUnavailableError,
    MarketJobLockTimeoutError,
    MarketJobNotFoundError,
    MarketOperationPlanConflictError,
    MarketRateLimitError,
    OperationVersionConflictError,
    UnknownInstrumentError,
    UnsupportedTimeframeError,
)
from app.market_data.jobs import MarketJobCatalog, MarketJobRecord
from app.market_data.operation_ports import MarketOperationRepository, OperationClock
from app.market_data.operations import (
    MarketOperationFailureCode,
    MarketOperationSnapshot,
    MarketOperationState,
    MarketOperationType,
    OperationProgress,
    OperationResult,
    SanitizedOperationFailure,
    renew_lease,
)
from app.market_data.orchestration import BackfillControl, BackfillExecutor
from app.market_data.planning import (
    BackfillChunk,
    BackfillPlan,
    BackfillResult,
    MarketDataPlanner,
    MarketJobStatus,
    MarketJobType,
    backfill_plan_checksum,
)
from app.market_data.services import HistoricalMarketDataService


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
        """Publish one durable local checkpoint using the latest owned version."""
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
            raise ValueError("operation does not accept checkpoint progress")

        now = self._clock()
        progress = _operation_progress_from_record(
            current,
            record,
            updated_at=now,
        )

        return await self._repository.update_progress(
            operation_id=current.operation_id,
            owner_id=self._owner_id,
            now=now,
            progress=progress,
            local_job_id=record.job_id,
            expected_version=current.record_version,
        )

    async def finish_success(
        self,
        operation: MarketOperationSnapshot,
        *,
        dataset_version: str,
        dataset_checksum: str,
    ) -> MarketOperationSnapshot:
        """Persist a sanitized successful terminal result."""
        self._require_owned(operation)
        now = self._clock()

        result = OperationResult(
            dataset_version=dataset_version,
            dataset_checksum=dataset_checksum,
            completed_at=now,
        )

        return await self._repository.complete(
            operation_id=operation.operation_id,
            owner_id=self._owner_id,
            now=now,
            result=result,
            progress=operation.progress,
            expected_version=operation.record_version,
        )

    async def finish_failure(
        self,
        operation: MarketOperationSnapshot,
        *,
        code: MarketOperationFailureCode,
    ) -> MarketOperationSnapshot:
        """Persist only the closed failure taxonomy, never arbitrary text."""
        self._require_owned(operation)
        now = self._clock()

        failure = SanitizedOperationFailure(
            code=code,
            failed_at=now,
        )

        return await self._repository.fail(
            operation_id=operation.operation_id,
            owner_id=self._owner_id,
            now=now,
            failure=failure,
            progress=operation.progress,
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


class MarketOperationWorker:
    """Execute at most one claimed market-data operation per iteration."""

    def __init__(
        self,
        *,
        session: MarketOperationWorkerSession,
        planner: MarketDataPlanner,
        executor: BackfillExecutor,
        jobs: MarketJobCatalog,
        history: HistoricalMarketDataService,
        clock: OperationClock,
        heartbeat_interval_seconds: float,
    ) -> None:
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")

        self._session = session
        self._planner = planner
        self._executor = executor
        self._jobs = jobs
        self._history = history
        self._clock = clock
        self._heartbeat_interval_seconds = heartbeat_interval_seconds

    async def run_once(self) -> MarketOperationSnapshot | None:
        """Claim and settle one operation without polling indefinitely."""
        self._jobs.recover_abandoned()

        claimed = await self._session.claim_next()
        if claimed is None:
            return None

        operation = await self._session.start(claimed)
        observer: MarketOperationExecutionObserver | None = None

        try:
            plan = build_operation_backfill_plan(
                operation,
                planner=self._planner,
                now=self._clock(),
            )

            observer = MarketOperationExecutionObserver(
                session=self._session,
                operation=operation,
            )

            execution = await self._execute_operation(
                operation,
                plan,
                observer,
            )

            return await self._settle_execution(
                observer.operation,
                plan,
                execution,
            )
        except (InvalidOperationLeaseError, OperationVersionConflictError):
            # Ownership/version loss cannot be safely rewritten by this worker.
            raise
        except Exception as error:
            current = observer.operation if observer is not None else operation
            return await self._settle_failure(current, error)

    async def _execute_operation(
        self,
        operation: MarketOperationSnapshot,
        plan: BackfillPlan,
        observer: MarketOperationExecutionObserver,
    ) -> BackfillResult:
        local = _local_job_or_none(self._jobs, plan.job_id)

        if local is None or local.status is MarketJobStatus.PLANNED:
            return await self._execute_with_heartbeat(
                plan,
                operation,
                observer,
                resume=False,
            )

        _require_bound_record(operation, local)

        if local.status in {
            MarketJobStatus.PAUSED,
            MarketJobStatus.FAILED,
        }:
            return await self._execute_with_heartbeat(
                plan,
                operation,
                observer,
                resume=True,
            )

        if local.status is MarketJobStatus.COMPLETED:
            return _backfill_result_from_record(local)

        raise MarketDataInconsistencyError("O estado do job local não permite execução segura.")

    async def _execute_with_heartbeat(
        self,
        plan: BackfillPlan,
        operation: MarketOperationSnapshot,
        observer: MarketOperationExecutionObserver,
        *,
        resume: bool,
    ) -> BackfillResult:
        pair = operation.request.dataset.pair

        if resume:
            execution_task = asyncio.create_task(
                self._executor.resume(
                    plan.job_id,
                    pair,
                    observer=observer,
                )
            )
        else:
            execution_task = asyncio.create_task(
                self._executor.run(
                    plan,
                    pair,
                    observer=observer,
                )
            )

        stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(observer, stop))

        done, _pending = await asyncio.wait(
            {execution_task, heartbeat_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if execution_task in done:
            stop.set()
            await heartbeat_task
            return execution_task.result()

        execution_task.cancel()
        await asyncio.gather(
            execution_task,
            return_exceptions=True,
        )

        # result() intentionally re-raises the heartbeat ownership failure.
        heartbeat_task.result()
        raise AssertionError("heartbeat task completed without a result")

    async def _heartbeat_loop(
        self,
        observer: MarketOperationExecutionObserver,
        stop: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self._heartbeat_interval_seconds,
                )
            except TimeoutError:
                await observer.heartbeat()

    async def _settle_execution(
        self,
        operation: MarketOperationSnapshot,
        plan: BackfillPlan,
        execution: BackfillResult,
    ) -> MarketOperationSnapshot:
        if execution.status in {
            MarketJobStatus.PAUSED,
            MarketJobStatus.CANCELLED,
        }:
            return await self._settle_interruption(
                operation,
                execution.status,
            )

        if execution.status is not MarketJobStatus.COMPLETED:
            raise MarketDataInconsistencyError("O executor retornou um estado local não terminal.")

        record = self._jobs.get(plan.job_id)
        _require_bound_record(operation, record)

        if record.status is not MarketJobStatus.COMPLETED:
            raise MarketDataInconsistencyError("O resultado COMPLETED divergiu do catálogo local.")

        current = await self._session.checkpoint(
            operation,
            record,
        )

        # Final chunk completion is another safe control boundary.
        current, control = await self._session.poll_control(current)

        if control is BackfillControl.PAUSE:
            return await self._session.finish_pause(current)

        if control is BackfillControl.CANCEL:
            return await self._session.finish_cancel(current)

        last_chunk_index = len(plan.chunks) - 1
        receipt = self._history.get_chunk_receipt(
            plan.job_id,
            last_chunk_index,
        )

        if receipt is None:
            raise MarketDataInconsistencyError("O receipt durável final não foi encontrado.")

        if (
            receipt.job_id != plan.job_id
            or receipt.chunk_index != last_chunk_index
            or receipt.dataset_key != plan.dataset_key
        ):
            raise MarketDataInconsistencyError("O receipt final divergiu da operação.")

        return await self._session.finish_success(
            current,
            dataset_version=receipt.version,
            dataset_checksum=receipt.checksum,
        )

    async def _settle_interruption(
        self,
        operation: MarketOperationSnapshot,
        status: MarketJobStatus,
    ) -> MarketOperationSnapshot:
        current, control = await self._session.poll_control(operation)

        if status is MarketJobStatus.PAUSED and control is BackfillControl.PAUSE:
            return await self._session.finish_pause(current)

        if status is MarketJobStatus.CANCELLED and control is BackfillControl.CANCEL:
            return await self._session.finish_cancel(current)

        raise MarketDataInconsistencyError(
            "O estado local interrompido divergiu do controle persistido."
        )

    async def _settle_failure(
        self,
        operation: MarketOperationSnapshot,
        error: Exception,
    ) -> MarketOperationSnapshot:
        current, control = await self._session.poll_control(operation)

        if control is BackfillControl.PAUSE:
            return await self._session.finish_pause(current)

        if control is BackfillControl.CANCEL:
            return await self._session.finish_cancel(current)

        return await self._session.finish_failure(
            current,
            code=_operation_failure_code(error),
        )


def _local_job_or_none(
    jobs: MarketJobCatalog,
    job_id: str,
) -> MarketJobRecord | None:
    try:
        return jobs.get(job_id)
    except MarketJobNotFoundError:
        return None


def _backfill_result_from_record(
    record: MarketJobRecord,
) -> BackfillResult:
    return BackfillResult(
        job_id=record.job_id,
        status=record.status,
        chunks_completed=record.chunks_completed,
        total_chunks=len(record.chunk_ranges),
        fetched_count=record.candles_fetched,
        stored_count=record.candles_stored,
        duplicate_count=record.duplicates,
        request_count=record.request_count,
    )


def _operation_failure_code(
    error: Exception,
) -> MarketOperationFailureCode:
    if isinstance(error, MarketOperationPlanConflictError):
        return MarketOperationFailureCode.PLAN_CONFLICT

    if isinstance(
        error,
        (
            InvalidMarketOperationRequestError,
            UnknownInstrumentError,
            UnsupportedTimeframeError,
        ),
    ):
        return MarketOperationFailureCode.INVALID_REQUEST

    if isinstance(error, MarketJobLockTimeoutError):
        return MarketOperationFailureCode.DATASET_BUSY

    if isinstance(error, MarketRateLimitError):
        return MarketOperationFailureCode.RATE_LIMITED

    if isinstance(
        error,
        (
            InvalidMarketResponseError,
            MarketDataUnavailableError,
        ),
    ):
        return MarketOperationFailureCode.NETWORK_FAILURE

    if isinstance(
        error,
        (
            MarketDataInconsistencyError,
            MarketJobNotFoundError,
        ),
    ):
        return MarketOperationFailureCode.LOCAL_STATE_INVALID

    if isinstance(error, MarketDataStorageError):
        return MarketOperationFailureCode.INTERNAL_ERROR

    return MarketOperationFailureCode.INTERNAL_ERROR
