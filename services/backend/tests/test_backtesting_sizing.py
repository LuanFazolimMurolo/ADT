from decimal import Decimal

import pytest

from app.backtesting.domain import (
    FeeModel,
    InstrumentConstraints,
    OrderIntent,
    OrderSide,
    OrderType,
    PortfolioSnapshot,
    PositionSizingKind,
    PositionSizingPolicy,
    SlippageModel,
)
from app.backtesting.sizing import DeterministicPositionSizer


def portfolio(
    cash: str = "1000",
    equity: str = "1000",
    base: str = "0",
) -> PortfolioSnapshot:
    quantity = Decimal(base)
    return PortfolioSnapshot(
        quote_cash=Decimal(cash),
        base_quantity=quantity,
        average_entry_price=Decimal("100") if quantity else Decimal("0"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage_cost=Decimal("0"),
        equity=Decimal(equity),
        peak_equity=Decimal(equity),
        drawdown=Decimal("0"),
        cost_basis=quantity * Decimal("100"),
        drawdown_pct=Decimal("0"),
    )


def sizer(
    policy: PositionSizingPolicy,
    *,
    reserve: Decimal = Decimal("0"),
) -> DeterministicPositionSizer:
    return DeterministicPositionSizer(
        policy=policy,
        constraints=InstrumentConstraints(
            Decimal("0.01"),
            Decimal("0.01"),
            Decimal("0.01"),
            Decimal("10"),
        ),
        fees=FeeModel(Decimal("5"), Decimal("10")),
        slippage=SlippageModel(fixed_bps=Decimal("10")),
        minimum_quote_reserve=reserve,
    )


def market(side: OrderSide, quantity: str = "1.239") -> OrderIntent:
    return OrderIntent(
        side=side,
        order_type=OrderType.MARKET,
        quantity=Decimal(quantity),
    )


def test_explicit_quantity_truncates_down() -> None:
    decision = sizer(PositionSizingPolicy()).size(
        market(OrderSide.BUY),
        portfolio(),
        reference_price=Decimal("100"),
    )
    assert decision.quantity == Decimal("1.23")
    assert decision.estimated_price == Decimal("100.100")


def test_fixed_notional_ignores_strategy_buy_quantity_and_respects_reserve() -> None:
    decision = sizer(
        PositionSizingPolicy(PositionSizingKind.FIXED_NOTIONAL, Decimal("1000")),
        reserve=Decimal("100"),
    ).size(
        market(OrderSide.BUY, "999"),
        portfolio(),
        reference_price=Decimal("100"),
    )
    assert decision.quantity == Decimal("8.98")
    assert decision.cash_required <= Decimal("900")


def test_equity_percent_uses_equity() -> None:
    decision = sizer(PositionSizingPolicy(PositionSizingKind.EQUITY_PERCENT, Decimal("25"))).size(
        market(OrderSide.BUY),
        portfolio("900", "1200"),
        reference_price=Decimal("50"),
    )
    assert decision.target_notional == Decimal("300")
    assert decision.quantity == Decimal("5.99")


def test_limit_uses_limit_price_maker_fee_and_no_slippage() -> None:
    decision = sizer(PositionSizingPolicy(PositionSizingKind.FIXED_NOTIONAL, Decimal("300"))).size(
        OrderIntent(
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("999"),
            limit_price=Decimal("50"),
        ),
        portfolio(),
        reference_price=Decimal("60"),
    )
    assert decision.estimated_price == Decimal("50")
    assert decision.quantity == Decimal("6.00")
    assert decision.estimated_fee == Decimal("0.150000")


def test_sell_keeps_explicit_semantics_under_policy_sizing() -> None:
    decision = sizer(PositionSizingPolicy(PositionSizingKind.EQUITY_PERCENT, Decimal("25"))).size(
        market(OrderSide.SELL),
        portfolio(base="2"),
        reference_price=Decimal("100"),
    )
    assert decision.quantity == Decimal("1.23")
    assert decision.estimated_price == Decimal("99.900")


def test_cash_exhaustion_produces_zero_without_rounding_up() -> None:
    decision = sizer(
        PositionSizingPolicy(PositionSizingKind.FIXED_NOTIONAL, Decimal("100")),
        reserve=Decimal("1000"),
    ).size(
        market(OrderSide.BUY),
        portfolio(),
        reference_price=Decimal("100"),
    )
    assert decision.quantity == Decimal("0.00")
    assert decision.cash_required == Decimal("0.00000")


def test_policy_validation() -> None:
    with pytest.raises(ValueError):
        PositionSizingPolicy(PositionSizingKind.EXPLICIT_QUANTITY, Decimal("1"))
    with pytest.raises(ValueError):
        PositionSizingPolicy(PositionSizingKind.FIXED_NOTIONAL, Decimal("0"))
    with pytest.raises(ValueError):
        PositionSizingPolicy(PositionSizingKind.EQUITY_PERCENT, Decimal("100.01"))
