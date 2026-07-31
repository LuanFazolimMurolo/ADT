"""Long-only Decimal portfolio accounting tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.backtesting.domain import Fill, FillLiquidity, FillReason, OrderSide
from app.backtesting.errors import InsufficientCashError, InsufficientPositionError
from app.backtesting.portfolio import apply_fill, initialize_portfolio, mark_to_market
from tests.market_data_helpers import utc


def _fill(
    *,
    side: OrderSide,
    quantity: str,
    price: str,
    fee: str = "0",
    slippage: str = "0",
    sequence: int = 1,
) -> Fill:
    qty = Decimal(quantity)
    execution = Decimal(price)
    return Fill(
        fill_id=f"F{sequence:012d}",
        order_id=f"O{sequence:012d}",
        reason=FillReason.MARKET_OPEN,
        liquidity=FillLiquidity.TAKER,
        side=side,
        quantity=qty,
        base_price=execution,
        execution_price=execution,
        notional=qty * execution,
        fee=Decimal(fee),
        slippage_cost=Decimal(slippage),
        event_time=utc(2026, 1, 1),
        candle_index=sequence,
    )


def test_initial_and_mark_to_market_are_exact() -> None:
    state = initialize_portfolio(Decimal("1000"))
    marked = mark_to_market(state, Decimal("100"))

    assert marked.quote_cash == Decimal("1000")
    assert marked.equity == Decimal("1000")
    assert marked.drawdown == 0
    assert marked.snapshot().cost_basis == 0


def test_multiple_buys_include_fees_in_weighted_average_cost() -> None:
    state = initialize_portfolio(Decimal("1000"))
    first = apply_fill(
        state,
        _fill(side=OrderSide.BUY, quantity="2", price="100", fee="2"),
    ).after
    second = apply_fill(
        first,
        _fill(side=OrderSide.BUY, quantity="1", price="120", fee="1", sequence=2),
    ).after

    assert second.quote_cash == Decimal("677")
    assert second.base_quantity == Decimal("3")
    assert second.cost_basis == Decimal("323")
    assert second.average_entry_price == Decimal("323") / Decimal("3")
    assert second.total_fees == Decimal("3")


def test_partial_sell_releases_proportional_cost_and_realizes_net_pnl() -> None:
    bought = apply_fill(
        initialize_portfolio(Decimal("1000")),
        _fill(side=OrderSide.BUY, quantity="4", price="100", fee="4"),
    ).after
    mutation = apply_fill(
        bought,
        _fill(side=OrderSide.SELL, quantity="1", price="130", fee="1", sequence=2),
    )

    assert mutation.after.base_quantity == Decimal("3")
    assert mutation.after.cost_basis == Decimal("303")
    assert mutation.realized_pnl_delta == Decimal("28")
    assert mutation.after.realized_pnl == Decimal("28")
    assert mutation.after.quote_cash == Decimal("725")


def test_full_sell_zeros_position_cost_and_average_price() -> None:
    bought = apply_fill(
        initialize_portfolio(Decimal("500")),
        _fill(side=OrderSide.BUY, quantity="2", price="100"),
    ).after
    sold = apply_fill(
        bought,
        _fill(side=OrderSide.SELL, quantity="2", price="110", sequence=2),
    ).after

    assert sold.base_quantity == 0
    assert sold.cost_basis == 0
    assert sold.average_entry_price == 0
    assert sold.realized_pnl == Decimal("20")
    assert sold.quote_cash == Decimal("520")


def test_cash_and_position_can_never_be_negative() -> None:
    with pytest.raises(InsufficientCashError):
        apply_fill(
            initialize_portfolio(Decimal("100")),
            _fill(side=OrderSide.BUY, quantity="2", price="60"),
        )

    with pytest.raises(InsufficientPositionError):
        apply_fill(
            initialize_portfolio(Decimal("100")),
            _fill(side=OrderSide.SELL, quantity="1", price="60"),
        )


def test_drawdown_uses_previous_peak_and_close_mark() -> None:
    bought = apply_fill(
        initialize_portfolio(Decimal("1000")),
        _fill(side=OrderSide.BUY, quantity="5", price="100"),
    ).after
    peak = mark_to_market(bought, Decimal("120"))
    drawdown = mark_to_market(peak, Decimal("80"))

    assert peak.equity == Decimal("1100")
    assert drawdown.equity == Decimal("900")
    assert drawdown.drawdown == Decimal("200")
    assert drawdown.drawdown_pct == Decimal("200") / Decimal("1100") * Decimal("100")
    assert drawdown.unrealized_pnl == Decimal("-100")
