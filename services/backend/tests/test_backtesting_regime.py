"""Opt-in market-regime observation in deterministic backtests."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal

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
    OrderType,
    RegimeAwareBacktestConfig,
    RegimeAwareEvaluationBacktestConfig,
    RiskLimits,
    SlippageModel,
    StrategyDescriptor,
    market_regime_policy_for,
    validate_backtest_config,
)
from app.backtesting.engine import DeterministicBacktestEngine
from app.backtesting.serialization import canonical_value
from app.backtesting.strategy import StrategyContext
from app.indicators.regime import (
    MarketRegimeKind,
    MarketRegimePolicy,
    TrendDirection,
)
from app.market_data.datasets import DatasetSnapshot
from app.market_data.domain import Candle, DataRange
from app.market_data.timeframes import get_timeframe
from tests.market_data_helpers import candle, utc


@dataclass
class _Reader:
    snapshot: DatasetSnapshot
    candles: tuple[Candle, ...]

    def open_snapshot(self, snapshot_id: str) -> DatasetSnapshot:
        assert snapshot_id == self.snapshot.snapshot_id
        return self.snapshot

    def iter_candles(self, data_range: DataRange | None = None) -> Iterator[Candle]:
        for item in self.candles:
            if data_range is None or data_range.start <= item.open_time < data_range.end:
                yield item

    def verify_unchanged(self) -> DatasetSnapshot:
        return self.snapshot


@dataclass
class _RecordingStrategy:
    start_intents: tuple[OrderIntent, ...] = ()
    descriptor: StrategyDescriptor = field(
        default_factory=lambda: StrategyDescriptor("regime-recording", "1")
    )
    start_contexts: list[StrategyContext] = field(default_factory=list)
    warmup_contexts: list[StrategyContext] = field(default_factory=list)
    candle_contexts: list[StrategyContext] = field(default_factory=list)
    fill_contexts: list[StrategyContext] = field(default_factory=list)
    end_contexts: list[StrategyContext] = field(default_factory=list)

    def on_start(self, context: StrategyContext) -> tuple[OrderIntent, ...]:
        self.start_contexts.append(context)
        return self.start_intents

    def on_warmup_candle(self, context: StrategyContext, candle: Candle) -> None:
        assert context.current_candle == candle
        self.warmup_contexts.append(context)

    def on_candle(
        self,
        context: StrategyContext,
        candle: Candle,
    ) -> tuple[OrderIntent, ...]:
        assert context.current_candle == candle
        self.candle_contexts.append(context)
        return ()

    def on_fill(self, context: StrategyContext, fill: Fill) -> tuple[OrderIntent, ...]:
        del fill
        self.fill_contexts.append(context)
        return ()

    def on_end(self, context: StrategyContext) -> None:
        self.end_contexts.append(context)


def _candles() -> tuple[Candle, ...]:
    start = utc(2026, 1, 1)
    closes = ("100", "101", "110", "120")
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


def _snapshot(candles: tuple[Candle, ...]) -> DatasetSnapshot:
    start = candles[0].open_time
    return DatasetSnapshot(
        snapshot_id="a" * 64,
        dataset_key="derived:binance:spot:BTC/USDT:1h",
        dataset_version="b" * 64,
        checksum="c" * 64,
        data_range=DataRange(start, candles[-1].close_time),
        partitions=("partitions/year=2026/month=01/candles.parquet",),
        manifest_path="dataset-manifest.json",
        created_at=start.isoformat(),
    )


def _base_config(
    snapshot: DatasetSnapshot,
    descriptor: StrategyDescriptor,
) -> BacktestConfig:
    return BacktestConfig(
        snapshot_id=snapshot.snapshot_id,
        data_range=snapshot.data_range,
        strategy=descriptor,
        initial_capital=Decimal("1000"),
        execution=ExecutionAssumptions(
            fees=FeeModel(Decimal("0"), Decimal("0")),
            slippage=SlippageModel(fixed_bps=Decimal("0")),
        ),
        constraints=InstrumentConstraints(
            minimum_quantity=Decimal("0.001"),
            quantity_step=Decimal("0.001"),
            price_tick=Decimal("0.01"),
            minimum_notional=Decimal("1"),
        ),
        risk_limits=RiskLimits(),
        history_window=2,
        max_candles=100,
        max_orders=100,
        max_events=1000,
        engine_version="phase5-08-test",
        schema_version=2,
    )


def _policy() -> MarketRegimePolicy:
    return MarketRegimePolicy(
        fast_ema_period=2,
        slow_ema_period=3,
        atr_period=2,
        volatile_atr_ratio=Decimal("0.50"),
        trend_strength_threshold=Decimal("0.10"),
    )


def _tracked_config(base: BacktestConfig) -> RegimeAwareBacktestConfig:
    return RegimeAwareBacktestConfig(
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
        market_regime_policy=_policy(),
    )


def _tracked_evaluation_config(
    base: BacktestConfig,
    *,
    evaluation_start: int,
) -> RegimeAwareEvaluationBacktestConfig:
    return RegimeAwareEvaluationBacktestConfig(
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
            base.data_range.start + timedelta(hours=evaluation_start),
            base.data_range.end,
        ),
        strategy_lifecycle_version=2,
        market_regime_policy=_policy(),
    )


def test_regime_tracking_is_opt_in_and_identity_bearing() -> None:
    candles = _candles()
    snapshot = _snapshot(candles)
    strategy = _RecordingStrategy()
    base = _base_config(snapshot, strategy.descriptor)
    tracked = _tracked_config(base)

    assert market_regime_policy_for(base) is None
    assert market_regime_policy_for(tracked) == _policy()
    assert canonical_value(base) != canonical_value(tracked)

    legacy_result = DeterministicBacktestEngine(_Reader(snapshot, candles)).run(
        base,
        strategy,
    )

    assert legacy_result.market_regimes == ()
    assert strategy.start_contexts[0].market_regime is None
    assert all(context.market_regime is None for context in strategy.candle_contexts)


def test_regime_aware_backtest_exposes_aligned_closed_candle_points() -> None:
    candles = _candles()
    snapshot = _snapshot(candles)
    strategy = _RecordingStrategy()

    result = DeterministicBacktestEngine(_Reader(snapshot, candles)).run(
        _tracked_config(_base_config(snapshot, strategy.descriptor)),
        strategy,
    )

    assert len(result.market_regimes) == result.candles_processed == len(candles)
    assert tuple(context.market_regime for context in strategy.candle_contexts) == (
        result.market_regimes
    )
    assert [point.regime for point in result.market_regimes[:2]] == [
        MarketRegimeKind.WARMUP,
        MarketRegimeKind.WARMUP,
    ]
    assert result.market_regimes[-1].regime is MarketRegimeKind.TREND
    assert result.market_regimes[-1].trend_direction is TrendDirection.UP
    assert [point.event_time for point in result.market_regimes] == [
        item.close_time for item in candles
    ]
    assert strategy.end_contexts[-1].market_regime == result.market_regimes[-1]


def test_fill_callback_sees_only_previous_closed_candle_regime() -> None:
    candles = _candles()
    snapshot = _snapshot(candles)
    strategy = _RecordingStrategy(
        start_intents=(OrderIntent(OrderSide.BUY, OrderType.MARKET, Decimal("1")),)
    )
    base = _base_config(snapshot, strategy.descriptor)

    result = DeterministicBacktestEngine(_Reader(snapshot, candles)).run(
        _tracked_evaluation_config(base, evaluation_start=2),
        strategy,
    )

    assert len(strategy.warmup_contexts) == 2
    assert all(context.market_regime is not None for context in strategy.warmup_contexts)
    assert len(result.market_regimes) == 2
    assert strategy.fill_contexts[0].current_candle == candles[1]
    assert strategy.fill_contexts[0].market_regime == (strategy.warmup_contexts[-1].market_regime)
    assert strategy.fill_contexts[0].market_regime != result.market_regimes[0]


def test_regime_aware_config_rejects_hostile_policy_mutation() -> None:
    candles = _candles()
    snapshot = _snapshot(candles)
    strategy = _RecordingStrategy()
    config = _tracked_config(_base_config(snapshot, strategy.descriptor))
    object.__setattr__(config.market_regime_policy, "slow_ema_period", 1)

    with pytest.raises(ValueError, match="market regime policy"):
        validate_backtest_config(config)


def test_plain_evaluation_config_remains_legacy() -> None:
    candles = _candles()
    snapshot = _snapshot(candles)
    strategy = _RecordingStrategy()
    base = _base_config(snapshot, strategy.descriptor)
    legacy = EvaluationBacktestConfig(
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
            base.data_range.start + timedelta(hours=2),
            base.data_range.end,
        ),
        strategy_lifecycle_version=2,
    )

    assert market_regime_policy_for(legacy) is None


def test_strategy_context_rejects_hostile_regime_enum_mutation() -> None:
    candles = _candles()
    snapshot = _snapshot(candles)
    strategy = _RecordingStrategy()

    DeterministicBacktestEngine(_Reader(snapshot, candles)).run(
        _tracked_config(_base_config(snapshot, strategy.descriptor)),
        strategy,
    )
    context = strategy.candle_contexts[-1]
    point = context.market_regime
    assert point is not None
    object.__setattr__(point, "regime", "range")
    object.__setattr__(point, "trend_direction", TrendDirection.NONE)

    with pytest.raises(ValueError, match="market_regime is invalid"):
        replace(context, market_regime=point)
