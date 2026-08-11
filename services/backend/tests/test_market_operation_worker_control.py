"""Phase 7-01D2A worker lease/control-session tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from app.market_data.domain import DataRange, Exchange, MarketType, TradingPair
from app.market_data.errors import MarketOperationPlanConflictError
from app.market_data.jobs import MarketJobRecord
from app.market_data.operation_ports import MarketOperationRepository
from app.market_data.operation_worker import (
    MarketOperationExecutionObserver,
    MarketOperationWorkerSession,
    build_operation_backfill_plan,
)
from app.market_data.operations import (
    MarketDatasetSelector,
    MarketOperationFailureCode,
    MarketOperationRequest,
    MarketOperationSnapshot,
    MarketOperationState,
    MarketOperationType,
    OperationPlanSummary,
    OperationProgress,
    SanitizedOperationFailure,
    WorkerLease,
)
from app.market_data.orchestration import (
    BackfillControl,
    BackfillExecutionObserver,
)
from app.market_data.planning import (
    BackfillChunk,
    BackfillPlan,
    MarketDataPlanner,
    MarketJobStatus,
    MarketJobType,
    backfill_plan_checksum,
)
from app.market_data.timeframes import get_timeframe

OWNER = UUID("10000000-0000-4000-8000-000000000001")
OTHER_OWNER = UUID("10000000-0000-4000-8000-000000000002")
OPERATION_ID = UUID("20000000-0000-4000-8000-000000000001")
REQUESTER_ID = UUID("30000000-0000-4000-8000-000000000001")

START = datetime(2026, 8, 10, 8, tzinfo=UTC)
END = datetime(2026, 8, 10, 10, tzinfo=UTC)
NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)


class ManualClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


def _dataset() -> MarketDatasetSelector:
    return MarketDatasetSelector(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        pair=TradingPair("BTC", "USDT"),
        timeframe=get_timeframe("1h"),
    )


def _snapshot(
    *,
    state: MarketOperationState = MarketOperationState.CLAIMED,
    version: int = 2,
    owner_id: UUID = OWNER,
    heartbeat_at: datetime = NOW,
    lease_expires_at: datetime = NOW + timedelta(minutes=1),
) -> MarketOperationSnapshot:
    dataset = _dataset()

    request = MarketOperationRequest(
        operation_type=MarketOperationType.RAW_BACKFILL,
        dataset=dataset,
        data_range=DataRange(START, END),
        plan_checksum="a" * 64,
        idempotency_key="phase7-01d2a",
        requested_by=REQUESTER_ID,
    )

    plan = OperationPlanSummary(
        checksum="a" * 64,
        chunks_planned=2,
        estimated_candles=2,
        estimated_requests=2,
        created_at=NOW,
    )

    return MarketOperationSnapshot(
        operation_id=OPERATION_ID,
        request=request,
        plan=plan,
        state=state,
        progress=OperationProgress(
            chunks_planned=2,
            chunks_completed=0,
            chunks_failed=0,
            candles_estimated=2,
            candles_received=0,
            candles_persisted=0,
            requests_completed=0,
            updated_at=NOW,
        ),
        created_at=NOW,
        updated_at=heartbeat_at,
        record_version=version,
        lease=WorkerLease(
            operation_id=OPERATION_ID,
            owner_id=owner_id,
            claimed_at=NOW,
            heartbeat_at=heartbeat_at,
            lease_expires_at=lease_expires_at,
        ),
        started_at=NOW,
    )


class FakeRepository:
    def __init__(self, operation: MarketOperationSnapshot | None) -> None:
        self.operation = operation
        self.claim_call: tuple[UUID, datetime, datetime] | None = None
        self.state_calls: list[tuple[MarketOperationState, int, UUID | None]] = []
        self.renew_calls: list[tuple[int, WorkerLease]] = []

    async def claim_next(
        self,
        *,
        owner_id: UUID,
        now: datetime,
        lease_expires_at: datetime,
    ) -> MarketOperationSnapshot | None:
        self.claim_call = (owner_id, now, lease_expires_at)
        return self.operation

    async def get(
        self,
        operation_id: UUID,
    ) -> MarketOperationSnapshot | None:
        assert operation_id == OPERATION_ID
        return self.operation

    async def request_state(
        self,
        *,
        operation_id: UUID,
        target: MarketOperationState,
        expected_version: int,
        now: datetime,
        owner_id: UUID | None = None,
    ) -> MarketOperationSnapshot:
        assert operation_id == OPERATION_ID
        assert self.operation is not None
        assert expected_version == self.operation.record_version
        self.state_calls.append((target, expected_version, owner_id))

        lease = self.operation.lease
        if target in {
            MarketOperationState.PAUSED,
            MarketOperationState.CANCELLED,
        }:
            lease = None

        failure = self.operation.failure
        finished_at = self.operation.finished_at

        if target is MarketOperationState.CANCELLED:
            failure = SanitizedOperationFailure(
                code=MarketOperationFailureCode.CANCELLED_BY_ADMIN,
                failed_at=now,
            )
            finished_at = now

        self.operation = replace(
            self.operation,
            state=target,
            lease=lease,
            started_at=self.operation.started_at,
            failure=failure,
            finished_at=finished_at,
            updated_at=now,
            record_version=self.operation.record_version + 1,
        )
        return self.operation

    async def renew_lease(
        self,
        *,
        operation_id: UUID,
        owner_id: UUID,
        now: datetime,
        lease: WorkerLease,
        expected_version: int,
    ) -> MarketOperationSnapshot:
        assert operation_id == OPERATION_ID
        assert owner_id == OWNER
        assert self.operation is not None
        assert expected_version == self.operation.record_version
        self.renew_calls.append((expected_version, lease))
        self.operation = replace(
            self.operation,
            lease=lease,
            updated_at=now,
            record_version=self.operation.record_version + 1,
        )
        return self.operation


def _session(
    repository: FakeRepository,
    clock: ManualClock,
) -> MarketOperationWorkerSession:
    return MarketOperationWorkerSession(
        repository=cast(MarketOperationRepository, repository),
        owner_id=OWNER,
        clock=clock,
        lease_duration=timedelta(minutes=2),
    )


@pytest.mark.asyncio
async def test_claim_next_uses_explicit_owner_clock_and_lease_bound() -> None:
    clock = ManualClock(NOW)
    repository = FakeRepository(_snapshot())
    session = _session(repository, clock)

    claimed = await session.claim_next()

    assert claimed is repository.operation
    assert repository.claim_call == (
        OWNER,
        NOW,
        NOW + timedelta(minutes=2),
    )


@pytest.mark.asyncio
async def test_start_requires_owned_claim_and_transitions_to_running() -> None:
    clock = ManualClock(NOW + timedelta(seconds=1))
    repository = FakeRepository(_snapshot())
    session = _session(repository, clock)

    running = await session.start(repository.operation)

    assert running.state is MarketOperationState.RUNNING
    assert running.record_version == 3
    assert repository.state_calls == [(MarketOperationState.RUNNING, 2, OWNER)]


@pytest.mark.asyncio
async def test_poll_continue_reloads_and_renews_owned_lease() -> None:
    clock = ManualClock(NOW + timedelta(seconds=30))
    repository = FakeRepository(_snapshot(state=MarketOperationState.RUNNING))
    session = _session(repository, clock)

    current, control = await session.poll_control(repository.operation)

    assert control is BackfillControl.CONTINUE
    assert current.record_version == 3
    assert current.lease is not None
    assert current.lease.heartbeat_at == clock.current
    assert current.lease.lease_expires_at == (clock.current + timedelta(minutes=2))
    assert len(repository.renew_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (MarketOperationState.PAUSE_REQUESTED, BackfillControl.PAUSE),
        (MarketOperationState.CANCEL_REQUESTED, BackfillControl.CANCEL),
    ],
)
async def test_poll_surfaces_admin_control_without_renewing(
    state: MarketOperationState,
    expected: BackfillControl,
) -> None:
    clock = ManualClock(NOW + timedelta(seconds=10))
    repository = FakeRepository(_snapshot(state=state))
    session = _session(repository, clock)

    current, control = await session.poll_control(repository.operation)

    assert current.state is state
    assert control is expected
    assert repository.renew_calls == []


@pytest.mark.asyncio
async def test_finish_pause_acknowledges_request_with_owner() -> None:
    clock = ManualClock(NOW + timedelta(seconds=15))
    repository = FakeRepository(_snapshot(state=MarketOperationState.PAUSE_REQUESTED))
    session = _session(repository, clock)

    paused = await session.finish_pause(repository.operation)

    assert paused.state is MarketOperationState.PAUSED
    assert paused.lease is None
    assert repository.state_calls == [(MarketOperationState.PAUSED, 2, OWNER)]


@pytest.mark.asyncio
async def test_finish_cancel_acknowledges_request_with_owner() -> None:
    clock = ManualClock(NOW + timedelta(seconds=15))
    repository = FakeRepository(_snapshot(state=MarketOperationState.CANCEL_REQUESTED))
    session = _session(repository, clock)

    cancelled = await session.finish_cancel(repository.operation)

    assert cancelled.state is MarketOperationState.CANCELLED
    assert cancelled.lease is None
    assert repository.state_calls == [(MarketOperationState.CANCELLED, 2, OWNER)]


@pytest.mark.asyncio
async def test_wrong_owner_is_rejected_before_repository_mutation() -> None:
    clock = ManualClock(NOW)
    repository = FakeRepository(_snapshot(owner_id=OTHER_OWNER))
    session = _session(repository, clock)

    with pytest.raises(ValueError, match="not owned"):
        await session.start(repository.operation)

    assert repository.state_calls == []
    assert repository.renew_calls == []


def _job_record(
    *,
    status: MarketJobStatus = MarketJobStatus.RUNNING,
    chunks_completed: int = 1,
    fetched: int = 1,
    stored: int = 1,
    requests: int = 1,
) -> MarketJobRecord:
    return MarketJobRecord(
        job_id=str(OPERATION_ID),
        dataset_key=_dataset().canonical_key,
        job_type=MarketJobType.BACKFILL,
        status=status,
        timeframe="1h",
        start=START.isoformat(),
        end=END.isoformat(),
        chunk_ranges=(
            (START.isoformat(), (START + timedelta(hours=1)).isoformat()),
            ((START + timedelta(hours=1)).isoformat(), END.isoformat()),
        ),
        plan_checksum="a" * 64,
        next_chunk_index=chunks_completed,
        chunks_completed=chunks_completed,
        candles_expected=2,
        chunk_candles=1,
        candles_fetched=fetched,
        candles_stored=stored,
        duplicates=0,
        request_count=requests,
        started_at=NOW.isoformat(),
        updated_at=NOW.isoformat(),
        finished_at=None,
        error_code=None,
    )


class ProgressFakeRepository(FakeRepository):
    def __init__(self, operation: MarketOperationSnapshot) -> None:
        super().__init__(operation)
        self.progress_calls: list[tuple[int, OperationProgress, str | None]] = []

    async def update_progress(
        self,
        *,
        operation_id: UUID,
        owner_id: UUID,
        now: datetime,
        progress: OperationProgress,
        local_job_id: str | None,
        expected_version: int,
    ) -> MarketOperationSnapshot:
        assert operation_id == OPERATION_ID
        assert owner_id == OWNER
        assert self.operation is not None
        assert expected_version == self.operation.record_version

        self.progress_calls.append((expected_version, progress, local_job_id))

        self.operation = replace(
            self.operation,
            progress=progress,
            local_job_id=local_job_id,
            updated_at=now,
            record_version=self.operation.record_version + 1,
        )
        return self.operation


class StaticPlanner:
    def __init__(self, plan: BackfillPlan) -> None:
        self.plan = plan
        self.call: dict[str, object] | None = None

    def backfill(
        self,
        dataset_key: str,
        timeframe: object,
        data_range: DataRange,
        *,
        job_type: MarketJobType = MarketJobType.BACKFILL,
        job_id: str | None = None,
        latest_closed_at: datetime | None = None,
    ) -> BackfillPlan:
        self.call = {
            "dataset_key": dataset_key,
            "timeframe": timeframe,
            "data_range": data_range,
            "job_type": job_type,
            "job_id": job_id,
            "latest_closed_at": latest_closed_at,
        }
        return replace(
            self.plan,
            job_type=job_type,
            job_id=self.plan.job_id if job_id is None else job_id,
        )


def _source_plan(
    *,
    job_type: MarketJobType = MarketJobType.BACKFILL,
) -> BackfillPlan:
    timeframe = get_timeframe("1h")
    return BackfillPlan(
        job_id="40000000-0000-4000-8000-000000000001",
        dataset_key=_dataset().canonical_key,
        timeframe=timeframe,
        data_range=DataRange(START, END),
        chunks=(
            BackfillChunk(
                0,
                DataRange(START, START + timedelta(hours=1)),
                1,
            ),
            BackfillChunk(
                1,
                DataRange(START + timedelta(hours=1), END),
                1,
            ),
        ),
        expected_candles=2,
        chunk_candles=1,
        job_type=job_type,
    )


def _operation_for_plan(
    plan: BackfillPlan,
) -> MarketOperationSnapshot:
    base = _snapshot(state=MarketOperationState.CLAIMED)
    checksum = backfill_plan_checksum(plan)

    request = replace(
        base.request,
        plan_checksum=checksum,
        operation_type=(
            MarketOperationType.RAW_INCREMENTAL_UPDATE
            if plan.job_type is MarketJobType.INCREMENTAL
            else MarketOperationType.RAW_BACKFILL
        ),
    )

    summary = OperationPlanSummary(
        checksum=checksum,
        chunks_planned=len(plan.chunks),
        estimated_candles=plan.expected_candles,
        estimated_requests=len(plan.chunks),
        created_at=NOW,
    )

    progress = OperationProgress(
        chunks_planned=len(plan.chunks),
        chunks_completed=0,
        chunks_failed=0,
        candles_estimated=plan.expected_candles,
        candles_received=0,
        candles_persisted=0,
        requests_completed=0,
        updated_at=NOW,
    )

    return replace(
        base,
        request=request,
        plan=summary,
        progress=progress,
    )


def _require_execution_observer(
    observer: BackfillExecutionObserver,
) -> BackfillExecutionObserver:
    return observer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        MarketOperationState.PAUSE_REQUESTED,
        MarketOperationState.CANCEL_REQUESTED,
    ],
)
async def test_heartbeat_keeps_control_requested_lease_alive_until_safe_boundary(
    state: MarketOperationState,
) -> None:
    clock = ManualClock(NOW + timedelta(seconds=30))
    repository = FakeRepository(_snapshot(state=state))
    session = _session(repository, clock)

    renewed = await session.heartbeat(repository.operation)

    assert renewed.state is state
    assert renewed.lease is not None
    assert renewed.lease.heartbeat_at == clock.current
    assert renewed.lease.lease_expires_at == (clock.current + timedelta(minutes=2))
    assert len(repository.renew_calls) == 1


@pytest.mark.asyncio
async def test_checkpoint_projects_durable_local_counters_and_job_identity() -> None:
    clock = ManualClock(NOW + timedelta(seconds=20))
    operation = _snapshot(state=MarketOperationState.RUNNING)
    repository = ProgressFakeRepository(operation)
    session = _session(repository, clock)
    record = _job_record(
        chunks_completed=1,
        fetched=1,
        stored=1,
        requests=1,
    )

    updated = await session.checkpoint(operation, record)

    assert updated.local_job_id == str(OPERATION_ID)
    assert updated.progress.chunks_completed == 1
    assert updated.progress.candles_received == 1
    assert updated.progress.candles_persisted == 1
    assert updated.progress.requests_completed == 1
    assert updated.progress.updated_at == clock.current
    assert repository.progress_calls == [
        (
            2,
            updated.progress,
            str(OPERATION_ID),
        )
    ]


@pytest.mark.asyncio
async def test_execution_observer_serializes_boundary_checkpoint_and_heartbeat() -> None:
    clock = ManualClock(NOW + timedelta(seconds=10))
    operation = _snapshot(state=MarketOperationState.RUNNING)
    repository = ProgressFakeRepository(operation)
    session = _session(repository, clock)
    observer = MarketOperationExecutionObserver(
        session=session,
        operation=operation,
    )

    assert _require_execution_observer(observer) is observer

    initial = _job_record(
        chunks_completed=0,
        fetched=0,
        stored=0,
        requests=0,
    )
    chunk = BackfillChunk(
        0,
        DataRange(START, START + timedelta(hours=1)),
        1,
    )

    control = await observer.before_chunk(initial, chunk)
    assert control is BackfillControl.CONTINUE

    clock.current += timedelta(seconds=10)
    confirmed = _job_record(
        chunks_completed=1,
        fetched=1,
        stored=1,
        requests=1,
    )

    await observer.after_checkpoint(confirmed, chunk)

    assert observer.operation.progress.chunks_completed == 1
    checkpoint_version = observer.operation.record_version

    clock.current += timedelta(seconds=10)
    heartbeat = await observer.heartbeat()

    assert heartbeat.record_version == checkpoint_version + 1
    assert heartbeat.progress.chunks_completed == 1


def test_operation_plan_is_reconstructed_with_deterministic_local_job_id() -> None:
    source = _source_plan()
    operation = _operation_for_plan(source)
    planner = StaticPlanner(source)

    rebuilt = build_operation_backfill_plan(
        operation,
        planner=cast(MarketDataPlanner, planner),
        now=NOW,
    )

    assert rebuilt.job_id == str(OPERATION_ID)
    assert backfill_plan_checksum(rebuilt) == operation.plan.checksum
    assert planner.call is not None
    assert planner.call["job_id"] == str(OPERATION_ID)
    assert planner.call["job_type"] is MarketJobType.BACKFILL
    assert planner.call["latest_closed_at"] == NOW


def test_operation_plan_reconstruction_rejects_checksum_drift() -> None:
    source = _source_plan()
    operation = _operation_for_plan(source)

    drifted_checksum = "b" * 64
    operation = replace(
        operation,
        request=replace(
            operation.request,
            plan_checksum=drifted_checksum,
        ),
        plan=replace(
            operation.plan,
            checksum=drifted_checksum,
        ),
    )

    planner = StaticPlanner(source)

    with pytest.raises(MarketOperationPlanConflictError):
        build_operation_backfill_plan(
            operation,
            planner=cast(MarketDataPlanner, planner),
            now=NOW,
        )
