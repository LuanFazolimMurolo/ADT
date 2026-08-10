"""Phase 2B planning, checkpoint, locking and orchestration tests."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.market_data.adapters import AdapterLimits
from app.market_data.catalog import ChunkOperationContext, JsonMarketDataCatalog, dataset_key
from app.market_data.domain import CandleBatch, DataRange, Instrument, Timeframe, TradingPair
from app.market_data.errors import (
    InvalidDataRangeError,
    MarketDataInconsistencyError,
    MarketDataStorageError,
    MarketJobLockTimeoutError,
)
from app.market_data.jobs import MarketJobCatalog
from app.market_data.locks import DatasetLockManager
from app.market_data.orchestration import BackfillExecutor
from app.market_data.planning import (
    BackfillPlan,
    MarketDataPlanner,
    MarketJobStatus,
    MarketJobType,
    expected_candle_count,
)
from app.market_data.quality import MarketDataQualityValidator
from app.market_data.services import HistoricalMarketDataService
from app.market_data.storage import RAW_DATASET_VERSION_ALGORITHM, ParquetCandleStore
from app.market_data.timeframes import TIMEFRAMES, get_timeframe
from app.market_data.transaction import MarketDataTransactionCoordinator
from tests.market_data_helpers import INSTRUMENT, PAIR, candle, utc


def _hold_dataset_lock(data_dir: Path, dataset_key_value: str, ready, release) -> None:
    manager = DatasetLockManager(
        data_dir,
        timeout_seconds=0,
        stale_after_seconds=60,
    )
    with manager.acquire(dataset_key_value):
        ready.set()
        release.wait(timeout=5)


def _concurrent_ingestion_worker(data_dir: Path, timeframe_code: str, start_event) -> None:
    timeframe = get_timeframe(timeframe_code)
    start_event.wait(timeout=5)
    asyncio.run(
        _service(data_dir, RangeAdapter()).ingest(
            PAIR,
            timeframe,
            DataRange(utc(2026, 1, 1), utc(2026, 1, 1) + timeframe.duration),
        )
    )


def _blocking_backfill_worker(data_dir: Path, started, release) -> None:
    adapter = BlockingRangeAdapter(started, release)
    executor, _jobs = _executor(data_dir, adapter)
    plan = _planner().backfill(
        _key(),
        get_timeframe("1h"),
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 2)),
    )
    asyncio.run(executor.run(plan, PAIR))


def _concurrent_job_worker(
    data_dir: Path,
    timeframe_code: str,
    job_id: str,
    start_event,
) -> None:
    timeframe = get_timeframe(timeframe_code)
    plan = _planner().backfill(
        _key(timeframe_code),
        timeframe,
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1) + timeframe.duration),
        job_id=job_id,
    )
    start_event.wait(timeout=5)
    jobs = MarketJobCatalog(data_dir)
    jobs.create(plan)
    jobs.start(plan.job_id)
    jobs.advance(
        plan.job_id,
        chunk_index=0,
        fetched=1,
        stored=1,
        duplicates=0,
        requests=1,
    )


def _abandoned_job_worker(data_dir: Path, job_id: str, ready) -> None:
    timeframe = get_timeframe("1h")
    plan = _planner().backfill(
        _key(),
        timeframe,
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 2)),
        job_id=job_id,
    )
    jobs = MarketJobCatalog(data_dir)
    jobs.create(plan)
    jobs.start(job_id)
    manager = DatasetLockManager(data_dir, timeout_seconds=0, stale_after_seconds=60)
    manager.acquire(plan.dataset_key)
    ready.set()
    os._exit(0)


def _crash_ingestion_after_catalog_promotion(
    data_dir: Path,
    timeframe_code: str,
    start: datetime,
) -> None:
    store = ParquetCandleStore(data_dir)
    catalog = JsonMarketDataCatalog(data_dir)

    def terminate(step: str) -> None:
        if step == "catalog_promoted":
            os._exit(17)

    locks = DatasetLockManager(data_dir, timeout_seconds=5, stale_after_seconds=60)
    service = HistoricalMarketDataService(
        adapter=RangeAdapter(),
        store=store,
        catalog=catalog,
        validator=MarketDataQualityValidator(clock=lambda: utc(2030, 1, 1)),
        max_fetch_candles=1000,
        coordinator=MarketDataTransactionCoordinator(
            store,
            catalog,
            failure_hook=terminate,
            lock_manager=locks,
        ),
        clock=lambda: utc(2030, 1, 1),
        lock_manager=locks,
    )
    timeframe = get_timeframe(timeframe_code)
    asyncio.run(
        service.ingest(
            PAIR,
            timeframe,
            DataRange(start, start + timeframe.duration),
        )
    )


def _pause_ingestion_worker(
    data_dir: Path,
    timeframe_code: str,
    start: datetime,
    pause_step: str,
    ready,
    release,
) -> None:
    store = ParquetCandleStore(data_dir)
    catalog = JsonMarketDataCatalog(data_dir)

    def pause(step: str) -> None:
        if step == pause_step:
            ready.set()
            release.wait(timeout=10)

    locks = DatasetLockManager(data_dir, timeout_seconds=5, stale_after_seconds=60)
    service = HistoricalMarketDataService(
        adapter=RangeAdapter(),
        store=store,
        catalog=catalog,
        validator=MarketDataQualityValidator(clock=lambda: utc(2030, 1, 1)),
        max_fetch_candles=1000,
        coordinator=MarketDataTransactionCoordinator(
            store,
            catalog,
            failure_hook=pause,
            lock_manager=locks,
        ),
        clock=lambda: utc(2030, 1, 1),
        lock_manager=locks,
    )
    timeframe = get_timeframe(timeframe_code)
    asyncio.run(
        service.ingest(
            PAIR,
            timeframe,
            DataRange(start, start + timeframe.duration),
        )
    )


def _read_receipt_worker(data_dir: Path, job_id: str, result) -> None:
    receipt = JsonMarketDataCatalog(data_dir).get_chunk_receipt(job_id, 0)
    result.put(receipt is not None)


def _inspect_dataset_worker(data_dir: Path, result) -> None:
    metadata = _service(data_dir, RangeAdapter()).inspect(
        INSTRUMENT,
        get_timeframe("1h"),
    )
    result.put((metadata.candle_count, metadata.version))


class RangeAdapter:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.fetch_calls: list[DataRange] = []
        self.fail_on_call = fail_on_call

    @property
    def limits(self) -> AdapterLimits:
        return AdapterLimits(1000, 2)

    @property
    def exchange(self):
        return INSTRUMENT.exchange

    @property
    def market_type(self):
        return INSTRUMENT.market_type

    async def list_instruments(self) -> tuple[Instrument, ...]:
        return (INSTRUMENT,)

    async def get_instrument(self, pair: TradingPair) -> Instrument:
        assert pair == PAIR
        return INSTRUMENT

    async def fetch_candles(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        data_range: DataRange,
        *,
        max_candles: int,
    ) -> CandleBatch:
        self.fetch_calls.append(data_range)
        if self.fail_on_call == len(self.fetch_calls):
            raise RuntimeError("synthetic failure")
        count = expected_candle_count(data_range, timeframe)
        assert count <= max_candles
        candles = tuple(
            candle(data_range.start + index * timeframe.duration, timeframe=timeframe)
            for index in range(count)
        )
        return CandleBatch(instrument, timeframe, data_range, candles, source_request_count=1)

    def normalize_symbol(self, native_symbol: str) -> TradingPair:
        return PAIR

    def native_symbol(self, pair: TradingPair) -> str:
        return "BTCUSDT"

    def native_timeframe(self, timeframe: Timeframe) -> str:
        return timeframe.code


class EmptyRangeAdapter(RangeAdapter):
    async def fetch_candles(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        data_range: DataRange,
        *,
        max_candles: int,
    ) -> CandleBatch:
        self.fetch_calls.append(data_range)
        return CandleBatch(instrument, timeframe, data_range, (), source_request_count=1)


class BlockingRangeAdapter(RangeAdapter):
    def __init__(self, started, release) -> None:
        super().__init__()
        self._started = started
        self._release = release

    async def fetch_candles(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        data_range: DataRange,
        *,
        max_candles: int,
    ) -> CandleBatch:
        self._started.set()
        self._release.wait(timeout=10)
        return await super().fetch_candles(
            instrument,
            timeframe,
            data_range,
            max_candles=max_candles,
        )


class TransientCheckpointCatalog(MarketJobCatalog):
    def __init__(self, data_dir: Path) -> None:
        super().__init__(data_dir, clock=lambda: utc(2026, 1, 1))
        self.failed_once = False

    def advance(
        self,
        job_id: str,
        *,
        chunk_index: int,
        fetched: int,
        stored: int,
        duplicates: int,
        requests: int,
    ):
        if not self.failed_once:
            self.failed_once = True
            raise OSError("synthetic checkpoint failure")
        return super().advance(
            job_id,
            chunk_index=chunk_index,
            fetched=fetched,
            stored=stored,
            duplicates=duplicates,
            requests=requests,
        )


def _planner(*, chunk: int = 2, total: int = 100, chunks: int = 100) -> MarketDataPlanner:
    return MarketDataPlanner(
        adapter_request_limit=1000,
        max_fetch_candles=1000,
        chunk_candles=chunk,
        max_total_candles=total,
        max_chunks=chunks,
    )


def _key(timeframe: str = "1h") -> str:
    return f"binance:spot:{PAIR.symbol}:{timeframe}"


def _service(
    tmp_path: Path,
    adapter: RangeAdapter,
    *,
    lock_timeout: float = 10,
) -> HistoricalMarketDataService:
    return HistoricalMarketDataService(
        adapter=adapter,
        store=ParquetCandleStore(tmp_path),
        catalog=JsonMarketDataCatalog(tmp_path),
        validator=MarketDataQualityValidator(clock=lambda: utc(2030, 1, 1)),
        max_fetch_candles=1000,
        clock=lambda: utc(2030, 1, 1),
        lock_manager=DatasetLockManager(
            tmp_path,
            timeout_seconds=lock_timeout,
            stale_after_seconds=60,
        ),
    )


def _executor(
    tmp_path: Path,
    adapter: RangeAdapter,
) -> tuple[BackfillExecutor, MarketJobCatalog]:
    jobs = MarketJobCatalog(tmp_path, clock=lambda: utc(2026, 1, 1))
    return (
        BackfillExecutor(
            history=_service(tmp_path, adapter),
            jobs=jobs,
            data_dir=tmp_path,
            lock_timeout_seconds=0,
            lock_stale_after_seconds=60,
        ),
        jobs,
    )


@pytest.mark.parametrize("code", tuple(TIMEFRAMES))
def test_exact_planning_for_all_supported_timeframes(code: str) -> None:
    timeframe = get_timeframe(code)
    start = datetime(2026, 1, 5 if code == "1w" else 1, tzinfo=UTC)
    plan = _planner(chunk=3).backfill(
        _key(code),
        timeframe,
        DataRange(start, start + 7 * timeframe.duration),
    )

    assert [item.expected_candles for item in plan.chunks] == [3, 3, 1]
    assert plan.chunks[0].data_range.start == start
    assert plan.chunks[-1].data_range.end == start + 7 * timeframe.duration
    assert all(
        left.data_range.end == right.data_range.start
        for left, right in zip(plan.chunks, plan.chunks[1:], strict=False)
    )


def test_planner_rejects_misalignment_and_job_limits() -> None:
    timeframe = get_timeframe("1h")
    start = utc(2026, 1, 1)
    with pytest.raises(InvalidDataRangeError, match="alinhado"):
        _planner().backfill(
            _key(),
            timeframe,
            DataRange(start + timedelta(minutes=1), start + timedelta(hours=1)),
        )
    with pytest.raises(InvalidDataRangeError, match="limite total"):
        _planner(total=2).backfill(
            _key(),
            timeframe,
            DataRange(start, start + timedelta(hours=3)),
        )
    with pytest.raises(InvalidDataRangeError, match="chunks"):
        _planner(chunk=1, chunks=2).backfill(
            _key(),
            timeframe,
            DataRange(start, start + timedelta(hours=3)),
        )


def test_job_checkpoint_lifecycle_is_durable_and_plan_is_immutable(tmp_path: Path) -> None:
    plan = _planner().backfill(
        _key(),
        get_timeframe("1h"),
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 3)),
    )
    jobs = MarketJobCatalog(tmp_path, clock=lambda: utc(2026, 1, 1))
    created = jobs.create(plan)
    running = jobs.start(created.job_id)
    checkpoint = jobs.advance(
        running.job_id,
        chunk_index=0,
        fetched=2,
        stored=2,
        duplicates=0,
        requests=1,
    )

    reloaded = MarketJobCatalog(tmp_path).get(plan.job_id)
    assert checkpoint.status is MarketJobStatus.RUNNING
    assert reloaded.next_chunk_index == 1
    assert reloaded.candles_stored == 2
    divergent = _planner(chunk=1).backfill(
        _key(),
        get_timeframe("1h"),
        plan.data_range,
        job_id=plan.job_id,
    )
    with pytest.raises(MarketDataInconsistencyError, match="imutável"):
        jobs.create(divergent)


@pytest.mark.parametrize(
    ("fetched", "stored", "duplicates", "requests"),
    (
        (-1, 0, 0, 0),
        (1, 2, 0, 1),
        (1, 1, 1, 1),
        (1.0, 1, 0, 1),
        (True, 1, 0, 1),
    ),
)
def test_advance_rejects_impossible_chunk_metrics(
    tmp_path: Path,
    fetched: int,
    stored: int,
    duplicates: int,
    requests: int,
) -> None:
    plan = _planner().backfill(
        _key(),
        get_timeframe("1h"),
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 1)),
    )
    jobs = MarketJobCatalog(tmp_path)
    jobs.create(plan)
    jobs.start(plan.job_id)
    with pytest.raises(MarketDataInconsistencyError, match="métricas"):
        jobs.advance(
            plan.job_id,
            chunk_index=0,
            fetched=fetched,
            stored=stored,
            duplicates=duplicates,
            requests=requests,
        )


def test_pause_and_cancel_preserve_confirmed_checkpoint(tmp_path: Path) -> None:
    plan = _planner(chunk=1).backfill(
        _key(),
        get_timeframe("1h"),
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 3)),
    )
    jobs = MarketJobCatalog(tmp_path)
    jobs.create(plan)
    jobs.start(plan.job_id)
    jobs.pause(plan.job_id)
    paused = jobs.advance(
        plan.job_id,
        chunk_index=0,
        fetched=1,
        stored=1,
        duplicates=0,
        requests=1,
    )
    assert paused.status is MarketJobStatus.PAUSED
    assert paused.next_chunk_index == 1

    jobs.start(plan.job_id)
    jobs.cancel(plan.job_id)
    cancelled = jobs.advance(
        plan.job_id,
        chunk_index=1,
        fetched=1,
        stored=1,
        duplicates=0,
        requests=1,
    )
    assert cancelled.status is MarketJobStatus.CANCELLED
    assert cancelled.next_chunk_index == 2
    with pytest.raises(MarketDataInconsistencyError):
        jobs.start(plan.job_id)


def test_abandoned_running_job_is_safely_marked_failed(tmp_path: Path) -> None:
    plan = _planner().backfill(
        _key(),
        get_timeframe("1h"),
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 1)),
    )
    jobs = MarketJobCatalog(tmp_path)
    jobs.create(plan)
    jobs.start(plan.job_id)

    manager = DatasetLockManager(
        tmp_path,
        timeout_seconds=0,
        stale_after_seconds=60,
    )
    with manager.acquire(plan.dataset_key):
        assert jobs.recover_abandoned() == 0
        assert jobs.get(plan.job_id).status is MarketJobStatus.RUNNING

    assert jobs.recover_abandoned() == 1
    assert jobs.get(plan.job_id).status is MarketJobStatus.FAILED
    assert jobs.get(plan.job_id).error_code == "interrupted_job"
    assert jobs.recover_abandoned() == 0


def test_same_dataset_lock_times_out_and_unlocked_file_is_reused(tmp_path: Path) -> None:
    manager = DatasetLockManager(
        tmp_path,
        timeout_seconds=0,
        stale_after_seconds=60,
    )
    second_manager = DatasetLockManager(
        tmp_path,
        timeout_seconds=0,
        stale_after_seconds=60,
    )
    with manager.acquire(_key()):
        with pytest.raises(MarketJobLockTimeoutError):
            second_manager.acquire(_key())
    with second_manager.acquire(_key()):
        pass


def test_same_dataset_lock_excludes_another_process(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(target=_hold_dataset_lock, args=(tmp_path, _key(), ready, release))
    process.start()
    try:
        assert ready.wait(timeout=5)
        with pytest.raises(MarketJobLockTimeoutError):
            manager = DatasetLockManager(
                tmp_path,
                timeout_seconds=0,
                stale_after_seconds=60,
            )
            manager.acquire(_key())
    finally:
        release.set()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert process.exitcode == 0


def test_kernel_releases_dataset_lock_after_process_exit(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    job_id = str(uuid4())
    process = context.Process(target=_abandoned_job_worker, args=(tmp_path, job_id, ready))
    process.start()
    assert ready.wait(timeout=10)
    process.join(timeout=10)
    assert process.exitcode == 0

    manager = DatasetLockManager(tmp_path, timeout_seconds=0, stale_after_seconds=60)
    with manager.acquire(_key()) as lease:
        assert lease.active


def test_lock_metadata_fsync_failure_releases_flock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.market_data.locks as locks_module

    real_fsync = locks_module.os.fsync
    calls = 0

    def fail_once(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic")
        real_fsync(descriptor)

    monkeypatch.setattr(locks_module.os, "fsync", fail_once)
    manager = DatasetLockManager(tmp_path, timeout_seconds=0, stale_after_seconds=60)
    with pytest.raises(MarketDataInconsistencyError, match="metadados"):
        manager.acquire(_key())
    with manager.acquire(_key()) as lease:
        assert lease.active


@pytest.mark.parametrize(
    ("timeout", "stale"),
    (
        (-1.0, 1.0),
        (float("inf"), 1.0),
        (1.0, -1.0),
        (1.0, float("nan")),
    ),
)
def test_dataset_lock_rejects_invalid_durations(
    tmp_path: Path,
    timeout: float,
    stale: float,
) -> None:
    with pytest.raises(MarketDataInconsistencyError, match="configuração"):
        DatasetLockManager(
            tmp_path,
            timeout_seconds=timeout,
            stale_after_seconds=stale,
        )


@pytest.mark.asyncio
async def test_executor_checkpoints_each_chunk_and_completes(tmp_path: Path) -> None:
    adapter = RangeAdapter()
    executor, jobs = _executor(tmp_path, adapter)
    plan = _planner().backfill(
        _key(),
        get_timeframe("1h"),
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 5)),
    )

    result = await executor.run(plan, PAIR)

    assert result.status is MarketJobStatus.COMPLETED
    assert result.chunks_completed == 3
    assert result.stored_count == 5
    assert jobs.get(plan.job_id).next_chunk_index == 3
    assert len(adapter.fetch_calls) == 3
    catalog = JsonMarketDataCatalog(tmp_path)
    metadata = catalog.get_dataset(dataset_key(INSTRUMENT, get_timeframe("1h")))
    assert metadata is not None
    assert metadata.version_algorithm == RAW_DATASET_VERSION_ALGORITHM
    assert metadata.version == ParquetCandleStore(tmp_path).logical_version(
        INSTRUMENT.exchange,
        INSTRUMENT.market_type,
        PAIR,
        get_timeframe("1h"),
    )
    receipts = catalog.list_chunk_receipts()
    assert receipts
    assert all(
        receipt.version_algorithm == RAW_DATASET_VERSION_ALGORITHM
        and receipt.version == receipt.checksum
        for receipt in receipts
    )
    repeated = await executor.run(plan, PAIR)
    assert repeated == result
    assert len(adapter.fetch_calls) == 3


@pytest.mark.asyncio
async def test_failed_job_resumes_from_last_confirmed_chunk(tmp_path: Path) -> None:
    failing = RangeAdapter(fail_on_call=2)
    executor, jobs = _executor(tmp_path, failing)
    plan = _planner().backfill(
        _key(),
        get_timeframe("1h"),
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 5)),
    )

    with pytest.raises(RuntimeError, match="synthetic"):
        await executor.run(plan, PAIR)

    failed = jobs.get(plan.job_id)
    assert failed.status is MarketJobStatus.FAILED
    assert failed.next_chunk_index == 1
    healthy = RangeAdapter()
    resumed, _ = _executor(tmp_path, healthy)
    result = await resumed.resume(plan.job_id, PAIR)

    assert result.status is MarketJobStatus.COMPLETED
    assert [item.start for item in healthy.fetch_calls] == [
        plan.chunks[1].data_range.start,
        plan.chunks[2].data_range.start,
    ]
    continuous_root = tmp_path / "continuous"
    continuous, _ = _executor(continuous_root, RangeAdapter())
    continuous_plan = _planner().backfill(_key(), get_timeframe("1h"), plan.data_range)
    await continuous.run(continuous_plan, PAIR)
    resumed_dataset = JsonMarketDataCatalog(tmp_path).get_dataset(
        dataset_key(INSTRUMENT, get_timeframe("1h"))
    )
    continuous_dataset = JsonMarketDataCatalog(continuous_root).get_dataset(
        dataset_key(INSTRUMENT, get_timeframe("1h"))
    )
    assert resumed_dataset is not None
    assert continuous_dataset is not None
    assert resumed_dataset.version == continuous_dataset.version
    assert resumed_dataset.candle_count == continuous_dataset.candle_count


@pytest.mark.asyncio
async def test_confirmed_chunk_retries_transient_checkpoint_write(tmp_path: Path) -> None:
    adapter = RangeAdapter()
    jobs = TransientCheckpointCatalog(tmp_path)
    executor = BackfillExecutor(
        history=_service(tmp_path, adapter),
        jobs=jobs,
        data_dir=tmp_path,
        lock_timeout_seconds=0,
        lock_stale_after_seconds=60,
    )
    plan = _planner().backfill(
        _key(),
        get_timeframe("1h"),
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 2)),
    )

    result = await executor.run(plan, PAIR)

    assert jobs.failed_once
    assert result.status is MarketJobStatus.COMPLETED
    assert len(adapter.fetch_calls) == 1


@pytest.mark.asyncio
async def test_resume_reconciles_durable_chunk_without_refetching(tmp_path: Path) -> None:
    timeframe = get_timeframe("1h")
    data_range = DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 2))
    plan = _planner().backfill(_key(), timeframe, data_range)
    jobs = MarketJobCatalog(tmp_path)
    jobs.create(plan)
    jobs.start(plan.job_id)
    await _service(tmp_path, RangeAdapter()).ingest(
        PAIR,
        timeframe,
        data_range,
        operation=ChunkOperationContext(plan.job_id, 0, data_range),
    )
    jobs.fail(plan.job_id, "checkpoint_write_failed")
    adapter = RangeAdapter()
    executor = BackfillExecutor(
        history=_service(tmp_path, adapter),
        jobs=jobs,
        data_dir=tmp_path,
        lock_timeout_seconds=0,
        lock_stale_after_seconds=60,
    )

    result = await executor.resume(plan.job_id, PAIR)

    assert result.status is MarketJobStatus.COMPLETED
    assert adapter.fetch_calls == []
    assert result.fetched_count == 2
    assert result.stored_count == 2
    assert result.request_count == 1


@pytest.mark.asyncio
async def test_rolled_back_chunk_never_leaves_success_receipt(tmp_path: Path) -> None:
    timeframe = get_timeframe("1h")
    data_range = DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 2))
    plan = _planner().backfill(_key(), timeframe, data_range)
    store = ParquetCandleStore(tmp_path)
    catalog = JsonMarketDataCatalog(tmp_path)

    def fail_before_commit(step: str) -> None:
        if step == "before_journal_committed":
            raise MarketDataStorageError()

    service = HistoricalMarketDataService(
        adapter=RangeAdapter(),
        store=store,
        catalog=catalog,
        validator=MarketDataQualityValidator(clock=lambda: utc(2030, 1, 1)),
        max_fetch_candles=1000,
        coordinator=MarketDataTransactionCoordinator(
            store,
            catalog,
            failure_hook=fail_before_commit,
        ),
        lock_manager=DatasetLockManager(
            tmp_path,
            timeout_seconds=0,
            stale_after_seconds=60,
        ),
    )

    with pytest.raises(MarketDataStorageError):
        await service.ingest(
            PAIR,
            timeframe,
            data_range,
            operation=ChunkOperationContext(plan.job_id, 0, data_range),
        )

    assert catalog.get_chunk_receipt(plan.job_id, 0) is None
    assert (
        store.first_last_count(
            INSTRUMENT.exchange,
            INSTRUMENT.market_type,
            PAIR,
            timeframe,
        )[2]
        == 0
    )


@pytest.mark.asyncio
async def test_persistent_ingestion_rejects_wrong_or_inactive_lease(tmp_path: Path) -> None:
    service = _service(tmp_path, RangeAdapter(), lock_timeout=0)
    manager = DatasetLockManager(tmp_path, timeout_seconds=0, stale_after_seconds=60)
    wrong = manager.acquire(_key("4h"))
    try:
        with pytest.raises(MarketDataInconsistencyError, match="lease"):
            await service.ingest(
                PAIR,
                get_timeframe("1h"),
                DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 1)),
                lease=wrong,
            )
    finally:
        wrong.__exit__()

    inactive = manager.acquire(_key())
    inactive.__exit__()
    with pytest.raises(MarketDataInconsistencyError, match="lease"):
        await service.ingest(
            PAIR,
            get_timeframe("1h"),
            DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 1)),
            lease=inactive,
        )


@pytest.mark.asyncio
async def test_dry_run_writes_neither_checkpoint_nor_market_data(tmp_path: Path) -> None:
    adapter = RangeAdapter()
    executor, jobs = _executor(tmp_path, adapter)
    plan = _planner().backfill(
        _key(),
        get_timeframe("1h"),
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 2)),
    )

    result = await executor.run(plan, PAIR, dry_run=True)

    assert result.status is MarketJobStatus.PLANNED
    assert adapter.fetch_calls == []
    assert not jobs.path.exists()


@pytest.mark.asyncio
async def test_gap_repair_is_source_backed_and_persistent_gap_fails(tmp_path: Path) -> None:
    timeframe = get_timeframe("1h")
    data_range = DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 2))
    successful_plan = _planner().backfill(
        _key(),
        timeframe,
        data_range,
        job_type=MarketJobType.GAP_REPAIR,
    )
    successful, _ = _executor(tmp_path / "successful", RangeAdapter())
    result = await successful.run(successful_plan, PAIR)
    assert result.status is MarketJobStatus.COMPLETED
    assert result.stored_count == 2

    failed_plan = _planner().backfill(
        _key(),
        timeframe,
        data_range,
        job_type=MarketJobType.GAP_REPAIR,
    )
    failed_root = tmp_path / "failed"
    failed, jobs = _executor(failed_root, EmptyRangeAdapter())
    with pytest.raises(MarketDataInconsistencyError):
        await failed.run(failed_plan, PAIR)
    assert jobs.get(failed_plan.job_id).status is MarketJobStatus.FAILED
    assert (
        ParquetCandleStore(failed_root).first_last_count(
            INSTRUMENT.exchange,
            INSTRUMENT.market_type,
            PAIR,
            timeframe,
        )[2]
        == 0
    )


@pytest.mark.asyncio
async def test_final_version_is_independent_of_chunk_order(tmp_path: Path) -> None:
    timeframe = get_timeframe("1h")
    ranges = (
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 2)),
        DataRange(utc(2026, 1, 1, 2), utc(2026, 1, 1, 4)),
    )
    versions: list[str] = []
    for name, order in (("forward", ranges), ("reverse", tuple(reversed(ranges)))):
        root = tmp_path / name
        service = _service(root, RangeAdapter())
        first_range = order[0]
        await service.ingest(PAIR, timeframe, first_range)
        first_metadata = JsonMarketDataCatalog(root).get_dataset(dataset_key(INSTRUMENT, timeframe))
        assert first_metadata is not None
        await service.ingest(PAIR, timeframe, first_range)
        duplicate_metadata = JsonMarketDataCatalog(root).get_dataset(
            dataset_key(INSTRUMENT, timeframe)
        )
        assert duplicate_metadata is not None
        assert duplicate_metadata.version == first_metadata.version
        for data_range in order[1:]:
            await service.ingest(PAIR, timeframe, data_range)
        metadata = JsonMarketDataCatalog(root).get_dataset(dataset_key(INSTRUMENT, timeframe))
        assert metadata is not None
        assert metadata.version != first_metadata.version
        versions.append(metadata.version)

    assert versions[0] == versions[1]


def test_incremental_overlap_noop_missing_dataset_and_gap_grouping(tmp_path: Path) -> None:
    timeframe = get_timeframe("1h")
    planner = _planner()
    store = ParquetCandleStore(tmp_path)
    with pytest.raises(InvalidDataRangeError, match="início"):
        planner.incremental(
            store,
            INSTRUMENT,
            timeframe,
            now=utc(2026, 1, 1, 3),
            overlap_candles=2,
        )
    initial = planner.incremental(
        store,
        INSTRUMENT,
        timeframe,
        now=utc(2026, 1, 1, 3),
        overlap_candles=2,
        start=utc(2026, 1, 1),
    )
    assert initial.backfill is not None
    assert initial.backfill.job_type is MarketJobType.INCREMENTAL

    gap_plan = planner.gaps(
        store,
        INSTRUMENT,
        timeframe,
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 3)),
    )
    assert gap_plan.gap_ranges == (DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 3)),)
    assert gap_plan.backfill.job_type is MarketJobType.GAP_REPAIR

    receipt = store.upsert(
        (
            candle(utc(2026, 1, 1)),
            candle(utc(2026, 1, 1, 2)),
            candle(utc(2026, 1, 1, 4)),
        )
    )
    receipt.commit()
    noop = planner.incremental(
        store,
        INSTRUMENT,
        timeframe,
        now=utc(2026, 1, 1, 5),
        overlap_candles=0,
    )
    assert noop.action == "NOOP"
    assert noop.backfill is None
    grouped = planner.gaps(
        store,
        INSTRUMENT,
        timeframe,
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 6)),
    )
    assert grouped.gap_ranges == (
        DataRange(utc(2026, 1, 1, 1), utc(2026, 1, 1, 2)),
        DataRange(utc(2026, 1, 1, 3), utc(2026, 1, 1, 4)),
        DataRange(utc(2026, 1, 1, 5), utc(2026, 1, 1, 6)),
    )


def test_two_datasets_commit_concurrently_without_catalog_lost_update(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    processes = [
        context.Process(
            target=_concurrent_ingestion_worker,
            args=(tmp_path, timeframe, start_event),
        )
        for timeframe in ("1h", "4h")
    ]
    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    catalog = JsonMarketDataCatalog(tmp_path)
    datasets = catalog.list_datasets()
    state = json.loads(catalog.path.read_text(encoding="utf-8"))
    assert {item.timeframe for item in datasets} == {"1h", "4h"}
    assert len(state["runs"]) == 2
    assert {item["status"] for item in state["runs"].values()} == {"COMPLETED"}
    assert len(tuple((tmp_path / "market").rglob("*.parquet"))) == 2


@pytest.mark.asyncio
async def test_late_prepared_recovery_reverts_only_its_dataset(
    tmp_path: Path,
) -> None:
    timeframe_a = get_timeframe("1h")
    await _service(tmp_path, RangeAdapter()).ingest(
        PAIR,
        timeframe_a,
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 1)),
    )
    old_a = JsonMarketDataCatalog(tmp_path).get_dataset(_key())
    assert old_a is not None

    context = multiprocessing.get_context("spawn")
    crashed = context.Process(
        target=_crash_ingestion_after_catalog_promotion,
        args=(tmp_path, "1h", utc(2026, 1, 1, 1)),
    )
    crashed.start()
    crashed.join(timeout=15)
    assert crashed.exitcode == 17

    await _service(tmp_path, RangeAdapter()).ingest(
        PAIR,
        get_timeframe("4h"),
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 4)),
    )
    catalog = JsonMarketDataCatalog(tmp_path)
    store = ParquetCandleStore(tmp_path)
    locks = DatasetLockManager(tmp_path, timeout_seconds=0, stale_after_seconds=60)
    coordinator = MarketDataTransactionCoordinator(
        store,
        catalog,
        lock_manager=locks,
    )
    with locks.acquire(_key()) as lease:
        assert coordinator.recover_dataset(_key(), lease) == 1

    recovered_a = catalog.get_dataset(_key())
    committed_b = catalog.get_dataset(_key("4h"))
    assert recovered_a == old_a
    assert committed_b is not None
    assert (
        store.first_last_count(
            INSTRUMENT.exchange,
            INSTRUMENT.market_type,
            PAIR,
            timeframe_a,
        )[2]
        == 1
    )
    assert (
        store.first_last_count(
            INSTRUMENT.exchange,
            INSTRUMENT.market_type,
            PAIR,
            get_timeframe("4h"),
        )[2]
        == 1
    )


@pytest.mark.asyncio
async def test_next_dataset_holder_recovers_prepared_before_read_and_ingest(
    tmp_path: Path,
) -> None:
    timeframe = get_timeframe("1h")
    await _service(tmp_path, RangeAdapter()).ingest(
        PAIR,
        timeframe,
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 1)),
    )
    context = multiprocessing.get_context("spawn")
    crashed = context.Process(
        target=_crash_ingestion_after_catalog_promotion,
        args=(tmp_path, "1h", utc(2026, 1, 1, 1)),
    )
    crashed.start()
    crashed.join(timeout=15)
    assert crashed.exitcode == 17

    service = _service(tmp_path, RangeAdapter())
    inspected = service.inspect(INSTRUMENT, timeframe)
    assert inspected.candle_count == 1
    assert not tuple((tmp_path / "market" / ".transactions").glob("journal-*.json"))
    result = await service.ingest(
        PAIR,
        timeframe,
        DataRange(utc(2026, 1, 1, 1), utc(2026, 1, 1, 2)),
    )
    assert result.stored_count == 1
    assert service.inspect(INSTRUMENT, timeframe).candle_count == 2


@pytest.mark.asyncio
async def test_receipt_read_during_catalog_promotion_is_complete(
    tmp_path: Path,
) -> None:
    timeframe = get_timeframe("1h")
    job_id = str(uuid4())
    data_range = DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 1))
    await _service(tmp_path, RangeAdapter()).ingest(
        PAIR,
        timeframe,
        data_range,
        operation=ChunkOperationContext(job_id, 0, data_range),
    )
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    writer = context.Process(
        target=_pause_ingestion_worker,
        args=(
            tmp_path,
            "4h",
            utc(2026, 1, 1),
            "before_catalog_promoted",
            ready,
            release,
        ),
    )
    writer.start()
    assert ready.wait(timeout=10)
    result = context.Queue()
    reader = context.Process(target=_read_receipt_worker, args=(tmp_path, job_id, result))
    reader.start()
    release.set()
    writer.join(timeout=15)
    reader.join(timeout=15)
    assert writer.exitcode == 0
    assert reader.exitcode == 0
    assert result.get(timeout=2) is True


@pytest.mark.asyncio
async def test_inspect_waits_for_same_dataset_partition_promotion(
    tmp_path: Path,
) -> None:
    timeframe = get_timeframe("1h")
    await _service(tmp_path, RangeAdapter()).ingest(
        PAIR,
        timeframe,
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 1)),
    )
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    writer = context.Process(
        target=_pause_ingestion_worker,
        args=(
            tmp_path,
            "1h",
            utc(2026, 1, 1, 1),
            "partition_promoted:0",
            ready,
            release,
        ),
    )
    writer.start()
    assert ready.wait(timeout=10)
    result = context.Queue()
    reader = context.Process(target=_inspect_dataset_worker, args=(tmp_path, result))
    reader.start()
    release.set()
    writer.join(timeout=15)
    reader.join(timeout=15)
    assert writer.exitcode == 0
    assert reader.exitcode == 0
    count, _version = result.get(timeout=2)
    assert count == 2


@pytest.mark.asyncio
async def test_backfill_lease_blocks_same_dataset_fetch_but_not_another(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    started = context.Event()
    release = context.Event()
    process = context.Process(
        target=_blocking_backfill_worker,
        args=(tmp_path, started, release),
    )
    process.start()
    try:
        assert started.wait(timeout=10)
        service = _service(tmp_path, RangeAdapter(), lock_timeout=0)
        with pytest.raises(MarketJobLockTimeoutError):
            await service.ingest(
                PAIR,
                get_timeframe("1h"),
                DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 1)),
            )
        other = await service.ingest(
            PAIR,
            get_timeframe("4h"),
            DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 4)),
        )
        assert other.stored_count == 1
    finally:
        release.set()
        process.join(timeout=15)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert process.exitcode == 0
    assert {item.timeframe for item in JsonMarketDataCatalog(tmp_path).list_datasets()} == {
        "1h",
        "4h",
    }


def test_jobs_json_retains_concurrent_updates_from_two_processes(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    job_ids = (str(uuid4()), str(uuid4()))
    processes = [
        context.Process(
            target=_concurrent_job_worker,
            args=(tmp_path, timeframe, job_id, start_event),
        )
        for timeframe, job_id in zip(("1h", "4h"), job_ids, strict=True)
    ]
    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    jobs = MarketJobCatalog(tmp_path)
    assert {jobs.get(job_id).status for job_id in job_ids} == {MarketJobStatus.COMPLETED}


@pytest.mark.asyncio
async def test_dead_process_job_is_recovered_and_resumable(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    job_id = str(uuid4())
    process = context.Process(target=_abandoned_job_worker, args=(tmp_path, job_id, ready))
    process.start()
    assert ready.wait(timeout=10)
    process.join(timeout=10)
    assert process.exitcode == 0

    jobs = MarketJobCatalog(tmp_path)
    assert jobs.recover_abandoned() == 1
    assert jobs.recover_abandoned() == 0
    assert jobs.get(job_id).status is MarketJobStatus.FAILED
    executor, _ = _executor(tmp_path, RangeAdapter())
    result = await executor.resume(job_id, PAIR)
    assert result.status is MarketJobStatus.COMPLETED
    assert result.chunks_completed == 1


@pytest.mark.asyncio
async def test_gap_noop_has_no_executable_plan_or_job(tmp_path: Path) -> None:
    timeframe = get_timeframe("1h")
    store = ParquetCandleStore(tmp_path)
    receipt = store.upsert((candle(utc(2026, 1, 1)), candle(utc(2026, 1, 1, 1))))
    receipt.commit()
    repair = _planner().gaps(
        store,
        INSTRUMENT,
        timeframe,
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 2)),
    )
    assert repair.backfill is None
    assert repair.gap_ranges == ()

    malformed = BackfillPlan(
        job_id=str(uuid4()),
        dataset_key=_key(),
        timeframe=timeframe,
        data_range=DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 2)),
        chunks=(),
        expected_candles=0,
        chunk_candles=2,
    )
    executor, jobs = _executor(tmp_path, RangeAdapter())
    with pytest.raises(MarketDataInconsistencyError, match="possuir chunks"):
        await executor.run(malformed, PAIR)
    assert not jobs.path.exists()


@pytest.mark.parametrize(
    "corruption",
    (
        "external_key",
        "index_mismatch",
        "negative_counter",
        "overlap",
        "expected_count",
        "terminal_without_finished",
        "stored_exceeds_fetched",
        "non_integer_metric",
        "inexact_interval_coverage",
    ),
)
def test_jobs_checkpoint_rejects_structural_corruption(
    tmp_path: Path,
    corruption: str,
) -> None:
    plan = _planner(chunk=1).backfill(
        _key(),
        get_timeframe("1h"),
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 2)),
    )
    jobs = MarketJobCatalog(tmp_path)
    jobs.create(plan)
    state = json.loads(jobs.path.read_text(encoding="utf-8"))
    raw = state[plan.job_id]
    if corruption == "external_key":
        state["00000000-0000-0000-0000-000000000001"] = state.pop(plan.job_id)
    elif corruption == "index_mismatch":
        raw["next_chunk_index"] = 1
    elif corruption == "negative_counter":
        raw["candles_stored"] = -1
    elif corruption == "overlap":
        raw["chunk_ranges"][1][0] = raw["chunk_ranges"][0][0]
        raw["plan_checksum"] = _record_checksum_for_test(raw)
    elif corruption == "expected_count":
        raw["candles_expected"] = 3
        raw["plan_checksum"] = _record_checksum_for_test(raw)
    elif corruption == "terminal_without_finished":
        raw["status"] = "COMPLETED"
        raw["next_chunk_index"] = 2
        raw["chunks_completed"] = 2
    elif corruption == "stored_exceeds_fetched":
        raw["candles_fetched"] = 1
        raw["candles_stored"] = 2
    elif corruption == "non_integer_metric":
        raw["request_count"] = 1.5
    elif corruption == "inexact_interval_coverage":
        raw["chunk_ranges"][1][0] = utc(2026, 1, 1, 3).isoformat()
        raw["chunk_ranges"][1][1] = utc(2026, 1, 1, 4).isoformat()
        raw["end"] = utc(2026, 1, 1, 4).isoformat()
        raw["plan_checksum"] = _record_checksum_for_test(raw)
    jobs.path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(MarketDataStorageError):
        jobs.get(plan.job_id)


def _record_checksum_for_test(raw: dict[str, object]) -> str:
    from hashlib import sha256

    ranges = raw["chunk_ranges"]
    assert isinstance(ranges, list)
    payload = "|".join(
        (
            str(raw["dataset_key"]),
            str(raw["job_type"]),
            str(raw["timeframe"]),
            str(raw["start"]),
            str(raw["end"]),
            str(raw["candles_expected"]),
            str(raw["chunk_candles"]),
            *(f"{item[0]}:{item[1]}" for item in ranges),
        )
    )
    return sha256(payload.encode()).hexdigest()
