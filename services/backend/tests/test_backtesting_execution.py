"""Deterministic OHLC fill and order-lifecycle tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.backtesting.domain import (
    FeeModel,
    FillLiquidity,
    FillReason,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    SlippageModel,
)
from app.backtesting.errors import InvalidOrderIntentError
from app.backtesting.execution import (
    DeterministicExecutionModel,
    create_order,
    is_tick_aligned,
    order_priority_key,
    transition_order,
    truncate_quantity,
)
from tests.market_data_helpers import candle, utc


def _model() -> DeterministicExecutionModel:
    return DeterministicExecutionModel(
        fees=FeeModel(Decimal("5"), Decimal("10")),
        slippage=SlippageModel(fixed_bps=Decimal("10")),
    )


def _open(intent: OrderIntent, sequence: int = 1):
    created = create_order(
        intent,
        sequence=sequence,
        created_at=utc(2026, 1, 1),
        created_candle_index=0,
    )
    return transition_order(created, OrderStatus.OPEN, event_time=utc(2026, 1, 1))


def test_market_order_never_fills_on_creation_candle_and_uses_next_open() -> None:
    order = _open(OrderIntent(OrderSide.BUY, OrderType.MARKET, Decimal("2")))
    item = candle(utc(2026, 1, 1), open_price="100")

    assert _model().quote(order, item, candle_index=0) is None
    quote = _model().quote(order, item, candle_index=1)

    assert quote is not None
    assert quote.base_price == Decimal("100")
    assert quote.execution_price == Decimal("100.1")
    assert quote.reason is FillReason.MARKET_OPEN
    assert quote.liquidity is FillLiquidity.TAKER


def test_limit_rules_use_favorable_gap_and_never_worsen_limit() -> None:
    buy = _open(
        OrderIntent(
            OrderSide.BUY,
            OrderType.LIMIT,
            Decimal("1"),
            limit_price=Decimal("100"),
        )
    )
    gap = candle(utc(2026, 1, 1), open_price="95", high="101", low="90", close="98")
    touched = candle(utc(2026, 1, 1), open_price="105", high="110", low="99", close="103")

    gap_quote = _model().quote(buy, gap, candle_index=1)
    touch_quote = _model().quote(buy, touched, candle_index=1)
    assert gap_quote is not None and gap_quote.execution_price == Decimal("95")
    assert gap_quote.reason is FillReason.LIMIT_GAP
    assert touch_quote is not None and touch_quote.execution_price == Decimal("100")
    assert touch_quote.reason is FillReason.LIMIT_TOUCHED

    sell = _open(
        OrderIntent(
            OrderSide.SELL,
            OrderType.LIMIT,
            Decimal("1"),
            limit_price=Decimal("110"),
        )
    )
    sell_gap = candle(
        utc(2026, 1, 1),
        open_price="115",
        high="120",
        low="109",
        close="116",
    )
    sell_quote = _model().quote(sell, sell_gap, candle_index=1)
    assert sell_quote is not None and sell_quote.execution_price == Decimal("115")


def test_stop_rules_apply_adverse_slippage_and_detect_gap() -> None:
    buy = _open(
        OrderIntent(
            OrderSide.BUY,
            OrderType.STOP_MARKET,
            Decimal("1"),
            stop_price=Decimal("110"),
        )
    )
    gap = candle(
        utc(2026, 1, 1),
        open_price="115",
        high="120",
        low="114",
        close="116",
    )
    quote = _model().quote(buy, gap, candle_index=1)
    assert quote is not None
    assert quote.reason is FillReason.STOP_GAP
    assert quote.base_price == Decimal("115")
    assert quote.execution_price == Decimal("115.115")

    sell = _open(
        OrderIntent(
            OrderSide.SELL,
            OrderType.STOP_MARKET,
            Decimal("1"),
            stop_price=Decimal("90"),
        )
    )
    touched = candle(
        utc(2026, 1, 1),
        open_price="100",
        high="101",
        low="89",
        close="91",
    )
    sell_quote = _model().quote(sell, touched, candle_index=1)
    assert sell_quote is not None
    assert sell_quote.execution_price == Decimal("89.91")


def test_order_lifecycle_is_forward_only_and_priority_is_stable() -> None:
    first = _open(OrderIntent(OrderSide.BUY, OrderType.MARKET, Decimal("1")), sequence=1)
    second = _open(OrderIntent(OrderSide.BUY, OrderType.MARKET, Decimal("1")), sequence=2)
    assert sorted((second, first), key=order_priority_key) == [first, second]

    filled = transition_order(first, OrderStatus.FILLED, event_time=utc(2026, 1, 1))
    with pytest.raises(InvalidOrderIntentError):
        transition_order(filled, OrderStatus.OPEN, event_time=utc(2026, 1, 1))


def test_precision_helpers_never_increase_risk() -> None:
    assert truncate_quantity(Decimal("1.239"), Decimal("0.01")) == Decimal("1.23")
    assert is_tick_aligned(Decimal("100.25"), Decimal("0.05"))
    assert not is_tick_aligned(Decimal("100.23"), Decimal("0.05"))
    assert _model().fee(Decimal("100"), FillLiquidity.TAKER) == Decimal("0.1")
