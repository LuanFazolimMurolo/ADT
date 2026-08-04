"""Deterministic long-only position sizing contracts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.backtesting.domain import (
    FeeModel,
    FillLiquidity,
    InstrumentConstraints,
    OrderIntent,
    OrderSide,
    OrderType,
    PortfolioSnapshot,
    PositionSizingKind,
    PositionSizingPolicy,
    SlippageKind,
    SlippageModel,
)
from app.backtesting.execution import truncate_quantity

_BPS = Decimal("10000")
_PERCENT = Decimal("100")


@dataclass(frozen=True, slots=True)
class PositionSizingDecision:
    """One deterministic quantity projection before final risk validation."""

    quantity: Decimal
    target_notional: Decimal
    estimated_price: Decimal
    estimated_fee: Decimal
    cash_required: Decimal

    def __post_init__(self) -> None:
        for name, value in (
            ("quantity", self.quantity),
            ("target_notional", self.target_notional),
            ("estimated_price", self.estimated_price),
            ("estimated_fee", self.estimated_fee),
            ("cash_required", self.cash_required),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.quantity > 0 and self.estimated_price <= 0:
            raise ValueError("positive quantity requires a positive estimated price")
        if self.cash_required != self.quantity * self.estimated_price + self.estimated_fee:
            raise ValueError("cash_required must match quantity, price and fee")


class DeterministicPositionSizer:
    """Convert strategy intents into bounded quantities without increasing exposure."""

    def __init__(
        self,
        *,
        policy: PositionSizingPolicy,
        constraints: InstrumentConstraints,
        fees: FeeModel,
        slippage: SlippageModel,
        minimum_quote_reserve: Decimal = Decimal("0"),
    ) -> None:
        if not isinstance(policy, PositionSizingPolicy):
            raise ValueError("position sizing policy is invalid")
        if (
            not isinstance(minimum_quote_reserve, Decimal)
            or not minimum_quote_reserve.is_finite()
            or minimum_quote_reserve < 0
        ):
            raise ValueError("minimum_quote_reserve must be finite and nonnegative")
        self._policy = policy
        self._constraints = constraints
        self._fees = fees
        self._slippage = slippage
        self._minimum_quote_reserve = max(
            minimum_quote_reserve,
            policy.minimum_quote_reserve,
        )

    def size(
        self,
        intent: OrderIntent,
        portfolio: PortfolioSnapshot,
        *,
        reference_price: Decimal,
    ) -> PositionSizingDecision:
        if not isinstance(intent, OrderIntent):
            raise ValueError("order intent is invalid")
        if (
            not isinstance(reference_price, Decimal)
            or not reference_price.is_finite()
            or reference_price <= 0
        ):
            raise ValueError("reference_price must be finite and positive")
        liquidity = (
            FillLiquidity.MAKER if intent.order_type is OrderType.LIMIT else FillLiquidity.TAKER
        )
        price = self._estimated_price(intent, reference_price)
        if (
            intent.side is OrderSide.SELL
            or self._policy.kind is PositionSizingKind.EXPLICIT_QUANTITY
        ):
            quantity = truncate_quantity(
                intent.quantity,
                self._constraints.quantity_step,
            )
            return self._decision(
                quantity,
                intent.quantity * price,
                price,
                liquidity,
            )
        value = self._policy.value
        if value is None:
            raise ValueError("position sizing value is required")
        target = (
            value
            if self._policy.kind is PositionSizingKind.FIXED_NOTIONAL
            else portfolio.equity * value / _PERCENT
        )
        available_cash = max(
            Decimal("0"),
            portfolio.quote_cash - self._minimum_quote_reserve,
        )
        fee_multiplier = Decimal("1") + self._fees.rate(liquidity)
        bounded_notional = min(target, available_cash / fee_multiplier)
        quantity = truncate_quantity(
            bounded_notional / price,
            self._constraints.quantity_step,
        )
        return self._decision(quantity, target, price, liquidity)

    def _decision(
        self,
        quantity: Decimal,
        target: Decimal,
        price: Decimal,
        liquidity: FillLiquidity,
    ) -> PositionSizingDecision:
        fee = quantity * price * self._fees.rate(liquidity)
        return PositionSizingDecision(
            quantity=quantity,
            target_notional=target,
            estimated_price=price,
            estimated_fee=fee,
            cash_required=quantity * price + fee,
        )

    def _estimated_price(
        self,
        intent: OrderIntent,
        reference_price: Decimal,
    ) -> Decimal:
        if intent.order_type is OrderType.LIMIT:
            assert intent.limit_price is not None
            return intent.limit_price
        base = intent.stop_price if intent.stop_price is not None else reference_price
        if self._slippage.kind is not SlippageKind.FIXED_BPS:
            raise ValueError("unsupported slippage model")
        multiplier = self._slippage.fixed_bps / _BPS
        return base * (
            Decimal("1") + multiplier if intent.side is OrderSide.BUY else Decimal("1") - multiplier
        )
