"""Explicit safe registry for locally executable Phase 3A strategies."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.backtesting.errors import UnsupportedStrategyError
from app.backtesting.strategy import BacktestStrategy, BuyAndHoldExample, NoOpStrategy


@dataclass(frozen=True, slots=True)
class StrategyRegistration:
    """One CLI-safe strategy registration; arbitrary module loading is forbidden."""

    name: str
    description: str
    requires_quantity: bool


_REGISTRATIONS = {
    "no-op": StrategyRegistration(
        name="no-op",
        description="Technical no-order strategy used to validate the engine.",
        requires_quantity=False,
    ),
    "buy-and-hold-example": StrategyRegistration(
        name="buy-and-hold-example",
        description="Non-financial example that buys once after the first candle.",
        requires_quantity=True,
    ),
}


class StrategyRegistry:
    """Build only explicitly approved strategies from validated scalar arguments."""

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(_REGISTRATIONS))

    def registrations(self) -> tuple[StrategyRegistration, ...]:
        return tuple(_REGISTRATIONS[name] for name in self.names)

    def build(self, name: str, *, quantity: Decimal | None = None) -> BacktestStrategy:
        registration = _REGISTRATIONS.get(name)
        if registration is None:
            raise UnsupportedStrategyError()
        if registration.requires_quantity:
            if quantity is None:
                raise UnsupportedStrategyError(
                    "A estratégia buy-and-hold-example exige --quantity."
                )
            return BuyAndHoldExample(quantity)
        if quantity is not None:
            raise UnsupportedStrategyError("A estratégia no-op não aceita --quantity.")
        return NoOpStrategy()
