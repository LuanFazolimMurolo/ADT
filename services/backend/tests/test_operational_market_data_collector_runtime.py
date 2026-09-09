"""Gate 2E runtime integration tests for operational collectors."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

import app.operational_market_data_collectors as collectors
from app.market_data.continuous import (
    ContinuousCollectionStateStore,
    ContinuousCycleStatus,
)
from app.market_data.domain import (
    Instrument,
    Timeframe,
    TradingPair,
)
from app.market_data.errors import (
    MarketDataInconsistencyError,
    MarketJobLockTimeoutError,
    UnknownInstrumentError,
)
from app.market_data.locks import DatasetLockManager
from app.market_data.planning import (
    BackfillPlan,
    BackfillResult,
    IncrementalUpdatePlan,
)
from app.market_data.storage import ParquetCandleStore
from app.services.operational_market_data_collector_runtime import (
    OperationalMarketDataCollectorRuntimeExecutor,
)
from tests.market_data_helpers import INSTRUMENT

NOW = datetime(
    2026,
    9,
    9,
    18,
    30,
    tzinfo=UTC,
)

LAST_OPEN = datetime(
    2026,
    9,
    9,
    17,
    0,
    tzinfo=UTC,
)


def _specification(
    *,
    interval_seconds: int = 60,
    overlap_candles: int = 2,
) -> collectors.OperationalMarketDataCollectorSpecification:
    return collectors.OperationalMarketDataCollectorSpecification(
        schema_version=1,
        collector_contract_version=1,
        scope=collectors.OperationalMarketDataCollectorScope.BINANCE_SPOT_RAW,
        targets=(
            collectors.OperationalMarketDataCollectorTarget(
                symbol="BTC/USDT",
                timeframe="1h",
                bootstrap_candles=24,
            ),
        ),
        interval_seconds=interval_seconds,
        overlap_candles=overlap_candles,
    )


class _InstrumentLookup:
    async def get_asset(
        self,
        pair: TradingPair,
    ) -> Instrument:
        assert pair == INSTRUMENT.pair
        return INSTRUMENT


class _MissingInstrumentLookup:
    async def get_asset(
        self,
        pair: TradingPair,
    ) -> Instrument:
        del pair
        raise UnknownInstrumentError()


class _History:
    @contextmanager
    def dataset_lease(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
    ) -> Iterator[object]:
        del instrument, timeframe
        yield object()


class _CurrentStore:
    def __init__(self) -> None:
        self.calls = 0

    def first_last_count(
        self,
        exchange: object,
        market_type: object,
        pair: TradingPair,
        timeframe: object,
    ) -> tuple[None, datetime, int]:
        del exchange, market_type, pair, timeframe
        self.calls += 1
        return None, LAST_OPEN, 1


class _UnexpectedPlanner:
    def incremental(
        self,
        store: ParquetCandleStore,
        instrument: Instrument,
        timeframe: Timeframe,
        *,
        now: datetime,
        overlap_candles: int,
        start: datetime | None = None,
    ) -> IncrementalUpdatePlan:
        del (
            store,
            instrument,
            timeframe,
            now,
            overlap_candles,
            start,
        )
        raise AssertionError("planner must not run for current local coverage")


class _UnexpectedExecutor:
    async def run(
        self,
        plan: BackfillPlan,
        pair: TradingPair,
        *,
        dry_run: bool = False,
    ) -> BackfillResult:
        del plan, pair, dry_run
        raise AssertionError("executor must not run for current local coverage")


def _runtime(
    tmp_path: Path,
    current_time: list[datetime],
    events: list[str],
    *,
    missing_instrument: bool = False,
) -> tuple[
    OperationalMarketDataCollectorRuntimeExecutor,
    ContinuousCollectionStateStore,
    DatasetLockManager,
    _CurrentStore,
]:
    state_store = ContinuousCollectionStateStore(tmp_path)

    lock_manager = DatasetLockManager(
        tmp_path,
        timeout_seconds=0,
        stale_after_seconds=60,
    )

    raw_store = _CurrentStore()

    async def recovery_hook() -> None:
        events.append("recovery")

    runtime = OperationalMarketDataCollectorRuntimeExecutor(
        instruments=(_MissingInstrumentLookup() if missing_instrument else _InstrumentLookup()),
        history=_History(),
        planner=_UnexpectedPlanner(),
        executor=_UnexpectedExecutor(),
        store=cast(
            ParquetCandleStore,
            raw_store,
        ),
        state_store=state_store,
        lock_manager=lock_manager,
        recovery_hook=recovery_hook,
        clock=lambda: current_time[0],
    )

    return (
        runtime,
        state_store,
        lock_manager,
        raw_store,
    )


def _authority_hook(
    events: list[str],
):
    async def startup_hook() -> None:
        events.append("authority")

    return startup_hook


@pytest.mark.asyncio
async def test_runtime_maps_frozen_spec_and_executes_one_complete_cycle(
    tmp_path: Path,
) -> None:
    current_time = [NOW]
    events: list[str] = []

    runtime, state_store, _locks, raw_store = _runtime(
        tmp_path,
        current_time,
        events,
    )

    executed = await runtime.execute_cycle(
        _specification(),
        startup_hook=_authority_hook(events),
    )

    assert executed is True
    assert events == [
        "authority",
        "recovery",
        "authority",
    ]

    state = state_store.read()

    assert state is not None
    assert state.cycle_index == 1
    assert state.policy.interval_seconds == 60
    assert state.policy.overlap_candles == 2
    assert state.policy.max_targets == 1
    assert tuple(result.target.key for result in state.results) == ("BTC/USDT:1h",)
    assert raw_store.calls == 1


@pytest.mark.asyncio
async def test_matching_contract_before_next_cycle_returns_not_due(
    tmp_path: Path,
) -> None:
    current_time = [NOW]
    events: list[str] = []

    runtime, state_store, _locks, raw_store = _runtime(
        tmp_path,
        current_time,
        events,
    )

    specification = _specification()

    assert await runtime.execute_cycle(
        specification,
        startup_hook=_authority_hook(events),
    )

    first = state_store.read()

    assert first is not None
    assert first.cycle_index == 1

    current_time[0] = NOW.replace(
        second=30,
    )

    events.clear()

    executed = await runtime.execute_cycle(
        specification,
        startup_hook=_authority_hook(events),
    )

    assert executed is False
    assert events == ["authority"]
    assert state_store.read() == first
    assert raw_store.calls == 1


@pytest.mark.asyncio
async def test_changed_contract_does_not_inherit_old_next_cycle_boundary(
    tmp_path: Path,
) -> None:
    current_time = [NOW]
    events: list[str] = []

    runtime, state_store, _locks, raw_store = _runtime(
        tmp_path,
        current_time,
        events,
    )

    assert await runtime.execute_cycle(
        _specification(),
        startup_hook=_authority_hook(events),
    )

    current_time[0] = NOW.replace(
        second=30,
    )

    events.clear()

    changed = replace(
        _specification(),
        overlap_candles=3,
    )

    executed = await runtime.execute_cycle(
        changed,
        startup_hook=_authority_hook(events),
    )

    assert executed is True

    state = state_store.read()

    assert state is not None
    assert state.cycle_index == 2
    assert state.policy.overlap_candles == 3
    assert raw_store.calls == 2
    assert events == [
        "authority",
        "recovery",
        "authority",
    ]


@pytest.mark.asyncio
async def test_corrupt_local_state_fails_closed_before_physical_cycle(
    tmp_path: Path,
) -> None:
    current_time = [NOW]
    events: list[str] = []

    runtime, state_store, _locks, raw_store = _runtime(
        tmp_path,
        current_time,
        events,
    )

    state_store.path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    state_store.path.write_text(
        '{"corrupt":true}',
        encoding="utf-8",
    )

    with pytest.raises(
        MarketDataInconsistencyError,
        match="estado local",
    ):
        await runtime.execute_cycle(
            _specification(),
            startup_hook=_authority_hook(events),
        )

    assert events == ["authority"]
    assert raw_store.calls == 0


@pytest.mark.asyncio
async def test_volume_lock_conflict_prevents_authority_hook_and_cycle(
    tmp_path: Path,
) -> None:
    current_time = [NOW]
    events: list[str] = []

    runtime, _state_store, locks, raw_store = _runtime(
        tmp_path,
        current_time,
        events,
    )

    with locks.acquire("adt:continuous-market-collection:v1"):
        with pytest.raises(MarketJobLockTimeoutError):
            await runtime.execute_cycle(
                _specification(),
                startup_hook=_authority_hook(events),
            )

    assert events == []
    assert raw_store.calls == 0


@pytest.mark.asyncio
async def test_target_failure_remains_complete_cycle_outcome(
    tmp_path: Path,
) -> None:
    current_time = [NOW]
    events: list[str] = []

    runtime, state_store, _locks, raw_store = _runtime(
        tmp_path,
        current_time,
        events,
        missing_instrument=True,
    )

    executed = await runtime.execute_cycle(
        _specification(),
        startup_hook=_authority_hook(events),
    )

    assert executed is True

    state = state_store.read()

    assert state is not None
    assert state.cycle_index == 1
    assert state.status is ContinuousCycleStatus.FAILED
    assert len(state.results) == 1
    assert state.results[0].error_code is not None

    # A target-level failure is still a complete collector cycle.
    assert raw_store.calls == 0
