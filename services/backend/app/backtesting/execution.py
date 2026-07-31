"""Deterministic order lifecycle and OHLC fill-price rules."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_DOWN, Decimal

from app.backtesting.domain import (
    FeeModel,
    FillLiquidity,
    FillReason,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    SimulatedOrder,
    SlippageKind,
    SlippageModel,
)
from app.backtesting.errors import InvalidOrderIntentError
from app.market_data.domain import Candle, require_utc

_BPS_DENOMINATOR = Decimal("10000")
_ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset({OrderStatus.OPEN, OrderStatus.CANCELLED, OrderStatus.REJECTED}),
    OrderStatus.OPEN: frozenset({OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED}),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ExecutionQuote:
    """One price candidate before quantity, cash and risk validation."""

    base_price: Decimal
    execution_price: Decimal
    reason: FillReason
    liquidity: FillLiquidity
    slippage_cost_per_unit: Decimal


class DeterministicExecutionModel:
    """Evaluate all-or-none MARKET, LIMIT and STOP_MARKET orders from OHLC only."""

    def __init__(self, *, fees: FeeModel, slippage: SlippageModel) -> None:
        self._fees = fees
        self._slippage = slippage

    def quote(
        self,
        order: SimulatedOrder,
        candle: Candle,
        *,
        candle_index: int,
    ) -> ExecutionQuote | None:
        """Return a deterministic fill quote or ``None`` when no trigger exists.

        Same-candle fills are impossible because ``eligible_candle_index`` must
        be reached and the order must already be OPEN.
        """
        if order.status is not OrderStatus.OPEN or candle_index < order.eligible_candle_index:
            return None
        intent = order.intent
        if intent.order_type is OrderType.MARKET:
            return self._adverse_quote(
                candle.open,
                intent.side,
                FillReason.MARKET_OPEN,
                FillLiquidity.TAKER,
            )
        if intent.order_type is OrderType.LIMIT:
            assert intent.limit_price is not None
            return self._limit_quote(intent, candle)
        if intent.order_type is OrderType.STOP_MARKET:
            assert intent.stop_price is not None
            return self._stop_quote(intent, candle)
        raise InvalidOrderIntentError("Tipo de ordem não suportado.")

    def force_close_quote(self, price: Decimal, side: OrderSide) -> ExecutionQuote:
        """Quote an explicit end-of-run liquidation at the last known close."""
        return self._adverse_quote(
            price,
            side,
            FillReason.FORCE_CLOSE,
            FillLiquidity.TAKER,
        )

    def fee(self, notional: Decimal, liquidity: FillLiquidity) -> Decimal:
        if not isinstance(notional, Decimal) or not notional.is_finite() or notional <= 0:
            raise InvalidOrderIntentError("O notional da execução é inválido.")
        return notional * self._fees.rate(liquidity)

    def _limit_quote(self, intent: OrderIntent, candle: Candle) -> ExecutionQuote | None:
        limit = intent.limit_price
        assert limit is not None
        if intent.side is OrderSide.BUY:
            if candle.low > limit:
                return None
            if candle.open < limit:
                return ExecutionQuote(
                    candle.open,
                    candle.open,
                    FillReason.LIMIT_GAP,
                    FillLiquidity.MAKER,
                    Decimal("0"),
                )
            return ExecutionQuote(
                limit,
                limit,
                FillReason.LIMIT_TOUCHED,
                FillLiquidity.MAKER,
                Decimal("0"),
            )
        if candle.high < limit:
            return None
        if candle.open > limit:
            return ExecutionQuote(
                candle.open,
                candle.open,
                FillReason.LIMIT_GAP,
                FillLiquidity.MAKER,
                Decimal("0"),
            )
        return ExecutionQuote(
            limit,
            limit,
            FillReason.LIMIT_TOUCHED,
            FillLiquidity.MAKER,
            Decimal("0"),
        )

    def _stop_quote(self, intent: OrderIntent, candle: Candle) -> ExecutionQuote | None:
        stop = intent.stop_price
        assert stop is not None
        if intent.side is OrderSide.BUY:
            if candle.high < stop:
                return None
            base = candle.open if candle.open > stop else stop
            reason = FillReason.STOP_GAP if candle.open > stop else FillReason.STOP_TRIGGERED
            return self._adverse_quote(base, intent.side, reason, FillLiquidity.TAKER)
        if candle.low > stop:
            return None
        base = candle.open if candle.open < stop else stop
        reason = FillReason.STOP_GAP if candle.open < stop else FillReason.STOP_TRIGGERED
        return self._adverse_quote(base, intent.side, reason, FillLiquidity.TAKER)

    def _adverse_quote(
        self,
        base_price: Decimal,
        side: OrderSide,
        reason: FillReason,
        liquidity: FillLiquidity,
    ) -> ExecutionQuote:
        if self._slippage.kind is not SlippageKind.FIXED_BPS:
            raise InvalidOrderIntentError("Modelo de slippage não suportado.")
        multiplier = self._slippage.fixed_bps / _BPS_DENOMINATOR
        execution = (
            base_price * (Decimal("1") + multiplier)
            if side is OrderSide.BUY
            else base_price * (Decimal("1") - multiplier)
        )
        if execution <= 0:
            raise InvalidOrderIntentError("O slippage produziu preço inválido.")
        return ExecutionQuote(
            base_price=base_price,
            execution_price=execution,
            reason=reason,
            liquidity=liquidity,
            slippage_cost_per_unit=abs(execution - base_price),
        )


def create_order(
    intent: OrderIntent,
    *,
    sequence: int,
    created_at: datetime,
    created_candle_index: int,
) -> SimulatedOrder:
    """Create one deterministic order that is eligible only on the next candle."""
    if sequence < 1:
        raise InvalidOrderIntentError("A sequência da ordem é inválida.")
    return SimulatedOrder(
        order_id=f"O{sequence:012d}",
        created_sequence=sequence,
        created_at=require_utc(created_at, field_name="created_at"),
        created_candle_index=created_candle_index,
        eligible_candle_index=created_candle_index + 1,
        intent=intent,
    )


def transition_order(
    order: SimulatedOrder,
    status: OrderStatus,
    *,
    event_time: datetime,
    rejection_code: str | None = None,
) -> SimulatedOrder:
    """Apply one valid forward-only lifecycle transition."""
    if status not in _ALLOWED_TRANSITIONS[order.status]:
        raise InvalidOrderIntentError("A transição de estado da ordem é inválida.")
    normalized_time = require_utc(event_time, field_name="event_time")
    if status is OrderStatus.OPEN:
        return replace(order, status=status, opened_at=normalized_time)
    return replace(
        order,
        status=status,
        terminal_at=normalized_time,
        rejection_code=rejection_code,
    )


def truncate_quantity(quantity: Decimal, step: Decimal) -> Decimal:
    """Truncate toward zero to avoid increasing exposure silently."""
    if (
        not isinstance(quantity, Decimal)
        or not isinstance(step, Decimal)
        or not quantity.is_finite()
        or not step.is_finite()
        or quantity < 0
        or step <= 0
    ):
        raise InvalidOrderIntentError("Quantidade ou step size inválido.")
    units = (quantity / step).to_integral_value(rounding=ROUND_DOWN)
    return units * step


def is_tick_aligned(price: Decimal, tick: Decimal) -> bool:
    if (
        not isinstance(price, Decimal)
        or not isinstance(tick, Decimal)
        or not price.is_finite()
        or not tick.is_finite()
        or price <= 0
        or tick <= 0
    ):
        return False
    return price % tick == 0


def order_priority_key(order: SimulatedOrder) -> tuple[int, int, str]:
    """Canonical priority for multiple orders eligible in one candle."""
    return order.eligible_candle_index, order.created_sequence, order.order_id
