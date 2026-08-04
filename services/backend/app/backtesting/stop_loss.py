"""Deterministic engine-managed protective stop-loss projection."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from app.backtesting.domain import (
    InstrumentConstraints,
    PortfolioSnapshot,
    StopLossKind,
    StopLossPolicy,
)
from app.backtesting.errors import InvalidOrderIntentError

_PERCENT_DENOMINATOR = Decimal("100")


@dataclass(frozen=True, slots=True)
class StopLossDecision:
    """One deterministic protective-stop projection for the current position."""

    quantity: Decimal
    stop_price: Decimal | None

    def __post_init__(self) -> None:
        if not isinstance(self.quantity, Decimal) or not self.quantity.is_finite():
            raise ValueError("stop loss quantity must be one finite Decimal")
        if self.quantity < 0:
            raise ValueError("stop loss quantity must be nonnegative")
        if self.quantity == 0:
            if self.stop_price is not None:
                raise ValueError("inactive stop loss must not define stop_price")
            return
        if (
            not isinstance(self.stop_price, Decimal)
            or not self.stop_price.is_finite()
            or self.stop_price <= 0
        ):
            raise ValueError("active stop loss requires one positive stop_price")

    @property
    def active(self) -> bool:
        return self.quantity > 0


class DeterministicStopLossManager:
    """Project one full-position fixed-percent protective stop without floats."""

    def __init__(
        self,
        *,
        policy: StopLossPolicy,
        constraints: InstrumentConstraints,
    ) -> None:
        if not isinstance(policy, StopLossPolicy):
            raise ValueError("stop loss policy is invalid")
        if not isinstance(constraints, InstrumentConstraints):
            raise ValueError("instrument constraints are invalid")
        self._policy = policy
        self._constraints = constraints

    def evaluate(self, portfolio: PortfolioSnapshot) -> StopLossDecision:
        """Return an inactive decision or a tick-aligned full-position stop."""

        if not isinstance(portfolio, PortfolioSnapshot):
            raise ValueError("portfolio snapshot is invalid")
        if self._policy.kind is StopLossKind.DISABLED or portfolio.base_quantity == 0:
            return StopLossDecision(Decimal("0"), None)
        if self._policy.kind is not StopLossKind.FIXED_PERCENT:
            raise InvalidOrderIntentError("Política de stop loss não suportada.")
        percentage = self._policy.value
        assert percentage is not None
        raw_stop = portfolio.average_entry_price * (
            Decimal("1") - percentage / _PERCENT_DENOMINATOR
        )
        tick = self._constraints.price_tick
        units = (raw_stop / tick).to_integral_value(rounding=ROUND_DOWN)
        stop_price = units * tick
        if stop_price <= 0:
            raise InvalidOrderIntentError(
                "O stop loss calculado ficou abaixo do menor tick de preço."
            )
        return StopLossDecision(portfolio.base_quantity, stop_price)
