from decimal import Decimal

import pytest

from app.backtesting.domain import (
    InstrumentConstraints,
    PortfolioSnapshot,
    StopLossKind,
    StopLossPolicy,
)
from app.backtesting.errors import InvalidOrderIntentError
from app.backtesting.stop_loss import DeterministicStopLossManager


def portfolio(
    *,
    base: str = "2.50",
    average_entry: str = "101.23",
) -> PortfolioSnapshot:
    quantity = Decimal(base)
    average = Decimal(average_entry) if quantity else Decimal("0")
    return PortfolioSnapshot(
        quote_cash=Decimal("500"),
        base_quantity=quantity,
        average_entry_price=average,
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        total_fees=Decimal("0"),
        total_slippage_cost=Decimal("0"),
        equity=Decimal("750"),
        peak_equity=Decimal("750"),
        drawdown=Decimal("0"),
        cost_basis=quantity * average,
        drawdown_pct=Decimal("0"),
    )


def manager(
    policy: StopLossPolicy,
    *,
    price_tick: str = "0.10",
) -> DeterministicStopLossManager:
    return DeterministicStopLossManager(
        policy=policy,
        constraints=InstrumentConstraints(
            minimum_quantity=Decimal("0.01"),
            quantity_step=Decimal("0.01"),
            price_tick=Decimal(price_tick),
            minimum_notional=Decimal("10"),
        ),
    )


def test_disabled_policy_is_inactive() -> None:
    decision = manager(StopLossPolicy()).evaluate(portfolio())

    assert not decision.active
    assert decision.quantity == Decimal("0")
    assert decision.stop_price is None


def test_flat_portfolio_is_inactive() -> None:
    decision = manager(StopLossPolicy(StopLossKind.FIXED_PERCENT, Decimal("5"))).evaluate(
        portfolio(base="0")
    )

    assert not decision.active


def test_fixed_percent_protects_full_position_and_truncates_to_tick() -> None:
    decision = manager(StopLossPolicy(StopLossKind.FIXED_PERCENT, Decimal("5"))).evaluate(
        portfolio()
    )

    assert decision.active
    assert decision.quantity == Decimal("2.50")
    assert decision.stop_price == Decimal("96.10")
    assert decision.stop_price % Decimal("0.10") == 0


def test_stop_price_uses_current_weighted_average_entry() -> None:
    decision = manager(
        StopLossPolicy(StopLossKind.FIXED_PERCENT, Decimal("10")),
        price_tick="0.01",
    ).evaluate(portfolio(average_entry="120.567"))

    assert decision.stop_price == Decimal("108.51")


def test_too_coarse_tick_fails_instead_of_leaving_position_unprotected() -> None:
    with pytest.raises(InvalidOrderIntentError, match="menor tick"):
        manager(
            StopLossPolicy(StopLossKind.FIXED_PERCENT, Decimal("50")),
            price_tick="1",
        ).evaluate(portfolio(average_entry="0.50"))
