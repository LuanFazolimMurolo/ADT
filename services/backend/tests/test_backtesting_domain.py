"""Backtesting domain invariants and immutable strategy contracts."""

from __future__ import annotations

from copy import copy
from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Decimal

import pytest

from app.backtesting.domain import (
    BacktestConfig,
    BacktestRunId,
    ExecutionAssumptions,
    FeeModel,
    FillLiquidity,
    InstrumentConstraints,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    PositionSizedExecutionAssumptions,
    PositionSizingKind,
    PositionSizingPolicy,
    RiskLimits,
    SimulatedOrder,
    SlippageModel,
    StopLossKind,
    StopLossPolicy,
    StopLossRiskLimits,
    StrategyDescriptor,
    validate_backtest_config,
)
from app.backtesting.errors import InvalidOrderIntentError
from app.backtesting.serialization import canonical_value
from app.market_data.domain import DataRange
from tests.market_data_helpers import utc


def test_strategy_descriptor_canonicalizes_parameter_order_and_rejects_float() -> None:
    descriptor = StrategyDescriptor(
        "example",
        "1",
        (("z", Decimal("2")), ("a", True)),
    )
    assert descriptor.parameters == (("a", True), ("z", Decimal("2")))

    with pytest.raises(ValueError, match="float"):
        StrategyDescriptor("example", "1", (("bad", 1.5),))


def test_order_intent_requires_exact_price_contract() -> None:
    market = OrderIntent(OrderSide.BUY, OrderType.MARKET, Decimal("1"))
    assert market.limit_price is None

    with pytest.raises(InvalidOrderIntentError):
        OrderIntent(
            OrderSide.BUY,
            OrderType.MARKET,
            Decimal("1"),
            limit_price=Decimal("100"),
        )
    with pytest.raises(InvalidOrderIntentError):
        OrderIntent(OrderSide.BUY, OrderType.LIMIT, Decimal("1"))
    with pytest.raises(InvalidOrderIntentError):
        OrderIntent(OrderSide.SELL, OrderType.STOP_MARKET, Decimal("1"))


def test_order_cannot_be_eligible_on_creation_candle_or_reopen_terminal_state() -> None:
    intent = OrderIntent(OrderSide.BUY, OrderType.MARKET, Decimal("1"))
    with pytest.raises(ValueError, match="eligible"):
        SimulatedOrder(
            "O000000000001",
            1,
            utc(2026, 1, 1),
            0,
            0,
            intent,
        )

    with pytest.raises(ValueError, match="terminal_at"):
        SimulatedOrder(
            "O000000000001",
            1,
            utc(2026, 1, 1),
            0,
            1,
            intent,
            status=OrderStatus.FILLED,
        )


def test_fee_risk_precision_and_portfolio_invariants() -> None:
    fees = FeeModel(Decimal("5"), Decimal("10"))
    assert fees.rate(FillLiquidity.MAKER) == Decimal("0.0005")
    assert fees.rate(FillLiquidity.TAKER) == Decimal("0.001")

    constraints = InstrumentConstraints(
        Decimal("0.001"),
        Decimal("0.001"),
        Decimal("0.01"),
        Decimal("10"),
        Decimal("1000"),
    )
    assert constraints.price_tick == Decimal("0.01")

    with pytest.raises(ValueError):
        RiskLimits(max_open_orders=2, max_total_orders=1)

    snapshot = PortfolioSnapshot(
        quote_cash=Decimal("100"),
        base_quantity=Decimal("0"),
        average_entry_price=Decimal("0"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage_cost=Decimal("0"),
        equity=Decimal("100"),
        peak_equity=Decimal("100"),
        drawdown=Decimal("0"),
    )
    with pytest.raises(FrozenInstanceError):
        snapshot.quote_cash = Decimal("0")  # type: ignore[misc]


def test_run_id_and_utc_are_strict() -> None:
    assert str(BacktestRunId("a" * 64)) == "a" * 64
    with pytest.raises(ValueError):
        BacktestRunId("ABC")

    intent = OrderIntent(OrderSide.BUY, OrderType.MARKET, Decimal("1"))
    with pytest.raises(Exception):
        SimulatedOrder(
            "O000000000001",
            1,
            utc(2026, 1, 1).replace(tzinfo=None),
            0,
            1,
            intent,
        )
    assert utc(2026, 1, 1) + timedelta(hours=1) > utc(2026, 1, 1)


def test_backtest_config_rejects_unimplemented_schema_version() -> None:
    with pytest.raises(ValueError, match="schema_version is not supported"):
        BacktestConfig(
            snapshot_id="a" * 64,
            data_range=DataRange(utc(2026, 1, 1), utc(2026, 1, 2)),
            strategy=StrategyDescriptor("no-op", "1"),
            initial_capital=Decimal("1000"),
            execution=ExecutionAssumptions(
                FeeModel(Decimal("0"), Decimal("0")),
                SlippageModel(fixed_bps=Decimal("0")),
            ),
            constraints=InstrumentConstraints(
                minimum_quantity=Decimal("0.001"),
                quantity_step=Decimal("0.001"),
                price_tick=Decimal("0.01"),
                minimum_notional=Decimal("1"),
            ),
            risk_limits=RiskLimits(),
            history_window=10,
            max_candles=100,
            max_orders=100,
            max_events=1000,
            engine_version="3b-1",
            schema_version=3,
        )


def test_position_sizing_policy_is_canonical_and_changes_execution_identity() -> None:
    explicit = ExecutionAssumptions(
        FeeModel(Decimal("0"), Decimal("0")),
        SlippageModel(fixed_bps=Decimal("0")),
    )
    sized = PositionSizedExecutionAssumptions(
        FeeModel(Decimal("0"), Decimal("0")),
        SlippageModel(fixed_bps=Decimal("0")),
        position_sizing=PositionSizingPolicy(
            PositionSizingKind.FIXED_NOTIONAL,
            Decimal("100"),
        ),
    )

    assert canonical_value(explicit) != canonical_value(sized)
    with pytest.raises(ValueError):
        PositionSizingPolicy(PositionSizingKind.EXPLICIT_QUANTITY, Decimal("1"))
    with pytest.raises(ValueError, match="quote reserve"):
        PositionSizingPolicy(
            PositionSizingKind.EXPLICIT_QUANTITY,
            minimum_quote_reserve=Decimal("1"),
        )
    with pytest.raises(ValueError, match="non-default"):
        PositionSizedExecutionAssumptions(
            FeeModel(Decimal("0"), Decimal("0")),
            SlippageModel(fixed_bps=Decimal("0")),
        )
    with pytest.raises(ValueError):
        PositionSizingPolicy(PositionSizingKind.EQUITY_PERCENT, Decimal("101"))


def test_backtest_config_rejects_low_level_redundant_explicit_sizing() -> None:
    execution = PositionSizedExecutionAssumptions(
        FeeModel(Decimal("0"), Decimal("0")),
        SlippageModel(fixed_bps=Decimal("0")),
        position_sizing=PositionSizingPolicy(
            PositionSizingKind.FIXED_NOTIONAL,
            Decimal("100"),
        ),
    )
    config = BacktestConfig(
        snapshot_id="a" * 64,
        data_range=DataRange(utc(2026, 1, 1), utc(2026, 1, 2)),
        strategy=StrategyDescriptor("no-op", "1"),
        initial_capital=Decimal("1000"),
        execution=execution,
        constraints=InstrumentConstraints(
            minimum_quantity=Decimal("0.001"),
            quantity_step=Decimal("0.001"),
            price_tick=Decimal("0.01"),
            minimum_notional=Decimal("1"),
        ),
        risk_limits=RiskLimits(),
        history_window=10,
        max_candles=100,
        max_orders=100,
        max_events=1000,
        engine_version="3b-1",
        schema_version=2,
    )
    tampered = copy(config.execution)
    object.__setattr__(tampered, "position_sizing", PositionSizingPolicy())
    object.__setattr__(config, "execution", tampered)

    with pytest.raises(ValueError, match="non-default"):
        validate_backtest_config(config)


def test_stop_loss_policy_is_canonical_and_changes_risk_identity() -> None:
    legacy = RiskLimits()
    protected = StopLossRiskLimits(
        stop_loss=StopLossPolicy(StopLossKind.FIXED_PERCENT, Decimal("5"))
    )

    assert canonical_value(legacy) != canonical_value(protected)
    with pytest.raises(ValueError, match="must not define value"):
        StopLossPolicy(StopLossKind.DISABLED, Decimal("5"))
    with pytest.raises(ValueError, match="below 100"):
        StopLossPolicy(StopLossKind.FIXED_PERCENT, Decimal("100"))
    with pytest.raises(ValueError, match="non-default"):
        StopLossRiskLimits()


def test_backtest_config_rejects_low_level_disabled_stop_loss() -> None:
    risk_limits = StopLossRiskLimits(
        stop_loss=StopLossPolicy(StopLossKind.FIXED_PERCENT, Decimal("5"))
    )
    config = BacktestConfig(
        snapshot_id="a" * 64,
        data_range=DataRange(utc(2026, 1, 1), utc(2026, 1, 2)),
        strategy=StrategyDescriptor("no-op", "1"),
        initial_capital=Decimal("1000"),
        execution=ExecutionAssumptions(
            FeeModel(Decimal("0"), Decimal("0")),
            SlippageModel(fixed_bps=Decimal("0")),
        ),
        constraints=InstrumentConstraints(
            minimum_quantity=Decimal("0.001"),
            quantity_step=Decimal("0.001"),
            price_tick=Decimal("0.01"),
            minimum_notional=Decimal("1"),
        ),
        risk_limits=risk_limits,
        history_window=10,
        max_candles=100,
        max_orders=100,
        max_events=1000,
        engine_version="3b-1",
        schema_version=2,
    )
    tampered = copy(config.risk_limits)
    object.__setattr__(tampered, "stop_loss", StopLossPolicy())
    object.__setattr__(config, "risk_limits", tampered)

    with pytest.raises(ValueError, match="risk limits are invalid"):
        validate_backtest_config(config)
