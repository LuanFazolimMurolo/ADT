"""Phase 7-01D2B2A market-operation run-once worker tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from app.market_data.catalog import ChunkCommitReceipt
from app.market_data.errors import MarketJobNotFoundError, MarketRateLimitError
from app.market_data.jobs import MarketJobCatalog, MarketJobRecord
from app.market_data.operation_worker import (
    MarketOperationWorker,
    MarketOperationWorkerSession,
)
from app.market_data.operations import (
    MarketOperationFailureCode,
    MarketOperationSnapshot,
    MarketOperationState,
    OperationProgress,
    OperationResult,
    SanitizedOperationFailure,
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
from tests.test_market_operation_worker_control import (
    NOW,
    OPERATION_ID,
    OWNER,
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
    def __init__(self, operation: MarketOperationSnapshot) -> None:
        super().__init__(operation)
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
        self.complete_calls += 1

        self.operation = replace(
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
        return self.operation

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
        self.fail_calls += 1

        self.operation = replace(
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
        return self.operation


class EmptyClaimRepository(FakeRepository):
    def __init__(self) -> None:
        super().__init__(None)


class FakeJobs:
    def __init__(
        self,
        record: MarketJobRecord | None = None,
    ) -> None:
        self.record = record
        self.recoveries = 0

    def recover_abandoned(self) -> int:
        self.recoveries += 1
        return 0

    def get(self, job_id: str) -> MarketJobRecord:
        if self.record is None:
            raise MarketJobNotFoundError()
        assert self.record.job_id == job_id
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
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)

        if self.control_request is not None:
            assert self.repository is not None
            assert self.repository.operation is not None
            assert self.clock is not None

            current = self.repository.operation
            self.repository.operation = replace(
                current,
                state=self.control_request,
                updated_at=self.clock(),
                record_version=current.record_version + 1,
            )

        if self.error is not None:
            raise self.error

        assert self.terminal_record is not None
        self.jobs.record = self.terminal_record
        return _result(self.terminal_record)


def _record(
    operation: MarketOperationSnapshot,
    status: MarketJobStatus,
) -> MarketJobRecord:
    completed = 2 if status is MarketJobStatus.COMPLETED else 1

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
        start=(NOW - timedelta(hours=1)).isoformat(),
        end=NOW.isoformat(),
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
