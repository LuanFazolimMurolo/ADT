"""Phase 7-01D1 cooperative BackfillExecutor control-boundary tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.market_data.catalog import ChunkOperationContext
from app.market_data.domain import DataRange
from app.market_data.jobs import MarketJobCatalog, MarketJobRecord
from app.market_data.orchestration import (
    BackfillControl,
    BackfillExecutionObserver,
    BackfillExecutor,
)
from app.market_data.planning import BackfillChunk, MarketJobStatus
from app.market_data.timeframes import get_timeframe
from tests.market_data_helpers import PAIR, utc
from tests.test_market_data_phase2b import (
    RangeAdapter,
    _executor,
    _key,
    _planner,
    _service,
)


@dataclass
class RecordingObserver:
    directives: dict[int, BackfillControl] = field(default_factory=dict)
    before: list[tuple[int, int]] = field(default_factory=list)
    checkpoints: list[tuple[int, int]] = field(default_factory=list)

    async def before_chunk(
        self,
        record: MarketJobRecord,
        chunk: BackfillChunk,
    ) -> BackfillControl:
        self.before.append((chunk.index, record.chunks_completed))
        return self.directives.get(
            chunk.index,
            BackfillControl.CONTINUE,
        )

    async def after_checkpoint(
        self,
        record: MarketJobRecord,
        chunk: BackfillChunk,
    ) -> None:
        self.checkpoints.append((chunk.index, record.chunks_completed))


def _require_protocol(
    value: BackfillExecutionObserver,
) -> BackfillExecutionObserver:
    return value


@pytest.mark.asyncio
async def test_observer_sees_every_confirmed_checkpoint(
    tmp_path: Path,
) -> None:
    adapter = RangeAdapter()
    executor, _jobs = _executor(tmp_path, adapter)
    observer = RecordingObserver()

    plan = _planner(chunk=1).backfill(
        _key(),
        get_timeframe("1h"),
        DataRange(
            utc(2026, 1, 1),
            utc(2026, 1, 1, 3),
        ),
    )

    result = await executor.run(
        plan,
        PAIR,
        observer=_require_protocol(observer),
    )

    assert result.status is MarketJobStatus.COMPLETED
    assert observer.before == [(0, 0), (1, 1), (2, 2)]
    assert observer.checkpoints == [(0, 1), (1, 2), (2, 3)]
    assert len(adapter.fetch_calls) == 3


@pytest.mark.asyncio
async def test_observer_can_pause_at_safe_chunk_boundary(
    tmp_path: Path,
) -> None:
    adapter = RangeAdapter()
    executor, jobs = _executor(tmp_path, adapter)
    observer = RecordingObserver(directives={1: BackfillControl.PAUSE})

    plan = _planner(chunk=1).backfill(
        _key(),
        get_timeframe("1h"),
        DataRange(
            utc(2026, 1, 1),
            utc(2026, 1, 1, 3),
        ),
    )

    result = await executor.run(plan, PAIR, observer=observer)

    assert result.status is MarketJobStatus.PAUSED
    assert result.chunks_completed == 1
    assert jobs.get(plan.job_id).status is MarketJobStatus.PAUSED
    assert observer.checkpoints == [(0, 1)]
    assert len(adapter.fetch_calls) == 1


@pytest.mark.asyncio
async def test_observer_can_cancel_before_first_chunk(
    tmp_path: Path,
) -> None:
    adapter = RangeAdapter()
    executor, jobs = _executor(tmp_path, adapter)
    observer = RecordingObserver(directives={0: BackfillControl.CANCEL})

    plan = _planner(chunk=1).backfill(
        _key(),
        get_timeframe("1h"),
        DataRange(
            utc(2026, 1, 1),
            utc(2026, 1, 1, 2),
        ),
    )

    result = await executor.run(plan, PAIR, observer=observer)

    assert result.status is MarketJobStatus.CANCELLED
    assert result.chunks_completed == 0
    assert jobs.get(plan.job_id).status is MarketJobStatus.CANCELLED
    assert observer.checkpoints == []
    assert adapter.fetch_calls == []


@pytest.mark.asyncio
async def test_recovered_receipt_emits_checkpoint_without_refetch(
    tmp_path: Path,
) -> None:
    timeframe = get_timeframe("1h")
    data_range = DataRange(
        utc(2026, 1, 1),
        utc(2026, 1, 1, 2),
    )

    plan = _planner().backfill(
        _key(),
        timeframe,
        data_range,
    )

    await _service(tmp_path, RangeAdapter()).ingest(
        PAIR,
        timeframe,
        data_range,
        operation=ChunkOperationContext(
            plan.job_id,
            0,
            data_range,
        ),
    )

    jobs = MarketJobCatalog(tmp_path)
    jobs.create(plan)
    jobs.start(plan.job_id)
    jobs.fail(plan.job_id, "checkpoint_write_failed")

    adapter = RangeAdapter()
    executor = BackfillExecutor(
        history=_service(tmp_path, adapter),
        jobs=jobs,
        data_dir=tmp_path,
        lock_timeout_seconds=0,
        lock_stale_after_seconds=60,
    )
    observer = RecordingObserver()

    result = await executor.resume(
        plan.job_id,
        PAIR,
        observer=observer,
    )

    assert result.status is MarketJobStatus.COMPLETED
    assert adapter.fetch_calls == []
    assert observer.checkpoints == [(0, 1)]
