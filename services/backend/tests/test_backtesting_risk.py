"""Deterministic precision, balance and exposure risk tests."""

from __future__ import annotations

from decimal import Decimal

from app.backtesting.domain import (
    FeeModel,
    Fill,
    FillLiquidity,
    FillReason,
    InstrumentConstraints,
    OrderIntent,
    OrderSide,
    OrderType,
    PortfolioSnapshot,
    RiskLimits,
    SlippageModel,
)
from app.backtesting.risk import (
    DeterministicRiskManager,
    RiskRejectionCode,
)
from tests.market_data_helpers import utc


def _portfolio(
    *,
    cash: str = "1000",
    base: str = "0",
    equity: str = "1000",
    peak: str = "1000",
    drawdown: str = "0",
) -> PortfolioSnapshot:
    base_quantity = Decimal(base)
    cost_basis = base_quantity * Decimal("100")
    return PortfolioSnapshot(
        quote_cash=Decimal(cash),
        base_quantity=base_quantity,
        average_entry_price=Decimal("100") if base_quantity else Decimal("0"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage_cost=Decimal("0"),
        equity=Decimal(equity),
        peak_equity=Decimal(peak),
        drawdown=Decimal(drawdown),
        cost_basis=cost_basis,
        drawdown_pct=(
            Decimal("0")
            if Decimal(peak) == 0
            else Decimal(drawdown) / Decimal(peak) * Decimal("100")
        ),
    )


def _manager(limits: RiskLimits | None = None) -> DeterministicRiskManager:
    return DeterministicRiskManager(
        constraints=InstrumentConstraints(
            minimum_quantity=Decimal("0.01"),
            quantity_step=Decimal("0.01"),
            price_tick=Decimal("0.05"),
            minimum_notional=Decimal("10"),
            maximum_notional=Decimal("500"),
        ),
        limits=limits or RiskLimits(max_open_orders=3, max_total_orders=10),
        fees=FeeModel(Decimal("5"), Decimal("10")),
        slippage=SlippageModel(fixed_bps=Decimal("10")),
    )


def test_order_is_truncated_without_increasing_risk() -> None:
    decision = _manager().evaluate_order(
        OrderIntent(OrderSide.BUY, OrderType.MARKET, Decimal("1.239")),
        _portfolio(),
        reference_price=Decimal("100"),
        open_order_count=0,
        total_order_count=0,
        risk_halt=False,
    )

    assert decision.accepted
    assert decision.normalized_intent is not None
    assert decision.normalized_intent.quantity == Decimal("1.23")
    assert decision.estimated_notional == Decimal("123.123")


def test_invalid_quantity_and_tick_are_rejected() -> None:
    quantity = _manager().evaluate_order(
        OrderIntent(OrderSide.BUY, OrderType.MARKET, Decimal("0.009")),
        _portfolio(),
        reference_price=Decimal("100"),
        open_order_count=0,
        total_order_count=0,
        risk_halt=False,
    )
    price = _manager().evaluate_order(
        OrderIntent(
            OrderSide.BUY,
            OrderType.LIMIT,
            Decimal("1"),
            limit_price=Decimal("100.03"),
        ),
        _portfolio(),
        reference_price=Decimal("100"),
        open_order_count=0,
        total_order_count=0,
        risk_halt=False,
    )

    assert quantity.rejection_code is RiskRejectionCode.INVALID_QUANTITY
    assert price.rejection_code is RiskRejectionCode.INVALID_PRICE


def test_notional_bounds_are_enforced() -> None:
    too_small = _manager().evaluate_order(
        OrderIntent(OrderSide.BUY, OrderType.MARKET, Decimal("0.05")),
        _portfolio(),
        reference_price=Decimal("100"),
        open_order_count=0,
        total_order_count=0,
        risk_halt=False,
    )
    too_large = _manager().evaluate_order(
        OrderIntent(OrderSide.BUY, OrderType.MARKET, Decimal("6")),
        _portfolio(),
        reference_price=Decimal("100"),
        open_order_count=0,
        total_order_count=0,
        risk_halt=False,
    )

    assert too_small.rejection_code is RiskRejectionCode.ORDER_NOTIONAL_TOO_SMALL
    assert too_large.rejection_code is RiskRejectionCode.ORDER_NOTIONAL_TOO_LARGE


def test_order_count_and_halt_rejections_are_stable() -> None:
    intent = OrderIntent(OrderSide.BUY, OrderType.MARKET, Decimal("1"))
    open_limit = _manager().evaluate_order(
        intent,
        _portfolio(),
        reference_price=Decimal("100"),
        open_order_count=3,
        total_order_count=3,
        risk_halt=False,
    )
    total_limit = _manager().evaluate_order(
        intent,
        _portfolio(),
        reference_price=Decimal("100"),
        open_order_count=0,
        total_order_count=10,
        risk_halt=False,
    )
    halted = _manager().evaluate_order(
        intent,
        _portfolio(),
        reference_price=Decimal("100"),
        open_order_count=0,
        total_order_count=0,
        risk_halt=True,
    )

    assert open_limit.rejection_code is RiskRejectionCode.MAXIMUM_OPEN_ORDERS
    assert total_limit.rejection_code is RiskRejectionCode.MAXIMUM_TOTAL_ORDERS
    assert halted.rejection_code is RiskRejectionCode.RISK_HALT_ACTIVE


def test_cash_position_reserve_and_position_limit_are_enforced() -> None:
    reserve_manager = _manager(RiskLimits(minimum_quote_reserve=Decimal("100")))
    cash = reserve_manager.evaluate_order(
        OrderIntent(OrderSide.BUY, OrderType.MARKET, Decimal("4.5")),
        _portfolio(cash="500", equity="500", peak="500"),
        reference_price=Decimal("100"),
        open_order_count=0,
        total_order_count=0,
        risk_halt=False,
    )
    position = _manager().evaluate_order(
        OrderIntent(OrderSide.SELL, OrderType.MARKET, Decimal("2")),
        _portfolio(base="1"),
        reference_price=Decimal("100"),
        open_order_count=0,
        total_order_count=0,
        risk_halt=False,
    )
    exposure = _manager(RiskLimits(max_position_notional=Decimal("150"))).evaluate_order(
        OrderIntent(OrderSide.BUY, OrderType.MARKET, Decimal("1")),
        _portfolio(base="1"),
        reference_price=Decimal("100"),
        open_order_count=0,
        total_order_count=0,
        risk_halt=False,
    )

    assert cash.rejection_code is RiskRejectionCode.INSUFFICIENT_CASH
    assert position.rejection_code is RiskRejectionCode.INSUFFICIENT_POSITION
    assert exposure.rejection_code is RiskRejectionCode.POSITION_LIMIT_EXCEEDED


def test_actual_gap_fill_is_rechecked_against_cash() -> None:
    manager = _manager()
    fill = Fill(
        fill_id="F000000000001",
        order_id="O000000000001",
        reason=FillReason.MARKET_OPEN,
        liquidity=FillLiquidity.TAKER,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        base_price=Decimal("500"),
        execution_price=Decimal("500"),
        notional=Decimal("500"),
        fee=Decimal("1"),
        slippage_cost=Decimal("0"),
        event_time=utc(2026, 1, 1),
        candle_index=1,
    )

    assert (
        manager.validate_fill(fill, _portfolio(cash="500")) is RiskRejectionCode.INSUFFICIENT_CASH
    )


def test_drawdown_halt_uses_configured_percentage() -> None:
    manager = _manager(RiskLimits(max_drawdown_pct=Decimal("10")))
    assert manager.drawdown_halt_required(_portfolio(equity="800", peak="1000", drawdown="200"))
    assert not manager.drawdown_halt_required(_portfolio(equity="950", peak="1000", drawdown="50"))


def test_actual_gap_fill_rechecks_notional_limits() -> None:
    manager = _manager(RiskLimits(max_order_notional=Decimal("500")))
    fill = Fill(
        fill_id="F000000000001",
        order_id="O000000000001",
        reason=FillReason.STOP_GAP,
        liquidity=FillLiquidity.TAKER,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        base_price=Decimal("600"),
        execution_price=Decimal("600"),
        notional=Decimal("600"),
        fee=Decimal("0.6"),
        slippage_cost=Decimal("0"),
        event_time=utc(2026, 1, 1),
        candle_index=1,
    )

    assert (
        manager.validate_fill(fill, _portfolio(cash="1000"))
        is RiskRejectionCode.ORDER_NOTIONAL_TOO_LARGE
    )
