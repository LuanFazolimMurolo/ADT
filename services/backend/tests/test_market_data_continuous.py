"""Phase 5-02 continuous market-data collection tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from app.market_data.continuous import (
    ContinuousCollectionPolicy,
    ContinuousCollectionRunner,
    ContinuousCollectionService,
    ContinuousCollectionState,
    ContinuousCollectionStateStore,
    ContinuousCollectionTarget,
    ContinuousCycleStatus,
    ContinuousTargetResult,
    ContinuousTargetStatus,
    collection_target_from_text,
)
from app.market_data.domain import DataRange, Instrument, Timeframe, TradingPair
from app.market_data.errors import (
    MarketDataInconsistencyError,
    MarketDataStorageError,
    MarketJobLockTimeoutError,
    UnknownInstrumentError,
)
from app.market_data.locks import DatasetLockManager
from app.market_data.planning import (
    BackfillChunk,
    BackfillPlan,
    BackfillResult,
    IncrementalUpdatePlan,
    MarketJobStatus,
    MarketJobType,
)
from app.market_data.storage import ParquetCandleStore
from app.market_data.timeframes import get_timeframe
from tests.market_data_helpers import INSTRUMENT, utc

TIMEFRAME = get_timeframe("1h")
TARGET = ContinuousCollectionTarget(TradingPair("BTC", "USDT"), TIMEFRAME, 24)
ETH_TARGET = ContinuousCollectionTarget(TradingPair("ETH", "USDT"), TIMEFRAME, 24)


class FakeInstrumentLookup:
    def __init__(self, instruments: dict[str, Instrument] | None = None) -> None:
        self._instruments = (
            {INSTRUMENT.symbol: INSTRUMENT} if instruments is None else instruments
        )
        self.calls: list[str] = []

    async def get_asset(self, pair: TradingPair) -> Instrument:
        self.calls.append(pair.symbol)
        try:
            return self._instruments[pair.symbol]
        except KeyError:
            raise UnknownInstrumentError() from None


class FakeHistory:
    def __init__(self) -> None:
        self.leases: list[str] = []

    @contextmanager
    def dataset_lease(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
    ) -> Iterator[object]:
        del timeframe
        self.leases.append(instrument.symbol)
        yield object()


class FakeStore:
    def __init__(self, last_by_symbol: dict[str, object] | None = None) -> None:
        self.last_by_symbol = last_by_symbol or {}
        self.calls: list[str] = []

    def first_last_count(
        self,
        exchange: object,
        market_type: object,
        pair: TradingPair,
        timeframe: object,
    ) -> tuple[object, object, int]:
        del exchange, market_type, timeframe
        self.calls.append(pair.symbol)
        last = self.last_by_symbol.get(pair.symbol)
        return (None, last, 0 if last is None else 1)


class FakePlanner:
    def __init__(self, plans: dict[str, IncrementalUpdatePlan]) -> None:
        self._plans = plans
        self.calls: list[tuple[str, int, object]] = []

    def incremental(
        self,
        store: ParquetCandleStore,
        instrument: Instrument,
        timeframe: object,
        *,
        now: object,
        overlap_candles: int,
        start: object = None,
    ) -> IncrementalUpdatePlan:
        del store, timeframe, now
        self.calls.append((instrument.symbol, overlap_candles, start))
        return self._plans[instrument.symbol]


class FakeExecutor:
    def __init__(self, results: dict[str, BackfillResult]) -> None:
        self._results = results
        self.calls: list[str] = []

    async def run(
        self,
        plan: BackfillPlan,
        pair: TradingPair,
        *,
        dry_run: bool = False,
    ) -> BackfillResult:
        assert dry_run is False
        self.calls.append(pair.symbol)
        return self._results[plan.dataset_key]


class FailingExecutor:
    async def run(
        self,
        plan: BackfillPlan,
        pair: TradingPair,
        *,
        dry_run: bool = False,
    ) -> BackfillResult:
        del plan, pair, dry_run
        raise MarketDataStorageError("Falha controlada no executor.")


def _plan(target: ContinuousCollectionTarget) -> IncrementalUpdatePlan:
    end = utc(2026, 8, 2, 12)
    start = end - target.timeframe.duration
    backfill = BackfillPlan(
        job_id=str(uuid4()),
        dataset_key=target.dataset_key,
        timeframe=target.timeframe,
        data_range=DataRange(start, end),
        chunks=(BackfillChunk(0, DataRange(start, end), 1),),
        expected_candles=1,
        chunk_candles=1,
        job_type=MarketJobType.INCREMENTAL,
    )
    return IncrementalUpdatePlan("RUN", backfill, None, end)


def _completed(plan: IncrementalUpdatePlan) -> BackfillResult:
    assert plan.backfill is not None
    return BackfillResult(
        job_id=plan.backfill.job_id,
        status=MarketJobStatus.COMPLETED,
        chunks_completed=1,
        total_chunks=1,
        fetched_count=1,
        stored_count=1,
        duplicate_count=0,
        request_count=1,
    )


def _service(
    plans: dict[str, IncrementalUpdatePlan],
    results: dict[str, BackfillResult],
    *,
    lookup: FakeInstrumentLookup | None = None,
    store: FakeStore | None = None,
) -> ContinuousCollectionService:
    return ContinuousCollectionService(
        instruments=lookup or FakeInstrumentLookup(),
        history=FakeHistory(),
        planner=FakePlanner(plans),
        executor=FakeExecutor(results),
        store=cast(ParquetCandleStore, store or FakeStore()),
        policy=ContinuousCollectionPolicy(30, 2, 10),
        clock=lambda: utc(2026, 8, 2, 12) + timedelta(minutes=30),
    )


@pytest.mark.asyncio
async def test_cycle_updates_and_noops_in_canonical_target_order() -> None:
    btc_plan = _plan(TARGET)
    eth_instrument = replace(INSTRUMENT, pair=ETH_TARGET.pair, native_symbol="ETHUSDT")
    noop = IncrementalUpdatePlan("NOOP", None, utc(2026, 8, 2, 11), utc(2026, 8, 2, 12))
    lookup = FakeInstrumentLookup(
        {INSTRUMENT.symbol: INSTRUMENT, eth_instrument.symbol: eth_instrument}
    )
    service = _service(
        {INSTRUMENT.symbol: btc_plan, eth_instrument.symbol: noop},
        {TARGET.dataset_key: _completed(btc_plan)},
        lookup=lookup,
    )

    state = await service.collect_cycle((TARGET, ETH_TARGET), cycle_index=1)

    assert state.status is ContinuousCycleStatus.COMPLETED
    assert [item.status for item in state.results] == [
        ContinuousTargetStatus.UPDATED,
        ContinuousTargetStatus.NOOP,
    ]
    assert state.results[0].stored_count == 1
    assert state.results[1].job_id is None
    assert len(state.checksum) == len(state.cycle_id) == 64


@pytest.mark.asyncio
async def test_target_failure_is_isolated_and_sanitized() -> None:
    eth_instrument = replace(INSTRUMENT, pair=ETH_TARGET.pair, native_symbol="ETHUSDT")
    btc_plan = _plan(TARGET)
    lookup = FakeInstrumentLookup(
        {INSTRUMENT.symbol: INSTRUMENT, eth_instrument.symbol: eth_instrument}
    )
    service = _service(
        {INSTRUMENT.symbol: btc_plan},
        {TARGET.dataset_key: _completed(btc_plan)},
        lookup=lookup,
    )

    state = await service.collect_cycle((TARGET, ETH_TARGET), cycle_index=5)

    assert state.status is ContinuousCycleStatus.PARTIALLY_FAILED
    assert state.results[0].status is ContinuousTargetStatus.UPDATED
    assert state.results[1].status is ContinuousTargetStatus.FAILED
    assert state.results[1].error_code == "collection_target_failed"


@pytest.mark.asyncio
async def test_all_failed_cycle_is_explicit() -> None:
    service = _service({}, {}, lookup=FakeInstrumentLookup({}))

    state = await service.collect_cycle((TARGET,), cycle_index=1)

    assert state.status is ContinuousCycleStatus.FAILED
    assert state.results[0].error_code == "unknown_instrument"


@pytest.mark.asyncio
async def test_failed_execution_preserves_planned_job_identity() -> None:
    plan = _plan(TARGET)
    assert plan.backfill is not None
    service = ContinuousCollectionService(
        instruments=FakeInstrumentLookup(),
        history=FakeHistory(),
        planner=FakePlanner({INSTRUMENT.symbol: plan}),
        executor=FailingExecutor(),
        store=cast(ParquetCandleStore, FakeStore()),
        policy=ContinuousCollectionPolicy(30, 2, 10),
        clock=lambda: utc(2026, 8, 2, 12),
    )

    state = await service.collect_cycle((TARGET,), cycle_index=1)

    assert state.status is ContinuousCycleStatus.FAILED
    assert state.results[0].job_id == plan.backfill.job_id
    assert state.results[0].error_code == "market_data_storage"


@pytest.mark.asyncio
async def test_cycle_rejects_reidentified_plan_with_wrong_close_boundary() -> None:
    plan = _plan(TARGET)
    hostile = replace(plan, latest_closed_end=utc(2026, 8, 2, 13))
    executor = FakeExecutor({})
    service = ContinuousCollectionService(
        instruments=FakeInstrumentLookup(),
        history=FakeHistory(),
        planner=FakePlanner({INSTRUMENT.symbol: hostile}),
        executor=executor,
        store=cast(ParquetCandleStore, FakeStore()),
        policy=ContinuousCollectionPolicy(30, 2, 10),
        clock=lambda: utc(2026, 8, 2, 12),
    )

    state = await service.collect_cycle((TARGET,), cycle_index=1)

    assert state.status is ContinuousCycleStatus.FAILED
    assert state.results[0].job_id is None
    assert executor.calls == []


@pytest.mark.asyncio
async def test_cycle_rejects_executor_result_from_another_job() -> None:
    plan = _plan(TARGET)
    mismatched = replace(_completed(plan), job_id=str(uuid4()))
    service = _service(
        {INSTRUMENT.symbol: plan},
        {TARGET.dataset_key: mismatched},
    )

    state = await service.collect_cycle((TARGET,), cycle_index=1)

    assert state.status is ContinuousCycleStatus.FAILED
    assert plan.backfill is not None
    assert state.results[0].job_id == plan.backfill.job_id
    assert state.results[0].error_code == "market_data_inconsistency"


@pytest.mark.asyncio
async def test_cycle_skips_planner_when_no_new_candle_closed() -> None:
    planner = FakePlanner({})
    store = FakeStore({INSTRUMENT.symbol: utc(2026, 8, 2, 11)})
    service = ContinuousCollectionService(
        instruments=FakeInstrumentLookup(),
        history=FakeHistory(),
        planner=planner,
        executor=FakeExecutor({}),
        store=cast(ParquetCandleStore, store),
        policy=ContinuousCollectionPolicy(30, 2, 10),
        clock=lambda: utc(2026, 8, 2, 12),
    )

    state = await service.collect_cycle((TARGET,), cycle_index=1)

    assert state.results[0].status is ContinuousTargetStatus.NOOP
    assert planner.calls == []
    assert store.calls == [INSTRUMENT.symbol]


@pytest.mark.parametrize(
    "target",
    [
        ContinuousCollectionTarget(TradingPair("BTC", "USDT"), TIMEFRAME, 1),
        collection_target_from_text("btc/usdt:1h", bootstrap_candles=10),
    ],
)
def test_target_is_canonical_and_parsed(target: ContinuousCollectionTarget) -> None:
    assert target.pair.symbol == "BTC/USDT"
    assert target.timeframe.code == "1h"


def test_bootstrap_start_maps_datetime_overflow_to_domain_error() -> None:
    target = ContinuousCollectionTarget(
        TradingPair("BTC", "USDT"),
        get_timeframe("1d"),
        1_000_000,
    )

    with pytest.raises(MarketDataInconsistencyError):
        target.bootstrap_start(utc(2026, 8, 2))


@pytest.mark.parametrize(
    "value",
    ["BTCUSDT", "BTC/USDT", "BTC/USDT:", ":1h", "BTC/USDT:2h"],
)
def test_target_parser_rejects_invalid_values(value: str) -> None:
    with pytest.raises(Exception):
        collection_target_from_text(value, bootstrap_candles=10)


@pytest.mark.parametrize(
    "policy",
    [
        (True, 2, 10),
        (0, 2, 10),
        (float("inf"), 2, 10),
        (30, True, 10),
        (30, 2, 0),
    ],
)
def test_policy_rejects_hostile_values(policy: tuple[object, object, object]) -> None:
    with pytest.raises(MarketDataInconsistencyError):
        ContinuousCollectionPolicy(*policy)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_cycle_rejects_duplicate_or_unsorted_targets() -> None:
    service = _service({}, {})
    with pytest.raises(MarketDataInconsistencyError):
        await service.collect_cycle((TARGET, TARGET), cycle_index=1)
    with pytest.raises(MarketDataInconsistencyError):
        await service.collect_cycle((ETH_TARGET, TARGET), cycle_index=1)


def _state(cycle_index: int = 1) -> ContinuousCollectionState:
    result = ContinuousTargetResult(
        target=TARGET,
        status=ContinuousTargetStatus.NOOP,
        started_at=utc(2026, 8, 2, 12),
        finished_at=utc(2026, 8, 2, 12),
        latest_closed_end=utc(2026, 8, 2, 12),
    )
    return ContinuousCollectionState(
        cycle_index=cycle_index,
        status=ContinuousCycleStatus.COMPLETED,
        policy=ContinuousCollectionPolicy(60, 2, 10),
        started_at=utc(2026, 8, 2, 12),
        finished_at=utc(2026, 8, 2, 12),
        next_cycle_at=utc(2026, 8, 2, 12) + timedelta(minutes=1),
        results=(result,),
    )


def test_state_store_round_trip_and_checksum_detection(tmp_path: Path) -> None:
    store = ContinuousCollectionStateStore(tmp_path)
    state = _state()

    assert store.read() is None
    assert store.write(state) == state
    assert store.read() == state

    payload = json.loads(store.path.read_text())
    payload["cycle_index"] = 2
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MarketDataStorageError):
        store.read()


def test_state_store_rejects_noncanonical_or_duplicate_json(tmp_path: Path) -> None:
    store = ContinuousCollectionStateStore(tmp_path)
    state = store.write(_state())
    canonical = store.path.read_text(encoding="utf-8")

    store.path.write_text(json.dumps(json.loads(canonical), indent=2), encoding="utf-8")
    with pytest.raises(MarketDataStorageError):
        store.read()

    duplicate = canonical.replace(
        '{"checksum":',
        f'{{"checksum":"{state.checksum}","checksum":',
        1,
    )
    store.path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(MarketDataStorageError):
        store.read()


def test_state_store_is_idempotent_and_rejects_cycle_gaps(tmp_path: Path) -> None:
    store = ContinuousCollectionStateStore(tmp_path)
    state = _state()

    assert store.write(state) == state
    assert store.write(state) == state
    with pytest.raises(MarketDataStorageError):
        store.write(_state(cycle_index=3))


def test_state_rejects_mutated_result_or_identity() -> None:
    state = _state()
    object.__setattr__(state.results[0], "status", ContinuousTargetStatus.FAILED)
    with pytest.raises(MarketDataInconsistencyError):
        ContinuousCollectionState.__post_init__(state)

    clean = _state()
    object.__setattr__(clean, "checksum", "0" * 64)
    with pytest.raises(MarketDataInconsistencyError):
        ContinuousCollectionState.__post_init__(clean)


def test_state_rejects_noncanonical_next_cycle_even_when_reidentified() -> None:
    state = _state()

    with pytest.raises(MarketDataInconsistencyError):
        replace(
            state,
            next_cycle_at=state.next_cycle_at + timedelta(seconds=1),
            cycle_id="",
            checksum="",
        )


@pytest.mark.parametrize("error_code", ["falha_Ç", "FAILURE", " falha", "falha-"])
def test_failed_result_rejects_noncanonical_error_code(error_code: str) -> None:
    with pytest.raises(MarketDataInconsistencyError):
        ContinuousTargetResult(
            target=TARGET,
            status=ContinuousTargetStatus.FAILED,
            started_at=utc(2026, 8, 2, 12),
            finished_at=utc(2026, 8, 2, 12),
            latest_closed_end=utc(2026, 8, 2, 12),
            error_code=error_code,
        )


@pytest.mark.asyncio
async def test_runner_increments_cycle_and_uses_fixed_cadence(tmp_path: Path) -> None:
    plan = IncrementalUpdatePlan("NOOP", None, None, utc(2026, 8, 2, 12))
    service = _service({INSTRUMENT.symbol: plan}, {})
    sleeps: list[float] = []
    startup_calls: list[str] = []

    async def sleeper(delay: float) -> None:
        sleeps.append(delay)

    runner = ContinuousCollectionRunner(
        service=service,
        state_store=ContinuousCollectionStateStore(tmp_path),
        lock_manager=DatasetLockManager(tmp_path, timeout_seconds=0, stale_after_seconds=60),
        sleeper=sleeper,
        clock=lambda: utc(2026, 8, 2, 12) + timedelta(minutes=30),
        startup_hook=lambda: startup_calls.append("called"),
    )

    latest = await runner.run((TARGET,), max_cycles=2)

    assert latest.cycle_index == 2
    assert sleeps == [30.0]
    assert startup_calls == ["called"]
    assert ContinuousCollectionStateStore(tmp_path).read() == latest


@pytest.mark.asyncio
async def test_runner_rejects_concurrent_collector_for_same_volume(tmp_path: Path) -> None:
    plan = IncrementalUpdatePlan("NOOP", None, None, utc(2026, 8, 2, 12))
    manager = DatasetLockManager(tmp_path, timeout_seconds=0, stale_after_seconds=60)
    startup_calls: list[str] = []
    runner = ContinuousCollectionRunner(
        service=_service({INSTRUMENT.symbol: plan}, {}),
        state_store=ContinuousCollectionStateStore(tmp_path),
        lock_manager=manager,
        startup_hook=lambda: startup_calls.append("called"),
    )

    with manager.acquire("adt:continuous-market-collection:v1"):
        with pytest.raises(MarketJobLockTimeoutError):
            await runner.run((TARGET,), max_cycles=1)
    assert startup_calls == []
