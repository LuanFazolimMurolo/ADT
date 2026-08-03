"""Candle-engine tests for temporal safety, lifecycle and deterministic state."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal
from typing import cast

import pytest

from app.backtesting.domain import (
    BacktestConfig,
    EvaluationBacktestConfig,
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
    evaluation_range_for,
    strategy_lifecycle_version_for,
)
from app.backtesting.engine import DeterministicBacktestEngine
from app.backtesting.errors import (
    MaximumEventsExceededError,
    SnapshotChangedError,
    SnapshotInvalidError,
    StrategyFailureError,
    UnsupportedBacktestMarketError,
)
from app.backtesting.strategy import BuyAndHoldExample, NoOpStrategy, StrategyContext
from app.market_data.datasets import DatasetSnapshot
from app.market_data.domain import Candle, DataRange, Exchange, MarketType, Timeframe
from app.market_data.timeframes import get_timeframe
from app.strategies.builtins import EmaCrossExampleStrategy
from app.strategies.catalog import builtin_indicator_capabilities
from app.strategies.registry import StrategyPluginRegistry
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

    def iter_candles(self, data_range: DataRange | None = None) -> Iterator[Candle]:
        for item in self.candles:
            if data_range is not None and not (data_range.start <= item.open_time < data_range.end):
                continue
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
    warmup_contexts: list[StrategyContext] = field(default_factory=list)
    fill_contexts: list[StrategyContext] = field(default_factory=list)
    start_contexts: list[StrategyContext] = field(default_factory=list)

    def on_start(self, context: StrategyContext) -> tuple[OrderIntent, ...]:
        assert context.current_candle is None
        self.start_contexts.append(context)
        return self.start_intents

    def on_warmup_candle(
        self,
        context: StrategyContext,
        candle: Candle,
    ) -> None:
        assert context.current_candle == candle
        self.warmup_contexts.append(context)

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


@dataclass
class LegacyLifecycleStrategy:
    descriptor: StrategyDescriptor = field(
        default_factory=lambda: StrategyDescriptor("legacy-test", "1")
    )
    start_calls: int = 0

    def on_start(self, context: StrategyContext) -> tuple[OrderIntent, ...]:
        del context
        self.start_calls += 1
        return ()

    def on_candle(self, context: StrategyContext, current: Candle) -> tuple[OrderIntent, ...]:
        del context, current
        return ()

    def on_fill(self, context: StrategyContext, fill: Fill) -> tuple[OrderIntent, ...]:
        del context, fill
        return ()

    def on_end(self, context: StrategyContext) -> None:
        del context


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


def _evaluation_config(
    snapshot: DatasetSnapshot,
    descriptor: StrategyDescriptor,
    *,
    evaluation_start: int,
    lifecycle_version: int = 2,
) -> EvaluationBacktestConfig:
    base = _config(snapshot, descriptor)
    return EvaluationBacktestConfig(
        snapshot_id=base.snapshot_id,
        data_range=base.data_range,
        strategy=base.strategy,
        initial_capital=base.initial_capital,
        execution=base.execution,
        constraints=base.constraints,
        risk_limits=base.risk_limits,
        history_window=base.history_window,
        max_candles=base.max_candles,
        max_orders=base.max_orders,
        max_events=base.max_events,
        engine_version=base.engine_version,
        schema_version=base.schema_version,
        evaluation_range=DataRange(
            snapshot.data_range.start + timedelta(hours=evaluation_start),
            snapshot.data_range.end,
        ),
        strategy_lifecycle_version=lifecycle_version,
    )


def test_terminal_order_policy_requires_exact_bool_before_execution() -> None:
    rows = _candles("100")
    snapshot = _snapshot(len(rows))
    strategy = RecordingStrategy()

    with pytest.raises(StrategyFailureError, match="política terminal"):
        DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
            _config(snapshot, strategy.descriptor),
            strategy,
            cancel_open_orders_at_end=1,  # type: ignore[arg-type]
        )

    assert not strategy.start_contexts


def test_evaluation_warmup_only_observes_history_and_has_no_financial_events() -> None:
    rows = _candles("100", "101", "102", "103")
    snapshot = _snapshot(len(rows))
    reader = FakeSnapshotReader(snapshot, rows)
    strategy = RecordingStrategy()

    result = DeterministicBacktestEngine(reader).run(
        _evaluation_config(snapshot, strategy.descriptor, evaluation_start=2), strategy
    )

    assert result.candles_processed == 2
    assert [context.current_candle for context in strategy.candle_contexts] == list(rows[2:])
    assert strategy.start_contexts[0].history == ()
    assert len(strategy.warmup_contexts) == 2
    assert [context.current_candle for context in strategy.warmup_contexts] == list(rows[:2])
    assert [context.history[-1] for context in strategy.warmup_contexts] == list(rows[:2])
    assert strategy.candle_contexts[0].history == rows[:3]
    assert result.orders == ()
    assert result.fills == ()
    assert result.ledger[0].event_time == rows[2].open_time
    assert [point.candle_index for point in result.equity_curve] == [0, 1]


def test_legacy_lifecycle_runs_unchanged_when_warmup_is_zero() -> None:
    rows = _candles("100", "101")
    snapshot = _snapshot(len(rows))
    strategy = LegacyLifecycleStrategy()

    result = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        _config(snapshot, strategy.descriptor), strategy
    )

    assert result.candles_processed == 2
    assert strategy.start_calls == 1


def test_legacy_lifecycle_is_rejected_before_callbacks_or_candle_iteration_for_warmup() -> None:
    rows = _candles("100", "101")
    snapshot = _snapshot(len(rows))
    reader = FakeSnapshotReader(snapshot, rows)
    strategy = LegacyLifecycleStrategy()

    with pytest.raises(StrategyFailureError, match="lifecycle de warmup"):
        DeterministicBacktestEngine(reader).run(
            _evaluation_config(snapshot, strategy.descriptor, evaluation_start=1), strategy
        )

    assert strategy.start_calls == 0
    assert reader.yielded == 0


@pytest.mark.parametrize("name", ["no-op", "ema-cross-example"])
def test_builtin_lifecycle_one_is_not_promoted_by_an_accidental_warmup_method(
    name: str,
) -> None:
    rows = _candles("100", "101")
    snapshot = _snapshot(len(rows))
    reader = FakeSnapshotReader(snapshot, rows)
    parameters = {} if name == "no-op" else {"quantity": Decimal("0.1")}
    indicators = () if name == "no-op" else builtin_indicator_capabilities()
    strategy = StrategyPluginRegistry.builtins().build(
        name,
        "1",
        parameters,
        available_indicators=indicators,
    )
    assert callable(getattr(strategy, "on_warmup_candle"))
    config = _evaluation_config(
        snapshot,
        strategy.descriptor,
        evaluation_start=1,
        lifecycle_version=2,
    )
    object.__setattr__(config, "strategy_lifecycle_version", 1)

    with pytest.raises(StrategyFailureError, match="configuração de lifecycle"):
        DeterministicBacktestEngine(reader).run(config, strategy)

    assert reader.open_calls == 0
    assert reader.yielded == 0


@pytest.mark.parametrize("value", [True, "2", 3])
def test_evaluation_config_rejects_hostile_lifecycle_values(value: object) -> None:
    snapshot = _snapshot(2)
    strategy = RecordingStrategy()
    config = _evaluation_config(snapshot, strategy.descriptor, evaluation_start=0)
    object.__setattr__(config, "strategy_lifecycle_version", value)

    with pytest.raises(ValueError, match="strategy_lifecycle_version"):
        config.__post_init__()


def test_evaluation_config_rejects_lifecycle_one_with_positive_warmup() -> None:
    snapshot = _snapshot(2)
    strategy = RecordingStrategy()

    with pytest.raises(ValueError, match="lifecycle version 1"):
        _evaluation_config(
            snapshot,
            strategy.descriptor,
            evaluation_start=1,
            lifecycle_version=1,
        )


def test_evaluation_config_accepts_lifecycle_one_with_zero_warmup() -> None:
    snapshot = _snapshot(2)
    strategy = RecordingStrategy()

    config = _evaluation_config(
        snapshot,
        strategy.descriptor,
        evaluation_start=0,
        lifecycle_version=1,
    )

    assert config.evaluation_range == config.data_range


def test_evaluation_config_accepts_lifecycle_two_with_positive_warmup() -> None:
    snapshot = _snapshot(2)
    strategy = RecordingStrategy()

    config = _evaluation_config(
        snapshot,
        strategy.descriptor,
        evaluation_start=1,
        lifecycle_version=2,
    )

    assert config.evaluation_range.start > config.data_range.start


def test_evaluation_range_helper_rejects_non_config_without_attribute_leak() -> None:
    with pytest.raises(ValueError, match="backtest config"):
        evaluation_range_for(object())  # type: ignore[arg-type]


def test_both_config_helpers_reject_lifecycle_one_warmup_mutation() -> None:
    snapshot = _snapshot(2)
    strategy = RecordingStrategy()
    config = _evaluation_config(
        snapshot,
        strategy.descriptor,
        evaluation_start=1,
        lifecycle_version=2,
    )
    object.__setattr__(config, "strategy_lifecycle_version", 1)

    for helper in (evaluation_range_for, strategy_lifecycle_version_for):
        with pytest.raises(ValueError, match="lifecycle version 1"):
            helper(config)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evaluation_range", object()),
        ("data_range", object()),
        ("strategy_lifecycle_version", True),
    ],
)
def test_both_config_helpers_reject_hostile_config_fields(
    field: str,
    value: object,
) -> None:
    snapshot = _snapshot(2)
    strategy = RecordingStrategy()
    config = _evaluation_config(snapshot, strategy.descriptor, evaluation_start=1)
    object.__setattr__(config, field, value)

    for helper in (evaluation_range_for, strategy_lifecycle_version_for):
        with pytest.raises(ValueError):
            helper(config)


@pytest.mark.parametrize("field", ["data_range", "evaluation_range"])
def test_evaluation_config_rejects_hostile_range_types_before_bound_access(
    field: str,
) -> None:
    snapshot = _snapshot(2)
    strategy = RecordingStrategy()
    config = _evaluation_config(snapshot, strategy.descriptor, evaluation_start=1)
    object.__setattr__(config, field, object())

    with pytest.raises(ValueError, match="range"):
        config.__post_init__()


def test_ema_state_is_warmed_before_cross_on_first_evaluation_candle() -> None:
    rows = _candles("100", "90", "80", "120", "121")
    snapshot = _snapshot(len(rows))
    strategy = EmaCrossExampleStrategy(2, 3, Decimal("1"))

    result = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        _evaluation_config(snapshot, strategy.descriptor, evaluation_start=3), strategy
    )

    assert len(result.orders) == 1
    assert result.orders[0].created_candle_index == 0
    assert result.orders[0].created_at == rows[3].close_time
    assert len(result.fills) == 1
    assert result.fills[0].candle_index == 1
    assert result.fills[0].event_time == rows[4].open_time


def test_warmup_callback_cannot_return_order_intents() -> None:
    @dataclass
    class InvalidWarmupStrategy(RecordingStrategy):
        def on_warmup_candle(
            self,
            context: StrategyContext,
            candle: Candle,
        ) -> None:
            super().on_warmup_candle(context, candle)
            return cast(None, (_market_buy(),))

    rows = _candles("100", "101", "102")
    snapshot = _snapshot(len(rows))
    strategy = InvalidWarmupStrategy()

    with pytest.raises(StrategyFailureError, match="on_warmup_candle"):
        DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
            _evaluation_config(snapshot, strategy.descriptor, evaluation_start=1),
            strategy,
        )


def test_start_intent_can_fill_at_first_evaluation_candle() -> None:
    rows = _candles("100", "101", "102", "103")
    snapshot = _snapshot(len(rows))
    strategy = RecordingStrategy(start_intents=(_market_buy(),))

    result = DeterministicBacktestEngine(FakeSnapshotReader(snapshot, rows)).run(
        _evaluation_config(snapshot, strategy.descriptor, evaluation_start=2), strategy
    )

    assert len(result.orders) == len(result.fills) == 1
    assert result.fills[0].candle_index == 0
    assert result.fills[0].event_time == rows[2].open_time
    assert all(point.event_time > rows[2].open_time for point in result.equity_curve)


def test_evaluation_config_rejects_post_context_or_early_ranges() -> None:
    snapshot = _snapshot(3)
    strategy = RecordingStrategy()
    valid = _evaluation_config(snapshot, strategy.descriptor, evaluation_start=1)

    with pytest.raises(ValueError, match="evaluation_range"):
        replace(
            valid,
            evaluation_range=DataRange(
                snapshot.data_range.start - timedelta(hours=1), snapshot.data_range.end
            ),
        )
    with pytest.raises(ValueError, match="evaluation_range"):
        replace(
            valid,
            evaluation_range=DataRange(
                snapshot.data_range.start,
                snapshot.data_range.end - timedelta(hours=1),
            ),
        )


def test_context_reader_does_not_expose_post_evaluation_candles() -> None:
    rows = _candles("100", "101", "102", "103", "104", "105")
    snapshot = _snapshot(len(rows))
    strategy = RecordingStrategy()
    config = _evaluation_config(snapshot, strategy.descriptor, evaluation_start=2)
    end = snapshot.data_range.start + timedelta(hours=4)
    config = replace(
        config,
        data_range=DataRange(snapshot.data_range.start, end),
        evaluation_range=DataRange(snapshot.data_range.start + timedelta(hours=2), end),
    )
    reader = FakeSnapshotReader(snapshot, rows)

    result = DeterministicBacktestEngine(reader).run(config, strategy)

    assert reader.yielded == 4
    assert result.candles_processed == 2
    assert strategy.candle_contexts[-1].current_candle == rows[3]


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
