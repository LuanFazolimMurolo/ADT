"""Candle-engine tests for temporal safety, lifecycle and deterministic state."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app.backtesting.domain import (
    BacktestConfig,
    ExecutionAssumptions,
    FeeModel,
    Fill,
    InstrumentConstraints,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    RiskLimits,
    SlippageModel,
    StrategyDescriptor,
    TimeInForce,
)
from app.backtesting.engine import DeterministicBacktestEngine
from app.backtesting.errors import (
    MaximumEventsExceededError,
    SnapshotChangedError,
    SnapshotInvalidError,
    UnsupportedBacktestMarketError,
)
from app.backtesting.strategy import BuyAndHoldExample, NoOpStrategy, StrategyContext
from app.market_data.datasets import DatasetSnapshot
from app.market_data.domain import Candle, DataRange, Exchange, MarketType, Timeframe
from app.market_data.timeframes import get_timeframe
from tests.market_data_helpers import candle, utc


@dataclass
class FakeSnapshotReader:
    snapshot: DatasetSnapshot
    candles: tuple[Candle, ...]
    changed: bool = False
    open_calls: int = 0
    yielded: int = 0
    verify_calls: int = 0

    def open_snapshot(self, snapshot_id: str) -> DatasetSnapshot:
        self.open_calls += 1
        assert snapshot_id == self.snapshot.snapshot_id
        return self.snapshot

    def iter_candles(self) -> Iterator[Candle]:
        for item in self.candles:
            self.yielded += 1
            yield item

    def verify_unchanged(self) -> DatasetSnapshot:
        self.verify_calls += 1
        if self.changed:
            return replace(self.snapshot, checksum="d" * 64)
        return self.snapshot


@dataclass
class RecordingStrategy:
    start_intents: tuple[OrderIntent, ...] = ()
    candle_intents: dict[int, tuple[OrderIntent, ...]] = field(default_factory=dict)
    fill_intents: tuple[OrderIntent, ...] = ()
    descriptor: StrategyDescriptor = field(
        default_factory=lambda: StrategyDescriptor("recording-test", "1")
    )
    candle_contexts: list[StrategyContext] = field(default_factory=list)
    fill_contexts: list[StrategyContext] = field(default_factory=list)

    def on_start(self, context: StrategyContext) -> tuple[OrderIntent, ...]:
        assert context.current_candle is None
        return self.start_intents

    def on_candle(
        self,
        context: StrategyContext,
        current: Candle,
    ) -> tuple[OrderIntent, ...]:
        assert context.current_candle == current
        self.candle_contexts.append(context)
        return self.candle_intents.get(context.candle_index, ())

    def on_fill(
        self,
        context: StrategyContext,
        fill: Fill,
    ) -> tuple[OrderIntent, ...]:
        assert fill.candle_index >= 0
        self.fill_contexts.append(context)
        return self.fill_intents

    def on_end(self, context: StrategyContext) -> None:
        assert context.current_candle is not None


def _snapshot(count: int) -> DatasetSnapshot:
    start = utc(2026, 1, 1)
    return DatasetSnapshot(
        snapshot_id="a" * 64,
        dataset_key="derived:binance:spot:BTC/USDT:1h",
        dataset_version="b" * 64,
        checksum="c" * 64,
        data_range=DataRange(start, start + timedelta(hours=count)),
        partitions=("partitions/year=2026/month=01/candles.parquet",),
        manifest_path="dataset-manifest.json",
        created_at=start.isoformat(),
    )


def _candles(*closes: str) -> tuple[Candle, ...]:
    start = utc(2026, 1, 1)
    return tuple(
        candle(
            start + timedelta(hours=index),
            timeframe=get_timeframe("1h"),
            open_price=close,
            high=str(Decimal(close) + Decimal("5")),
            low=str(Decimal(close) - Decimal("5")),
            close=close,
        )
        for index, close in enumerate(closes)
    )


def _config(
    snapshot: DatasetSnapshot,
    descriptor: StrategyDescriptor,
    *,
    capital: str = "1000",
    history_window: int = 3,
    risk_limits: RiskLimits | None = None,
    force_close: bool = False,
) -> BacktestConfig:
    return BacktestConfig(
        snapshot_id=snapshot.snapshot_id,
        data_range=snapshot.data_range,
        strategy=descriptor,
        initial_capital=Decimal(capital),
        execution=ExecutionAssumptions(
            fees=FeeModel(Decimal("0"), Decimal("0")),
            slippage=SlippageModel(fixed_bps=Decimal("0")),
            force_close_at_end=force_close,
        ),
        constraints=InstrumentConstraints(
            minimum_quantity=Decimal("0.001"),
            quantity_step=Decimal("0.001"),
            price_tick=Decimal("0.01"),
            minimum_notional=Decimal("1"),
        ),
        risk_limits=risk_limits or RiskLimits(),
        history_window=history_window,
        max_candles=100,
        max_orders=100,
        max_events=1000,
        engine_version="phase3a-test",
        schema_version=1,
    )


def _market_buy(quantity: str = "1") -> OrderIntent:
    return OrderIntent(OrderSide.BUY, OrderType.MARKET, Decimal(quantity))


def test_noop_marks_constant_equity_and_verifies_snapshot() -> None:
    rows = _candles("100", "110", "90")
    snapshot = _snapshot(len(rows))
    reader = FakeSnapshotReader(snapshot, rows)
    strategy = NoOpStrategy()

    result = DeterministicBacktestEngine(reader).run(
        _config(snapshot, strategy.descriptor),
        strategy,
    )

    assert result.candles_processed == 3
    assert result.orders == ()
    assert result.fills == ()
    assert [point.equity for point in result.equity_curve] == [Decimal("1000")] * 3
    assert result.final_portfolio.quote_cash == Decimal("1000")
    assert reader.open_calls == reader.verify_calls == 1
    assert reader.yielded == 3


def test_on_start_order_may_fill_first_candle_without_seeing_it() -> None:
    rows = _candles("100", "110")
    snapshot = _snapshot(len(rows))
    strategy = RecordingStrategy(start_intents=(_market_buy(),))

    result = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        _config(snapshot, strategy.descriptor),
        strategy,
    )

    assert result.fills[0].candle_index == 0
    assert result.fills[0].execution_price == Decimal("100")
    assert result.orders[0].created_candle_index == -1
    assert result.orders[0].eligible_candle_index == 0


def test_order_created_on_t_fills_only_at_next_open() -> None:
    rows = _candles("100", "120", "130")
    snapshot = _snapshot(len(rows))
    strategy = RecordingStrategy(candle_intents={0: (_market_buy(),)})

    result = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        _config(snapshot, strategy.descriptor),
        strategy,
    )

    assert len(result.fills) == 1
    fill = result.fills[0]
    assert fill.candle_index == 1
    assert fill.execution_price == Decimal("120")
    assert result.orders[0].created_candle_index == 0
    assert result.orders[0].eligible_candle_index == 1
    assert result.orders[0].status is OrderStatus.FILLED


def test_order_created_on_last_candle_never_creates_nonexistent_fill() -> None:
    rows = _candles("100", "110")
    snapshot = _snapshot(len(rows))
    strategy = RecordingStrategy(candle_intents={1: (_market_buy(),)})

    result = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        _config(snapshot, strategy.descriptor),
        strategy,
    )

    assert result.fills == ()
    assert result.orders[0].status is OrderStatus.CANCELLED


def test_on_fill_sees_only_previously_closed_history() -> None:
    rows = _candles("100", "120", "130")
    snapshot = _snapshot(len(rows))
    strategy = RecordingStrategy(candle_intents={0: (_market_buy(),)})

    DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        _config(snapshot, strategy.descriptor),
        strategy,
    )

    assert len(strategy.fill_contexts) == 1
    context = strategy.fill_contexts[0]
    assert context.current_candle == rows[0]
    assert context.history == (rows[0],)
    assert all(item.open_time < rows[1].open_time for item in context.history)
    assert context.portfolio.base_quantity == Decimal("1")


def test_strategy_history_is_bounded_and_never_contains_future_candles() -> None:
    rows = _candles("100", "101", "102", "103")
    snapshot = _snapshot(len(rows))
    strategy = RecordingStrategy()

    DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        _config(snapshot, strategy.descriptor, history_window=2),
        strategy,
    )

    assert [len(context.history) for context in strategy.candle_contexts] == [1, 2, 2, 2]
    for context in strategy.candle_contexts:
        assert context.history[-1] == context.current_candle
        assert all(item.open_time <= context.current_candle.open_time for item in context.history)


def test_ioc_limit_expires_on_first_eligible_candle_when_not_touched() -> None:
    rows = _candles("100", "110", "120")
    snapshot = _snapshot(len(rows))
    intent = OrderIntent(
        OrderSide.BUY,
        OrderType.LIMIT,
        Decimal("1"),
        time_in_force=TimeInForce.IOC,
        limit_price=Decimal("90"),
    )
    strategy = RecordingStrategy(candle_intents={0: (intent,)})

    result = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        _config(snapshot, strategy.descriptor),
        strategy,
    )

    assert result.fills == ()
    assert result.orders[0].status is OrderStatus.EXPIRED


def test_deterministic_priority_prevents_second_sell_from_exceeding_position() -> None:
    rows = _candles("100", "100", "120", "120")
    snapshot = _snapshot(len(rows))
    sell_one = OrderIntent(OrderSide.SELL, OrderType.MARKET, Decimal("1"))
    strategy = RecordingStrategy(
        candle_intents={
            0: (_market_buy(),),
            1: (sell_one, sell_one),
        }
    )

    result = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        _config(snapshot, strategy.descriptor),
        strategy,
    )

    assert [fill.side for fill in result.fills] == [OrderSide.BUY, OrderSide.SELL]
    assert result.orders[1].status is OrderStatus.FILLED
    assert result.orders[2].status is OrderStatus.CANCELLED
    assert result.orders[2].rejection_code is None
    assert result.final_portfolio.base_quantity == 0


def test_drawdown_halt_cancels_open_orders_and_rejects_new_intents() -> None:
    rows = _candles("100", "100", "80", "70")
    snapshot = _snapshot(len(rows))
    waiting_limit = OrderIntent(
        OrderSide.SELL,
        OrderType.LIMIT,
        Decimal("1"),
        limit_price=Decimal("200"),
    )
    strategy = RecordingStrategy(
        candle_intents={
            0: (_market_buy("10"),),
            1: (replace(waiting_limit, quantity=Decimal("10")),),
            2: (OrderIntent(OrderSide.SELL, OrderType.MARKET, Decimal("10")),),
        }
    )
    limits = RiskLimits(max_drawdown_pct=Decimal("10"), stop_on_max_drawdown=True)

    result = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        _config(snapshot, strategy.descriptor, risk_limits=limits),
        strategy,
    )

    assert result.risk_halt
    assert result.orders[1].status is OrderStatus.CANCELLED
    assert result.orders[2].status is OrderStatus.REJECTED
    assert result.orders[2].rejection_code == "risk_halt_active"


def test_force_close_uses_last_close_without_future_candle() -> None:
    rows = _candles("100", "120")
    snapshot = _snapshot(len(rows))
    strategy = BuyAndHoldExample(Decimal("1"))

    result = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        _config(snapshot, strategy.descriptor, force_close=True),
        strategy,
    )

    assert len(result.fills) == 2
    assert result.fills[-1].reason.value == "FORCE_CLOSE"
    assert result.fills[-1].candle_index == 1
    assert result.fills[-1].base_price == Decimal("120")
    assert result.final_portfolio.base_quantity == 0
    assert result.final_portfolio.quote_cash == Decimal("1000")


def test_snapshot_change_after_iteration_rejects_result() -> None:
    rows = _candles("100", "110")
    snapshot = _snapshot(len(rows))
    strategy = NoOpStrategy()

    with pytest.raises(SnapshotChangedError):
        DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows, changed=True)).run(
            _config(snapshot, strategy.descriptor),
            strategy,
        )


def test_open_candle_and_unsupported_market_are_rejected() -> None:
    snapshot = _snapshot(1)
    strategy = NoOpStrategy()
    opened = replace(_candles("100")[0], is_closed=False)
    with pytest.raises(SnapshotInvalidError, match="aberto"):
        DeterministicBacktestEngine(FakeSnapshotReader(snapshot, (opened,))).run(
            _config(snapshot, strategy.descriptor),
            strategy,
        )

    base = _candles("100")[0]
    forex = Candle(
        exchange=Exchange.BINANCE,
        market_type=MarketType.FOREX,
        symbol=base.symbol,
        timeframe=Timeframe("1h", timedelta(hours=1)),
        open_time=base.open_time,
        close_time=base.close_time,
        open=base.open,
        high=base.high,
        low=base.low,
        close=base.close,
        volume=base.volume,
        quote_volume=base.quote_volume,
        trade_count=base.trade_count,
        is_closed=True,
        source="test",
    )
    with pytest.raises(UnsupportedBacktestMarketError):
        DeterministicBacktestEngine(FakeSnapshotReader(snapshot, (forex,))).run(
            _config(snapshot, strategy.descriptor),
            strategy,
        )


def test_force_close_respects_total_order_limit() -> None:
    rows = _candles("100", "120")
    snapshot = _snapshot(len(rows))
    strategy = BuyAndHoldExample(Decimal("1"))
    config = replace(
        _config(snapshot, strategy.descriptor, force_close=True),
        max_orders=1,
        risk_limits=RiskLimits(max_open_orders=1, max_total_orders=1),
    )

    with pytest.raises(MaximumEventsExceededError):
        DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
            config,
            strategy,
        )
