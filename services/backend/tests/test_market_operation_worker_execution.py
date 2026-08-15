"""Phase 7-01D2B2A market-operation run-once worker tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from app.market_data.catalog import ChunkCommitReceipt, ChunkOperationContext
from app.market_data.errors import (
    InvalidOperationLeaseError,
    MarketJobNotFoundError,
    MarketRateLimitError,
)
from app.market_data.jobs import MarketJobCatalog, MarketJobRecord
from app.market_data.locks import DatasetLockManager
from app.market_data.operation_worker import (
    MarketOperationWorker,
    MarketOperationWorkerSession,
)
from app.market_data.operations import (
    MarketOperationFailureCode,
    MarketOperationRecoveryClaim,
    MarketOperationSnapshot,
    MarketOperationState,
    OperationProgress,
    OperationResult,
    SanitizedOperationFailure,
    require_transition,
    validate_operation_update,
)
from app.market_data.orchestration import (
    BackfillExecutionObserver,
    BackfillExecutor,
)
from app.market_data.planning import (
    BackfillPlan,
    BackfillResult,
    MarketDataPlanner,
    MarketJobStatus,
)
from app.market_data.services import HistoricalMarketDataService
from tests.market_data_helpers import PAIR
from tests.test_market_data_phase2b import RangeAdapter, _service
from tests.test_market_operation_worker_control import (
    END,
    NOW,
    OPERATION_ID,
    OWNER,
    START,
    FakeRepository,
    ProgressFakeRepository,
    StaticPlanner,
    _job_record,
    _operation_for_plan,
    _source_plan,
)


class TickClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        self.current += timedelta(seconds=1)
        return self.current


class TerminalRepository(ProgressFakeRepository):
    def __init__(
        self,
        operation: MarketOperationSnapshot,
        *,
        recovery_claim: MarketOperationRecoveryClaim | None = None,
        renew_error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        super().__init__(operation)
        self.recovery_claim = recovery_claim
        self.renew_error = renew_error
        self.events = events
        self.complete_calls = 0
        self.fail_calls = 0

    async def complete(
        self,
        *,
        operation_id: UUID,
        owner_id: UUID,
        now: datetime,
        result: OperationResult,
        progress: OperationProgress,
        expected_version: int,
    ) -> MarketOperationSnapshot:
        assert operation_id == OPERATION_ID
        assert owner_id == OWNER
        assert self.operation is not None
        assert expected_version == self.operation.record_version
        assert self.operation.lease is not None
        assert self.operation.lease.owner_id == owner_id
        require_transition(self.operation.state, MarketOperationState.COMPLETED)
        self.complete_calls += 1

        completed = replace(
            self.operation,
            state=MarketOperationState.COMPLETED,
            progress=progress,
            lease=None,
            result=result,
            failure=None,
            finished_at=now,
            updated_at=now,
            record_version=self.operation.record_version + 1,
        )
        validate_operation_update(self.operation, completed)
        self.operation = completed
        return completed

    async def fail(
        self,
        *,
        operation_id: UUID,
        owner_id: UUID,
        now: datetime,
        failure: SanitizedOperationFailure,
        progress: OperationProgress,
        expected_version: int,
    ) -> MarketOperationSnapshot:
        assert operation_id == OPERATION_ID
        assert owner_id == OWNER
        assert self.operation is not None
        assert expected_version == self.operation.record_version
        assert self.operation.lease is not None
        assert self.operation.lease.owner_id == owner_id
        require_transition(self.operation.state, MarketOperationState.FAILED)
        self.fail_calls += 1

        failed = replace(
            self.operation,
            state=MarketOperationState.FAILED,
            progress=progress,
            lease=None,
            result=None,
            failure=failure,
            finished_at=now,
            updated_at=now,
            record_version=self.operation.record_version + 1,
        )
        validate_operation_update(self.operation, failed)
        self.operation = failed
        return failed


class EmptyClaimRepository(FakeRepository):
    def __init__(self) -> None:
        super().__init__(None)


class FakeJobs:
    def __init__(
        self,
        record: MarketJobRecord | None = None,
        *,
        events: list[str] | None = None,
    ) -> None:
        self.record = record
        self.events = events
        self.recoveries = 0
        self.cancel_calls = 0

    def recover_abandoned(self) -> int:
        if self.events is not None:
            self.events.append("recover_abandoned")
        self.recoveries += 1
        return 0

    def get(self, job_id: str) -> MarketJobRecord:
        if self.record is None:
            raise MarketJobNotFoundError()
        assert self.record.job_id == job_id
        return self.record

    def cancel(self, job_id: str) -> MarketJobRecord:
        record = self.get(job_id)
        assert record.status in {
            MarketJobStatus.PLANNED,
            MarketJobStatus.RUNNING,
            MarketJobStatus.PAUSED,
            MarketJobStatus.FAILED,
        }
        self.cancel_calls += 1
        self.record = replace(
            record,
            status=MarketJobStatus.CANCELLED,
            updated_at=NOW.isoformat(),
            finished_at=NOW.isoformat(),
        )
        return self.record


class FakeHistory:
    def __init__(
        self,
        receipt: ChunkCommitReceipt | None,
    ) -> None:
        self.receipt = receipt
        self.calls: list[tuple[str, int]] = []

    def get_chunk_receipt(
        self,
        job_id: str,
        chunk_index: int,
    ) -> ChunkCommitReceipt | None:
        self.calls.append((job_id, chunk_index))
        return self.receipt


class FakeExecutor:
    def __init__(
        self,
        *,
        jobs: FakeJobs,
        terminal_record: MarketJobRecord | None = None,
        error: Exception | None = None,
        delay_seconds: float = 0,
        repository: TerminalRepository | None = None,
        control_request: MarketOperationState | None = None,
        clock: TickClock | None = None,
    ) -> None:
        self.jobs = jobs
        self.terminal_record = terminal_record
        self.error = error
        self.delay_seconds = delay_seconds
        self.repository = repository
        self.control_request = control_request
        self.clock = clock
        self.run_calls = 0
        self.resume_calls = 0
        self.cancelled = False
        self.cancel_awaited = False

    async def run(
        self,
        plan: BackfillPlan,
        pair: object,
        *,
        dry_run: bool = False,
        observer: BackfillExecutionObserver | None = None,
    ) -> BackfillResult:
        assert not dry_run
        assert observer is not None
        self.run_calls += 1
        return await self._execute()

    async def resume(
        self,
        job_id: str,
        pair: object,
        *,
        observer: BackfillExecutionObserver | None = None,
    ) -> BackfillResult:
        assert job_id == str(OPERATION_ID)
        assert observer is not None
        self.resume_calls += 1
        return await self._execute()

    async def _execute(self) -> BackfillResult:
        try:
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
        except asyncio.CancelledError:
            self.cancelled = True
            await asyncio.sleep(0)
            self.cancel_awaited = True
            raise

        if self.control_request is not None:
            assert self.repository is not None
            assert self.repository.operation is not None
            assert self.clock is not None

            current = self.repository.operation
            require_transition(current.state, self.control_request)
            controlled = replace(
                current,
                state=self.control_request,
                updated_at=self.clock(),
                record_version=current.record_version + 1,
            )
            validate_operation_update(current, controlled)
            self.repository.operation = controlled

        if self.error is not None:
            raise self.error

        assert self.terminal_record is not None
        self.jobs.record = self.terminal_record
        return _result(self.terminal_record)


def _record(
    operation: MarketOperationSnapshot,
    status: MarketJobStatus,
) -> MarketJobRecord:
    completed = (
        2 if status is MarketJobStatus.COMPLETED else 0 if status is MarketJobStatus.PLANNED else 1
    )

    record = _job_record(
        status=status,
        chunks_completed=completed,
        fetched=completed,
        stored=completed,
        requests=completed,
    )

    return replace(
        record,
        plan_checksum=operation.plan.checksum,
        finished_at=(
            NOW.isoformat()
            if status
            in {
                MarketJobStatus.COMPLETED,
                MarketJobStatus.FAILED,
                MarketJobStatus.CANCELLED,
            }
            else None
        ),
        error_code=("job_failed" if status is MarketJobStatus.FAILED else None),
    )


def _recovery_claim(
    plan: BackfillPlan,
    *,
    recovered_from: MarketOperationState,
    bind_local: bool,
) -> MarketOperationRecoveryClaim:
    operation = replace(
        _operation_for_plan(plan),
        state=MarketOperationState.RECOVERING,
        local_job_id=str(OPERATION_ID) if bind_local else None,
        record_version=7,
    )
    return MarketOperationRecoveryClaim(
        operation=operation,
        recovered_from=recovered_from,
    )


def _result(record: MarketJobRecord) -> BackfillResult:
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


def _receipt(
    operation: MarketOperationSnapshot,
) -> ChunkCommitReceipt:
    return ChunkCommitReceipt(
        job_id=str(OPERATION_ID),
        chunk_index=1,
        dataset_key=operation.request.dataset.canonical_key,
        start=(START + timedelta(hours=1)).isoformat(),
        end=END.isoformat(),
        fetched_count=1,
        stored_count=1,
        duplicate_count=0,
        request_count=1,
        version="c" * 64,
        checksum="d" * 64,
        committed_at=NOW.isoformat(),
    )


def _session(
    repository: FakeRepository,
    clock: TickClock,
) -> MarketOperationWorkerSession:
    return MarketOperationWorkerSession(
        repository=cast(object, repository),  # type: ignore[arg-type]
        owner_id=OWNER,
        clock=clock,
        lease_duration=timedelta(minutes=2),
    )


def _worker(
    *,
    operation: MarketOperationSnapshot,
    repository: TerminalRepository,
    jobs: FakeJobs,
    executor: FakeExecutor,
    history: FakeHistory,
    clock: TickClock,
    heartbeat_interval_seconds: float = 60,
) -> MarketOperationWorker:
    source = _source_plan()
    planner = StaticPlanner(source)

    return MarketOperationWorker(
        session=_session(repository, clock),
        planner=cast(MarketDataPlanner, planner),
        executor=cast(BackfillExecutor, executor),
        jobs=cast(MarketJobCatalog, jobs),
        history=cast(HistoricalMarketDataService, history),
        clock=clock,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )


@pytest.mark.asyncio
async def test_run_once_returns_none_when_queue_is_empty() -> None:
    clock = TickClock(NOW)
    repository = EmptyClaimRepository()
    session = _session(repository, clock)

    source = _source_plan()
    jobs = FakeJobs()
    executor = FakeExecutor(jobs=jobs)
    history = FakeHistory(None)

    worker = MarketOperationWorker(
        session=session,
        planner=cast(MarketDataPlanner, StaticPlanner(source)),
        executor=cast(BackfillExecutor, executor),
        jobs=cast(MarketJobCatalog, jobs),
        history=cast(HistoricalMarketDataService, history),
        clock=clock,
        heartbeat_interval_seconds=60,
    )

    assert await worker.run_once() is None
    assert jobs.recoveries == 1
    assert executor.run_calls == 0
    assert executor.resume_calls == 0


@pytest.mark.asyncio
async def test_run_once_completes_new_operation_from_final_durable_receipt() -> None:
    source = _source_plan()
    operation = _operation_for_plan(source)
    clock = TickClock(NOW)
    repository = TerminalRepository(operation)
    jobs = FakeJobs()
    completed = _record(operation, MarketJobStatus.COMPLETED)
    executor = FakeExecutor(
        jobs=jobs,
        terminal_record=completed,
    )
    history = FakeHistory(_receipt(operation))

    result = await _worker(
        operation=operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=history,
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.state is MarketOperationState.COMPLETED
    assert result.result is not None
    assert result.result.dataset_version == "c" * 64
    assert result.result.dataset_checksum == "d" * 64
    assert result.progress.chunks_completed == 2
    assert result.local_job_id == str(OPERATION_ID)
    assert result.lease is None
    assert repository.complete_calls == 1
    assert executor.run_calls == 1
    assert executor.resume_calls == 0


@pytest.mark.asyncio
async def test_run_once_resumes_existing_failed_local_job() -> None:
    source = _source_plan()
    operation = replace(
        _operation_for_plan(source),
        local_job_id=str(OPERATION_ID),
    )
    clock = TickClock(NOW)
    repository = TerminalRepository(operation)
    jobs = FakeJobs(_record(operation, MarketJobStatus.FAILED))
    completed = _record(operation, MarketJobStatus.COMPLETED)
    executor = FakeExecutor(
        jobs=jobs,
        terminal_record=completed,
    )
    history = FakeHistory(_receipt(operation))

    result = await _worker(
        operation=operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=history,
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.state is MarketOperationState.COMPLETED
    assert executor.run_calls == 0
    assert executor.resume_calls == 1


@pytest.mark.asyncio
async def test_run_once_finalizes_local_completed_crash_window_without_refetch() -> None:
    source = _source_plan()
    operation = replace(
        _operation_for_plan(source),
        local_job_id=str(OPERATION_ID),
    )
    clock = TickClock(NOW)
    repository = TerminalRepository(operation)
    jobs = FakeJobs(_record(operation, MarketJobStatus.COMPLETED))
    executor = FakeExecutor(jobs=jobs)
    history = FakeHistory(_receipt(operation))

    result = await _worker(
        operation=operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=history,
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.state is MarketOperationState.COMPLETED
    assert executor.run_calls == 0
    assert executor.resume_calls == 0
    assert history.calls == [(str(OPERATION_ID), 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested", "local_status", "expected"),
    [
        (
            MarketOperationState.PAUSE_REQUESTED,
            MarketJobStatus.PAUSED,
            MarketOperationState.PAUSED,
        ),
        (
            MarketOperationState.CANCEL_REQUESTED,
            MarketJobStatus.CANCELLED,
            MarketOperationState.CANCELLED,
        ),
    ],
)
async def test_run_once_honors_admin_control_at_safe_boundary(
    requested: MarketOperationState,
    local_status: MarketJobStatus,
    expected: MarketOperationState,
) -> None:
    source = _source_plan()
    operation = _operation_for_plan(source)
    clock = TickClock(NOW)
    repository = TerminalRepository(operation)
    jobs = FakeJobs()
    interrupted = _record(operation, local_status)
    executor = FakeExecutor(
        jobs=jobs,
        terminal_record=interrupted,
        repository=repository,
        control_request=requested,
        clock=clock,
    )
    history = FakeHistory(None)

    result = await _worker(
        operation=operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=history,
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.state is expected
    assert result.lease is None
    assert repository.complete_calls == 0
    assert repository.fail_calls == 0


@pytest.mark.asyncio
async def test_run_once_sanitizes_rate_limit_failure() -> None:
    source = _source_plan()
    operation = _operation_for_plan(source)
    clock = TickClock(NOW)
    repository = TerminalRepository(operation)
    jobs = FakeJobs()
    executor = FakeExecutor(
        jobs=jobs,
        error=MarketRateLimitError(),
    )
    history = FakeHistory(None)

    result = await _worker(
        operation=operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=history,
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.state is MarketOperationState.FAILED
    assert result.failure is not None
    assert result.failure.code is MarketOperationFailureCode.RATE_LIMITED
    assert result.result is None
    assert result.lease is None
    assert repository.fail_calls == 1


@pytest.mark.asyncio
async def test_run_once_renews_lease_while_chunk_is_in_flight() -> None:
    source = _source_plan()
    operation = _operation_for_plan(source)
    clock = TickClock(NOW)
    repository = TerminalRepository(operation)
    jobs = FakeJobs()
    completed = _record(operation, MarketJobStatus.COMPLETED)
    executor = FakeExecutor(
        jobs=jobs,
        terminal_record=completed,
        delay_seconds=0.04,
    )
    history = FakeHistory(_receipt(operation))

    result = await _worker(
        operation=operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=history,
        clock=clock,
        heartbeat_interval_seconds=0.005,
    ).run_once()

    assert result is not None
    assert result.state is MarketOperationState.COMPLETED

    # At least one in-flight heartbeat plus the final safe-boundary renewal.
    assert len(repository.renew_calls) >= 2


@pytest.mark.asyncio
async def test_run_once_prioritizes_recovery_before_normal_claim() -> None:
    source = _source_plan()
    claim = _recovery_claim(
        source,
        recovered_from=MarketOperationState.CLAIMED,
        bind_local=False,
    )
    events: list[str] = []
    clock = TickClock(NOW)
    repository = TerminalRepository(
        claim.operation,
        recovery_claim=claim,
        events=events,
    )
    jobs = FakeJobs(events=events)
    executor = FakeExecutor(
        jobs=jobs,
        terminal_record=_record(claim.operation, MarketJobStatus.COMPLETED),
    )

    result = await _worker(
        operation=claim.operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=FakeHistory(_receipt(claim.operation)),
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.state is MarketOperationState.COMPLETED
    assert repository.recovery_claim_call is not None
    assert events[:2] == ["recover_abandoned", "claim_next_expired"]
    assert "claim_next" not in events
    assert executor.run_calls == 1
    assert executor.resume_calls == 0
    assert repository.reconcile_calls[0][1] == OWNER
    assert repository.reconcile_calls[0][3] == claim.operation.record_version


@pytest.mark.asyncio
async def test_claimed_recovery_rejects_existing_local_job_without_execution() -> None:
    source = _source_plan()
    claim = _recovery_claim(
        source,
        recovered_from=MarketOperationState.CLAIMED,
        bind_local=False,
    )
    events: list[str] = []
    clock = TickClock(NOW)
    repository = TerminalRepository(
        claim.operation,
        recovery_claim=claim,
        events=events,
    )
    jobs = FakeJobs(_record(claim.operation, MarketJobStatus.PLANNED), events=events)
    executor = FakeExecutor(jobs=jobs)

    result = await _worker(
        operation=claim.operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=FakeHistory(None),
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.state is MarketOperationState.FAILED
    assert result.failure is not None
    assert result.failure.code is MarketOperationFailureCode.LOCAL_STATE_INVALID
    assert result.progress == claim.operation.progress
    assert result.record_version == claim.operation.record_version + 1
    assert result.lease is None
    assert executor.run_calls == executor.resume_calls == 0
    assert repository.recovery_claim_call is not None
    assert "claim_next" not in events


@pytest.mark.asyncio
async def test_run_once_uses_normal_claim_when_recovery_is_absent() -> None:
    source = _source_plan()
    operation = _operation_for_plan(source)
    events: list[str] = []
    clock = TickClock(NOW)
    repository = TerminalRepository(operation, events=events)
    jobs = FakeJobs(events=events)
    executor = FakeExecutor(
        jobs=jobs,
        terminal_record=_record(operation, MarketJobStatus.COMPLETED),
    )

    result = await _worker(
        operation=operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=FakeHistory(_receipt(operation)),
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.state is MarketOperationState.COMPLETED
    assert events[:3] == ["recover_abandoned", "claim_next_expired", "claim_next"]
    assert repository.reconcile_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("local_status", "expected_run", "expected_resume"),
    [
        (MarketJobStatus.PLANNED, 1, 0),
        (MarketJobStatus.FAILED, 0, 1),
        (MarketJobStatus.PAUSED, 0, 1),
    ],
)
async def test_running_recovery_reuses_normal_execution_path(
    local_status: MarketJobStatus,
    expected_run: int,
    expected_resume: int,
) -> None:
    source = _source_plan()
    claim = _recovery_claim(
        source,
        recovered_from=MarketOperationState.RUNNING,
        bind_local=True,
    )
    clock = TickClock(NOW)
    repository = TerminalRepository(claim.operation, recovery_claim=claim)
    jobs = FakeJobs(_record(claim.operation, local_status))
    executor = FakeExecutor(
        jobs=jobs,
        terminal_record=_record(claim.operation, MarketJobStatus.COMPLETED),
    )

    result = await _worker(
        operation=claim.operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=FakeHistory(_receipt(claim.operation)),
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.state is MarketOperationState.COMPLETED
    assert result.record_version > claim.operation.record_version + 1
    assert executor.run_calls == expected_run
    assert executor.resume_calls == expected_resume
    reconciled = repository.reconcile_calls[0][0]
    assert reconciled.state is MarketOperationState.RUNNING
    assert reconciled.lease == claim.operation.lease
    assert reconciled.record_version == claim.operation.record_version + 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("local_status", "expected_state"),
    [
        (MarketJobStatus.COMPLETED, MarketOperationState.COMPLETED),
        (MarketJobStatus.CANCELLED, MarketOperationState.CANCELLED),
    ],
)
async def test_running_recovery_settles_terminal_local_evidence_without_executor(
    local_status: MarketJobStatus,
    expected_state: MarketOperationState,
) -> None:
    source = _source_plan()
    claim = _recovery_claim(
        source,
        recovered_from=MarketOperationState.RUNNING,
        bind_local=True,
    )
    clock = TickClock(NOW)
    repository = TerminalRepository(claim.operation, recovery_claim=claim)
    jobs = FakeJobs(_record(claim.operation, local_status))
    executor = FakeExecutor(jobs=jobs)
    history = FakeHistory(
        _receipt(claim.operation) if local_status is MarketJobStatus.COMPLETED else None
    )

    result = await _worker(
        operation=claim.operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=history,
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.state is expected_state
    assert result.lease is None
    assert result.record_version == claim.operation.record_version + 1
    assert executor.run_calls == 0
    assert executor.resume_calls == 0
    assert repository.complete_calls == 0
    assert repository.fail_calls == 0
    if expected_state is MarketOperationState.COMPLETED:
        assert result.result is not None
        assert history.calls == [(str(OPERATION_ID), 1)]
    else:
        assert result.failure is not None
        assert result.failure.code is MarketOperationFailureCode.CANCELLED_BY_ADMIN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("local_status", "expected_code"),
    [
        (MarketJobStatus.RUNNING, MarketOperationFailureCode.DATASET_BUSY),
        (None, MarketOperationFailureCode.LOCAL_STATE_INVALID),
    ],
)
async def test_running_recovery_fails_closed_for_busy_or_missing_local_state(
    local_status: MarketJobStatus | None,
    expected_code: MarketOperationFailureCode,
) -> None:
    source = _source_plan()
    claim = _recovery_claim(
        source,
        recovered_from=MarketOperationState.RUNNING,
        bind_local=local_status is not None,
    )
    clock = TickClock(NOW)
    repository = TerminalRepository(claim.operation, recovery_claim=claim)
    jobs = FakeJobs(None if local_status is None else _record(claim.operation, local_status))
    executor = FakeExecutor(jobs=jobs)

    result = await _worker(
        operation=claim.operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=FakeHistory(None),
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.state is MarketOperationState.FAILED
    assert result.failure is not None
    assert result.failure.code is expected_code
    assert result.progress == claim.operation.progress
    assert result.lease is None
    assert executor.run_calls == 0
    assert executor.resume_calls == 0


@pytest.mark.asyncio
async def test_running_recovery_with_real_flock_fails_dataset_busy_without_execution(
    tmp_path: Path,
) -> None:
    source = _source_plan()
    local_plan = replace(source, job_id=str(OPERATION_ID))
    claim = _recovery_claim(
        source,
        recovered_from=MarketOperationState.RUNNING,
        bind_local=True,
    )
    jobs = MarketJobCatalog(tmp_path, clock=lambda: NOW)
    jobs.create(local_plan)
    running = jobs.start(local_plan.job_id)
    assert running.status is MarketJobStatus.RUNNING

    adapter = RangeAdapter()
    history = _service(tmp_path, adapter)
    events: list[str] = []
    clock = TickClock(NOW)
    repository = TerminalRepository(
        claim.operation,
        recovery_claim=claim,
        events=events,
    )
    executor = FakeExecutor(jobs=cast(FakeJobs, jobs))
    worker = MarketOperationWorker(
        session=_session(repository, clock),
        planner=cast(MarketDataPlanner, StaticPlanner(source)),
        executor=cast(BackfillExecutor, executor),
        jobs=jobs,
        history=history,
        clock=clock,
        heartbeat_interval_seconds=60,
    )
    lock_manager = DatasetLockManager(
        tmp_path,
        timeout_seconds=0,
        stale_after_seconds=60,
        clock=lambda: NOW,
    )

    with lock_manager.acquire(local_plan.dataset_key):
        assert jobs.recover_abandoned() == 0
        assert jobs.get(local_plan.job_id).status is MarketJobStatus.RUNNING
        result = await worker.run_once()
        assert jobs.get(local_plan.job_id).status is MarketJobStatus.RUNNING

    assert result is not None
    assert result.state is MarketOperationState.FAILED
    assert result.failure is not None
    assert result.failure.code is MarketOperationFailureCode.DATASET_BUSY
    assert result.progress == claim.operation.progress
    assert result.record_version == claim.operation.record_version + 1
    assert result.lease is None
    assert executor.run_calls == executor.resume_calls == 0
    assert adapter.fetch_calls == []
    assert repository.recovery_claim_call is not None
    assert len(repository.reconcile_calls) == 1
    assert "claim_next" not in events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("recovered_from", "local_status", "expected_state", "expected_code"),
    [
        (
            MarketOperationState.PAUSE_REQUESTED,
            None,
            MarketOperationState.PAUSED,
            None,
        ),
        (
            MarketOperationState.PAUSE_REQUESTED,
            MarketJobStatus.PAUSED,
            MarketOperationState.PAUSED,
            None,
        ),
        (
            MarketOperationState.PAUSE_REQUESTED,
            MarketJobStatus.RUNNING,
            MarketOperationState.FAILED,
            MarketOperationFailureCode.DATASET_BUSY,
        ),
        (
            MarketOperationState.CANCEL_REQUESTED,
            None,
            MarketOperationState.CANCELLED,
            MarketOperationFailureCode.CANCELLED_BY_ADMIN,
        ),
        (
            MarketOperationState.CANCEL_REQUESTED,
            MarketJobStatus.FAILED,
            MarketOperationState.CANCELLED,
            MarketOperationFailureCode.CANCELLED_BY_ADMIN,
        ),
        (
            MarketOperationState.CANCEL_REQUESTED,
            MarketJobStatus.RUNNING,
            MarketOperationState.FAILED,
            MarketOperationFailureCode.DATASET_BUSY,
        ),
    ],
)
async def test_control_recovery_never_resumes_work(
    recovered_from: MarketOperationState,
    local_status: MarketJobStatus | None,
    expected_state: MarketOperationState,
    expected_code: MarketOperationFailureCode | None,
) -> None:
    source = _source_plan()
    claim = _recovery_claim(
        source,
        recovered_from=recovered_from,
        bind_local=local_status is not None,
    )
    clock = TickClock(NOW)
    repository = TerminalRepository(claim.operation, recovery_claim=claim)
    jobs = FakeJobs(None if local_status is None else _record(claim.operation, local_status))
    executor = FakeExecutor(jobs=jobs)

    result = await _worker(
        operation=claim.operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=FakeHistory(None),
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.state is expected_state
    assert result.lease is None
    assert result.result is None
    assert executor.run_calls == 0
    assert executor.resume_calls == 0
    assert len(repository.reconcile_calls) == 1
    assert repository.reconcile_calls[0][0].state is expected_state
    if expected_code is None:
        assert result.failure is None
    else:
        assert result.failure is not None
        assert result.failure.code is expected_code
    if recovered_from is MarketOperationState.CANCEL_REQUESTED and local_status is not None:
        if local_status not in {MarketJobStatus.RUNNING, MarketJobStatus.CANCELLED}:
            assert jobs.cancel_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recovered_from",
    [MarketOperationState.PAUSE_REQUESTED, MarketOperationState.CANCEL_REQUESTED],
)
async def test_completion_evidence_wins_recovered_control_request(
    recovered_from: MarketOperationState,
) -> None:
    source = _source_plan()
    claim = _recovery_claim(source, recovered_from=recovered_from, bind_local=True)
    clock = TickClock(NOW)
    repository = TerminalRepository(claim.operation, recovery_claim=claim)
    jobs = FakeJobs(_record(claim.operation, MarketJobStatus.COMPLETED))
    executor = FakeExecutor(jobs=jobs)

    result = await _worker(
        operation=claim.operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=FakeHistory(_receipt(claim.operation)),
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.state is MarketOperationState.COMPLETED
    assert result.result is not None
    assert result.result.dataset_version == "c" * 64
    assert result.result.dataset_checksum == "d" * 64
    assert executor.run_calls == executor.resume_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("local_status", "expected_state"),
    [
        (MarketJobStatus.COMPLETED, MarketOperationState.COMPLETED),
        (MarketJobStatus.CANCELLED, MarketOperationState.CANCELLED),
        (MarketJobStatus.PAUSED, MarketOperationState.PAUSED),
    ],
)
async def test_abandoned_recovery_accepts_only_unambiguous_terminal_evidence(
    local_status: MarketJobStatus,
    expected_state: MarketOperationState,
) -> None:
    source = _source_plan()
    claim = _recovery_claim(
        source,
        recovered_from=MarketOperationState.RECOVERING,
        bind_local=True,
    )
    clock = TickClock(NOW)
    repository = TerminalRepository(claim.operation, recovery_claim=claim)
    jobs = FakeJobs(_record(claim.operation, local_status))
    executor = FakeExecutor(jobs=jobs)

    result = await _worker(
        operation=claim.operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=FakeHistory(
            _receipt(claim.operation) if local_status is MarketJobStatus.COMPLETED else None
        ),
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.state is expected_state
    assert result.lease is None
    assert executor.run_calls == executor.resume_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "local_status",
    [None, MarketJobStatus.PLANNED, MarketJobStatus.FAILED, MarketJobStatus.RUNNING],
)
async def test_abandoned_recovery_fails_closed_when_provenance_is_ambiguous(
    local_status: MarketJobStatus | None,
) -> None:
    source = _source_plan()
    claim = _recovery_claim(
        source,
        recovered_from=MarketOperationState.RECOVERING,
        bind_local=local_status is not None,
    )
    clock = TickClock(NOW)
    repository = TerminalRepository(claim.operation, recovery_claim=claim)
    jobs = FakeJobs(None if local_status is None else _record(claim.operation, local_status))
    executor = FakeExecutor(jobs=jobs)

    result = await _worker(
        operation=claim.operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=FakeHistory(None),
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.state is MarketOperationState.FAILED
    assert result.failure is not None
    assert result.failure.code is MarketOperationFailureCode.LOCAL_STATE_INVALID
    assert executor.run_calls == executor.resume_calls == 0


@pytest.mark.asyncio
async def test_recovery_rejects_invalid_local_binding_without_regressing_progress() -> None:
    source = _source_plan()
    claim = _recovery_claim(
        source,
        recovered_from=MarketOperationState.RUNNING,
        bind_local=True,
    )
    invalid = replace(
        _record(claim.operation, MarketJobStatus.FAILED),
        dataset_key="binance:spot:ETH/USDT:1h",
    )
    clock = TickClock(NOW)
    repository = TerminalRepository(claim.operation, recovery_claim=claim)
    jobs = FakeJobs(invalid)
    executor = FakeExecutor(jobs=jobs)

    result = await _worker(
        operation=claim.operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=FakeHistory(None),
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.state is MarketOperationState.FAILED
    assert result.failure is not None
    assert result.failure.code is MarketOperationFailureCode.LOCAL_STATE_INVALID
    assert result.progress == claim.operation.progress


@pytest.mark.asyncio
async def test_recovery_rejects_real_local_progress_regression_and_preserves_pg() -> None:
    source = _source_plan()
    original_claim = _recovery_claim(
        source,
        recovered_from=MarketOperationState.RUNNING,
        bind_local=True,
    )
    pg_progress = replace(
        original_claim.operation.progress,
        chunks_completed=2,
        candles_received=2,
        candles_persisted=2,
        requests_completed=2,
    )
    operation = replace(original_claim.operation, progress=pg_progress)
    claim = MarketOperationRecoveryClaim(
        operation=operation,
        recovered_from=MarketOperationState.RUNNING,
    )
    local = _record(operation, MarketJobStatus.FAILED)
    assert local.chunks_completed == 1
    assert operation.progress.chunks_completed == 2

    events: list[str] = []
    clock = TickClock(NOW)
    repository = TerminalRepository(operation, recovery_claim=claim, events=events)
    jobs = FakeJobs(local, events=events)
    executor = FakeExecutor(jobs=jobs)

    result = await _worker(
        operation=operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=FakeHistory(None),
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.state is MarketOperationState.FAILED
    assert result.failure is not None
    assert result.failure.code is MarketOperationFailureCode.LOCAL_STATE_INVALID
    assert result.progress == pg_progress
    assert result.progress.chunks_completed == 2
    assert result.record_version == operation.record_version + 1
    assert result.lease is None
    assert executor.run_calls == executor.resume_calls == 0
    assert repository.recovery_claim_call is not None
    assert len(repository.reconcile_calls) == 1
    reconciled, owner_id, _now, expected_version = repository.reconcile_calls[0]
    assert reconciled.progress == pg_progress
    assert reconciled.lease is None
    assert owner_id == OWNER
    assert expected_version == operation.record_version
    assert "claim_next" not in events


@pytest.mark.asyncio
async def test_recovery_plan_conflict_is_sanitized_without_execution() -> None:
    source = _source_plan()
    claim = _recovery_claim(
        source,
        recovered_from=MarketOperationState.RUNNING,
        bind_local=True,
    )
    drift = "b" * 64
    operation = replace(
        claim.operation,
        request=replace(claim.operation.request, plan_checksum=drift),
        plan=replace(claim.operation.plan, checksum=drift),
    )
    claim = MarketOperationRecoveryClaim(
        operation=operation,
        recovered_from=MarketOperationState.RUNNING,
    )
    clock = TickClock(NOW)
    repository = TerminalRepository(operation, recovery_claim=claim)
    jobs = FakeJobs(_record(operation, MarketJobStatus.FAILED))
    executor = FakeExecutor(jobs=jobs)

    result = await _worker(
        operation=operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=FakeHistory(None),
        clock=clock,
    ).run_once()

    assert result is not None
    assert result.state is MarketOperationState.FAILED
    assert result.failure is not None
    assert result.failure.code is MarketOperationFailureCode.PLAN_CONFLICT
    assert executor.run_calls == executor.resume_calls == 0


@pytest.mark.asyncio
async def test_recovery_resume_publishes_durable_receipt_without_refetch(
    tmp_path: Path,
) -> None:
    source = _source_plan()
    local_plan = replace(source, job_id=str(OPERATION_ID))
    claim = _recovery_claim(
        source,
        recovered_from=MarketOperationState.RUNNING,
        bind_local=True,
    )
    adapter = RangeAdapter()
    history = _service(tmp_path, adapter)
    first_chunk = local_plan.chunks[0]
    await history.ingest(
        PAIR,
        local_plan.timeframe,
        first_chunk.data_range,
        operation=ChunkOperationContext(
            job_id=local_plan.job_id,
            chunk_index=first_chunk.index,
            data_range=first_chunk.data_range,
        ),
    )
    adapter.fetch_calls.clear()

    jobs = MarketJobCatalog(tmp_path, clock=lambda: NOW)
    jobs.create(local_plan)
    jobs.start(local_plan.job_id)
    jobs.fail(local_plan.job_id, "interrupted_job")
    executor = BackfillExecutor(
        history=history,
        jobs=jobs,
        data_dir=tmp_path,
        lock_timeout_seconds=0,
        lock_stale_after_seconds=60,
    )
    clock = TickClock(NOW)
    repository = TerminalRepository(claim.operation, recovery_claim=claim)
    worker = MarketOperationWorker(
        session=_session(repository, clock),
        planner=cast(MarketDataPlanner, StaticPlanner(source)),
        executor=executor,
        jobs=jobs,
        history=history,
        clock=clock,
        heartbeat_interval_seconds=60,
    )

    result = await worker.run_once()

    assert result is not None
    assert result.state is MarketOperationState.COMPLETED
    assert result.result is not None
    assert result.progress.chunks_completed == 2
    assert adapter.fetch_calls == [local_plan.chunks[1].data_range]
    assert repository.progress_calls[0][1].chunks_completed == 1
    assert repository.progress_calls[-1][1].chunks_completed == 2
    final_receipt = history.get_chunk_receipt(local_plan.job_id, local_plan.chunks[-1].index)
    assert final_receipt is not None
    assert result.result.dataset_version == final_receipt.version
    assert result.result.dataset_checksum == final_receipt.checksum


@pytest.mark.asyncio
async def test_recovered_execution_renews_new_owner_lease_and_current_versions() -> None:
    source = _source_plan()
    claim = _recovery_claim(
        source,
        recovered_from=MarketOperationState.RUNNING,
        bind_local=True,
    )
    clock = TickClock(NOW)
    repository = TerminalRepository(claim.operation, recovery_claim=claim)
    jobs = FakeJobs(_record(claim.operation, MarketJobStatus.FAILED))
    executor = FakeExecutor(
        jobs=jobs,
        terminal_record=_record(claim.operation, MarketJobStatus.COMPLETED),
        delay_seconds=0.04,
    )

    result = await _worker(
        operation=claim.operation,
        repository=repository,
        jobs=jobs,
        executor=executor,
        history=FakeHistory(_receipt(claim.operation)),
        clock=clock,
        heartbeat_interval_seconds=0.005,
    ).run_once()

    assert result is not None
    assert result.state is MarketOperationState.COMPLETED
    assert len(repository.renew_calls) >= 2
    assert all(lease.owner_id == OWNER for _version, lease in repository.renew_calls)
    assert repository.renew_calls[0][0] >= claim.operation.record_version + 1


@pytest.mark.asyncio
async def test_recovered_execution_lease_loss_cancels_and_awaits_executor() -> None:
    source = _source_plan()
    claim = _recovery_claim(
        source,
        recovered_from=MarketOperationState.RUNNING,
        bind_local=True,
    )
    clock = TickClock(NOW)
    repository = TerminalRepository(
        claim.operation,
        recovery_claim=claim,
        renew_error=InvalidOperationLeaseError(),
    )
    jobs = FakeJobs(_record(claim.operation, MarketJobStatus.FAILED))
    executor = FakeExecutor(
        jobs=jobs,
        terminal_record=_record(claim.operation, MarketJobStatus.COMPLETED),
        delay_seconds=0.1,
    )

    with pytest.raises(InvalidOperationLeaseError):
        await _worker(
            operation=claim.operation,
            repository=repository,
            jobs=jobs,
            executor=executor,
            history=FakeHistory(_receipt(claim.operation)),
            clock=clock,
            heartbeat_interval_seconds=0.005,
        ).run_once()

    assert executor.cancelled
    assert executor.cancel_awaited
    assert repository.operation is not None
    assert repository.operation.state is MarketOperationState.RUNNING
    assert repository.complete_calls == 0
    assert repository.fail_calls == 0
