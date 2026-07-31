"""Deterministic pre-order and pre-fill risk validation for Spot backtests."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from app.backtesting.domain import (
    FeeModel,
    Fill,
    FillLiquidity,
    InstrumentConstraints,
    OrderIntent,
    OrderSide,
    OrderType,
    PortfolioSnapshot,
    RiskLimits,
    SlippageKind,
    SlippageModel,
)
from app.backtesting.execution import is_tick_aligned, truncate_quantity

_BPS_DENOMINATOR = Decimal("10000")


class RiskRejectionCode(StrEnum):
    INSUFFICIENT_CASH = "insufficient_cash"
    INSUFFICIENT_POSITION = "insufficient_position"
    ORDER_NOTIONAL_TOO_SMALL = "order_notional_too_small"
    ORDER_NOTIONAL_TOO_LARGE = "order_notional_too_large"
    POSITION_LIMIT_EXCEEDED = "position_limit_exceeded"
    MAXIMUM_OPEN_ORDERS = "maximum_open_orders"
    MAXIMUM_TOTAL_ORDERS = "maximum_total_orders"
    INVALID_QUANTITY = "invalid_quantity"
    INVALID_PRICE = "invalid_price"
    RISK_HALT_ACTIVE = "risk_halt_active"


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """One predictable decision returned before an order is opened."""

    accepted: bool
    normalized_intent: OrderIntent | None
    estimated_notional: Decimal
    rejection_code: RiskRejectionCode | None = None

    def __post_init__(self) -> None:
        if self.estimated_notional < 0 or not self.estimated_notional.is_finite():
            raise ValueError("estimated_notional must be finite and nonnegative")
        if self.accepted:
            if self.normalized_intent is None or self.rejection_code is not None:
                raise ValueError("accepted risk decision is inconsistent")
        elif self.normalized_intent is not None or self.rejection_code is None:
            raise ValueError("rejected risk decision is inconsistent")


class DeterministicRiskManager:
    """Validate precision, balances and configured bounded exposure."""

    def __init__(
        self,
        *,
        constraints: InstrumentConstraints,
        limits: RiskLimits,
        fees: FeeModel,
        slippage: SlippageModel,
    ) -> None:
        self._constraints = constraints
        self._limits = limits
        self._fees = fees
        self._slippage = slippage

    def evaluate_order(
        self,
        intent: OrderIntent,
        portfolio: PortfolioSnapshot,
        *,
        reference_price: Decimal,
        open_order_count: int,
        total_order_count: int,
        risk_halt: bool,
    ) -> RiskDecision:
        """Return an accepted normalized intent or one stable rejection code."""
        if risk_halt:
            return _reject(RiskRejectionCode.RISK_HALT_ACTIVE)
        if open_order_count >= self._limits.max_open_orders:
            return _reject(RiskRejectionCode.MAXIMUM_OPEN_ORDERS)
        if total_order_count >= self._limits.max_total_orders:
            return _reject(RiskRejectionCode.MAXIMUM_TOTAL_ORDERS)
        quantity = truncate_quantity(intent.quantity, self._constraints.quantity_step)
        if quantity < self._constraints.minimum_quantity or quantity <= 0:
            return _reject(RiskRejectionCode.INVALID_QUANTITY)
        if not _valid_reference_price(reference_price):
            return _reject(RiskRejectionCode.INVALID_PRICE)
        trigger_price = intent.limit_price or intent.stop_price
        if trigger_price is not None and not is_tick_aligned(
            trigger_price,
            self._constraints.price_tick,
        ):
            return _reject(RiskRejectionCode.INVALID_PRICE)
        normalized = replace(intent, quantity=quantity)
        estimated_price = self._estimated_execution_price(normalized, reference_price)
        notional = quantity * estimated_price
        if notional < self._constraints.minimum_notional:
            return _reject(RiskRejectionCode.ORDER_NOTIONAL_TOO_SMALL, notional)
        maximum = self._constraints.maximum_notional
        if maximum is not None and notional > maximum:
            return _reject(RiskRejectionCode.ORDER_NOTIONAL_TOO_LARGE, notional)
        if self._limits.max_order_notional is not None:
            if notional > self._limits.max_order_notional:
                return _reject(RiskRejectionCode.ORDER_NOTIONAL_TOO_LARGE, notional)
        if normalized.side is OrderSide.BUY:
            liquidity = (
                FillLiquidity.MAKER
                if normalized.order_type is OrderType.LIMIT
                else FillLiquidity.TAKER
            )
            fee = notional * self._fees.rate(liquidity)
            reserve = (
                Decimal("0") if self._limits.allow_all_in else self._limits.minimum_quote_reserve
            )
            if portfolio.quote_cash - notional - fee < reserve:
                return _reject(RiskRejectionCode.INSUFFICIENT_CASH, notional)
            projected_value = (portfolio.base_quantity + quantity) * max(
                reference_price,
                estimated_price,
            )
            if self._limits.max_position_notional is not None:
                if projected_value > self._limits.max_position_notional:
                    return _reject(RiskRejectionCode.POSITION_LIMIT_EXCEEDED, notional)
        elif quantity > portfolio.base_quantity:
            return _reject(RiskRejectionCode.INSUFFICIENT_POSITION, notional)
        return RiskDecision(True, normalized, notional)

    def validate_fill(
        self,
        fill: Fill,
        portfolio: PortfolioSnapshot,
    ) -> RiskRejectionCode | None:
        """Recheck all price-sensitive limits at the actual execution price."""
        if fill.notional < self._constraints.minimum_notional:
            return RiskRejectionCode.ORDER_NOTIONAL_TOO_SMALL
        maximum = self._constraints.maximum_notional
        if maximum is not None and fill.notional > maximum:
            return RiskRejectionCode.ORDER_NOTIONAL_TOO_LARGE
        if (
            self._limits.max_order_notional is not None
            and fill.notional > self._limits.max_order_notional
        ):
            return RiskRejectionCode.ORDER_NOTIONAL_TOO_LARGE
        if fill.side is OrderSide.BUY:
            reserve = (
                Decimal("0") if self._limits.allow_all_in else self._limits.minimum_quote_reserve
            )
            if portfolio.quote_cash - fill.notional - fill.fee < reserve:
                return RiskRejectionCode.INSUFFICIENT_CASH
            projected = (portfolio.base_quantity + fill.quantity) * fill.execution_price
            if self._limits.max_position_notional is not None:
                if projected > self._limits.max_position_notional:
                    return RiskRejectionCode.POSITION_LIMIT_EXCEEDED
        elif fill.quantity > portfolio.base_quantity:
            return RiskRejectionCode.INSUFFICIENT_POSITION
        return None

    def drawdown_halt_required(self, portfolio: PortfolioSnapshot) -> bool:
        threshold = self._limits.max_drawdown_pct
        return bool(
            self._limits.stop_on_max_drawdown
            and threshold is not None
            and portfolio.drawdown_pct >= threshold
        )

    def _estimated_execution_price(
        self,
        intent: OrderIntent,
        reference_price: Decimal,
    ) -> Decimal:
        if intent.order_type is OrderType.LIMIT:
            assert intent.limit_price is not None
            return intent.limit_price
        base = intent.stop_price if intent.stop_price is not None else reference_price
        if self._slippage.kind is not SlippageKind.FIXED_BPS:
            return base
        fraction = self._slippage.fixed_bps / _BPS_DENOMINATOR
        if intent.side is OrderSide.BUY:
            return base * (Decimal("1") + fraction)
        return base * (Decimal("1") - fraction)


def _reject(
    code: RiskRejectionCode,
    notional: Decimal = Decimal("0"),
) -> RiskDecision:
    return RiskDecision(False, None, notional, code)


def _valid_reference_price(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0
