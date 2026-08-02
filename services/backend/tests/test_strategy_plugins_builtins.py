"""Built-in strategy lifecycle and deterministic engine integration tests."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from app.backtesting.domain import (
    BacktestConfig,
    ExecutionAssumptions,
    FeeModel,
    InstrumentConstraints,
    OrderSide,
    PortfolioSnapshot,
    RiskLimits,
    SlippageModel,
)
from app.backtesting.engine import DeterministicBacktestEngine
from app.backtesting.strategy import StrategyContext
from app.market_data.datasets import DatasetSnapshot
from app.market_data.domain import Candle, DataRange
from app.market_data.timeframes import get_timeframe
from app.strategies.builtins import EmaCrossExampleStrategy
from app.strategies.catalog import builtin_indicator_capabilities
from app.strategies.registry import StrategyPluginRegistry
from tests.market_data_helpers import candle, utc


@dataclass
class FakeSnapshotReader:
    snapshot: DatasetSnapshot
    candles: tuple[Candle, ...]

    def open_snapshot(self, snapshot_id: str) -> DatasetSnapshot:
        assert snapshot_id == self.snapshot.snapshot_id
        return self.snapshot

    def iter_candles(self, data_range: DataRange | None = None) -> Iterator[Candle]:
        yield from (
            candle
            for candle in self.candles
            if data_range is None or data_range.start <= candle.open_time < data_range.end
        )

    def verify_unchanged(self) -> DatasetSnapshot:
        return self.snapshot


def _candles(*closes: str) -> tuple[Candle, ...]:
    start = utc(2026, 1, 1)
    return tuple(
        candle(
            start + timedelta(hours=index),
            timeframe=get_timeframe("1h"),
            open_price=close,
            high=str(Decimal(close) + Decimal("1")),
            low=str(Decimal(close) - Decimal("1")),
            close=close,
        )
        for index, close in enumerate(closes)
    )


def _snapshot(rows: tuple[Candle, ...]) -> DatasetSnapshot:
    start = rows[0].open_time
    return DatasetSnapshot(
        snapshot_id="a" * 64,
        dataset_key="derived:binance:spot:BTC/USDT:1h",
        dataset_version="b" * 64,
        checksum="c" * 64,
        data_range=DataRange(start, rows[-1].close_time + timedelta(microseconds=1)),
        partitions=("partitions/year=2026/month=01/candles.parquet",),
        manifest_path="dataset-manifest.json",
        created_at=start.isoformat(),
    )


def _portfolio(base: str = "0") -> PortfolioSnapshot:
    base_quantity = Decimal(base)
    average = Decimal("1") if base_quantity else Decimal("0")
    cost_basis = base_quantity * average
    return PortfolioSnapshot(
        quote_cash=Decimal("1000"),
        base_quantity=base_quantity,
        average_entry_price=average,
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage_cost=Decimal("0"),
        equity=Decimal("1000"),
        peak_equity=Decimal("1000"),
        drawdown=Decimal("0"),
        cost_basis=cost_basis,
        drawdown_pct=Decimal("0"),
    )


def _context(
    rows: tuple[Candle, ...],
    *,
    base: str = "0",
    risk_halt: bool = False,
) -> StrategyContext:
    return StrategyContext(
        snapshot=_snapshot(rows),
        candle_index=len(rows) - 1,
        current_candle=rows[-1],
        history=rows,
        portfolio=_portfolio(base),
        open_orders=(),
        last_fill=None,
        risk_halt=risk_halt,
    )


def _config(snapshot: DatasetSnapshot, strategy: EmaCrossExampleStrategy) -> BacktestConfig:
    return BacktestConfig(
        snapshot_id=snapshot.snapshot_id,
        data_range=snapshot.data_range,
        strategy=strategy.descriptor,
        initial_capital=Decimal("1000"),
        execution=ExecutionAssumptions(
            fees=FeeModel(Decimal("0"), Decimal("0")),
            slippage=SlippageModel(fixed_bps=Decimal("0")),
            force_close_at_end=False,
        ),
        constraints=InstrumentConstraints(
            minimum_quantity=Decimal("0.001"),
            quantity_step=Decimal("0.001"),
            price_tick=Decimal("0.01"),
            minimum_notional=Decimal("1"),
        ),
        risk_limits=RiskLimits(),
        history_window=100,
        max_candles=100,
        max_orders=100,
        max_events=1000,
        engine_version="phase3c-test",
        schema_version=2,
    )


def test_ema_example_waits_for_warmup_and_confirmed_relation_change() -> None:
    strategy = EmaCrossExampleStrategy(2, 3, Decimal("1"))
    first = _candles("3", "2")
    warm = _candles("3", "2", "1")
    crossed = _candles("3", "2", "1", "4")

    strategy.on_start(_context(first))
    assert strategy.on_candle(_context(first), first[-1]) == ()
    assert strategy.on_candle(_context(warm), warm[-1]) == ()

    intents = strategy.on_candle(_context(crossed), crossed[-1])

    assert len(intents) == 1
    assert intents[0].side is OrderSide.BUY
    assert intents[0].quantity == Decimal("1")
    assert intents[0].client_tag == "ema-cross-entry"


def test_ema_example_emits_exit_for_observed_downward_cross() -> None:
    strategy = EmaCrossExampleStrategy(2, 3, Decimal("1"))
    before = _candles("3", "2", "1")
    upward = _candles("3", "2", "1", "4")
    downward = _candles("3", "2", "1", "4", "0.5")

    strategy.on_start(_context(before))
    assert strategy.on_candle(_context(before), before[-1]) == ()
    assert strategy.on_candle(_context(upward), upward[-1])

    intents = strategy.on_candle(_context(downward, base="0.75"), downward[-1])

    assert len(intents) == 1
    assert intents[0].side is OrderSide.SELL
    assert intents[0].quantity == Decimal("0.75")
    assert intents[0].client_tag == "ema-cross-exit"


def test_ema_example_submits_nothing_during_risk_halt() -> None:
    strategy = EmaCrossExampleStrategy(2, 3, Decimal("1"))
    before = _candles("3", "2", "1")
    crossed = _candles("3", "2", "1", "4")
    strategy.on_start(_context(before))
    strategy.on_candle(_context(before), before[-1])

    assert strategy.on_candle(_context(crossed, risk_halt=True), crossed[-1]) == ()


def test_on_start_resets_state_for_repeated_deterministic_runs() -> None:
    strategy = EmaCrossExampleStrategy(2, 3, Decimal("1"))
    warm = _candles("3", "2", "1")
    crossed = _candles("3", "2", "1", "4")

    strategy.on_start(_context(warm))
    strategy.on_candle(_context(warm), warm[-1])
    assert strategy.on_candle(_context(crossed), crossed[-1])

    strategy.on_start(_context(warm))
    assert strategy.on_candle(_context(crossed), crossed[-1]) == ()


def test_plugin_strategy_runs_through_engine_without_future_candles() -> None:
    rows = _candles("3", "2", "1", "4", "5", "0.5", "1")
    snapshot = _snapshot(rows)
    strategy = StrategyPluginRegistry.builtins().build(
        "ema-cross-example",
        "1",
        {"fast_period": 2, "slow_period": 3, "quantity": Decimal("1")},
        available_indicators=builtin_indicator_capabilities(),
    )

    first = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        _config(snapshot, strategy),
        strategy,
    )
    second_strategy = StrategyPluginRegistry.builtins().build(
        "ema-cross-example",
        "1",
        {"fast_period": 2, "slow_period": 3, "quantity": Decimal("1")},
        available_indicators=builtin_indicator_capabilities(),
    )
    second = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        _config(snapshot, second_strategy),
        second_strategy,
    )

    assert first.orders == second.orders
    assert first.fills == second.fills
    assert first.equity_curve == second.equity_curve
    assert [order.intent.side for order in first.orders] == [OrderSide.BUY, OrderSide.SELL]
    assert first.orders[0].created_candle_index == 3
    assert first.fills[0].candle_index == 4
