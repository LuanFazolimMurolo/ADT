"""Safe strategy protocol and deterministic test/example strategies."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from app.backtesting.domain import (
    Fill,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    SimulatedOrder,
    StrategyDescriptor,
    TimeInForce,
)
from app.market_data.datasets import DatasetSnapshot
from app.market_data.domain import Candle


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Bounded immutable view of already-processed engine state."""

    snapshot: DatasetSnapshot
    candle_index: int
    current_candle: Candle | None
    history: tuple[Candle, ...]
    portfolio: PortfolioSnapshot
    open_orders: tuple[SimulatedOrder, ...]
    last_fill: Fill | None
    risk_halt: bool

    def __post_init__(self) -> None:
        if self.candle_index < -1:
            raise ValueError("strategy candle_index is invalid")
        if self.current_candle is None:
            if self.candle_index != -1 or self.history:
                raise ValueError("start context cannot expose candle history")
            return
        if self.candle_index < 0 or not self.history or self.history[-1] != self.current_candle:
            raise ValueError("strategy history must end at the current candle")
        if any(
            left.open_time >= right.open_time for left, right in zip(self.history, self.history[1:])
        ):
            raise ValueError("strategy history must be in strict chronological order")
        if any(candle.open_time > self.current_candle.open_time for candle in self.history):
            raise ValueError("strategy context contains a future candle")
        if any(order.status is not OrderStatus.OPEN for order in self.open_orders):
            raise ValueError("strategy open_orders must contain only OPEN orders")


class BacktestStrategy(Protocol):
    """Minimal plugin boundary; strategies return intents and mutate no engine state."""

    descriptor: StrategyDescriptor

    def on_start(self, context: StrategyContext) -> tuple[OrderIntent, ...]: ...

    def on_candle(
        self,
        context: StrategyContext,
        candle: Candle,
    ) -> tuple[OrderIntent, ...]: ...

    def on_fill(
        self,
        context: StrategyContext,
        fill: Fill,
    ) -> tuple[OrderIntent, ...]: ...

    def on_end(self, context: StrategyContext) -> None: ...


@dataclass(slots=True)
class NoOpStrategy:
    """Technical strategy that intentionally never submits an order."""

    descriptor: StrategyDescriptor = field(default_factory=lambda: StrategyDescriptor("no-op", "1"))

    def on_start(self, context: StrategyContext) -> tuple[OrderIntent, ...]:
        del context
        return ()

    def on_candle(
        self,
        context: StrategyContext,
        candle: Candle,
    ) -> tuple[OrderIntent, ...]:
        del context, candle
        return ()

    def on_fill(
        self,
        context: StrategyContext,
        fill: Fill,
    ) -> tuple[OrderIntent, ...]:
        del context, fill
        return ()

    def on_end(self, context: StrategyContext) -> None:
        del context


@dataclass(slots=True)
class BuyAndHoldExample:
    """Non-financial example: submit one MARKET BUY after the first candle."""

    quantity: Decimal
    descriptor: StrategyDescriptor = field(init=False)
    _submitted: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.quantity, Decimal)
            or not self.quantity.is_finite()
            or self.quantity <= 0
        ):
            raise ValueError("buy-and-hold example quantity must be a positive Decimal")
        self.descriptor = StrategyDescriptor(
            "buy-and-hold-example",
            "1",
            (("quantity", self.quantity),),
        )

    def on_start(self, context: StrategyContext) -> tuple[OrderIntent, ...]:
        del context
        self._submitted = False
        return ()

    def on_candle(
        self,
        context: StrategyContext,
        candle: Candle,
    ) -> tuple[OrderIntent, ...]:
        del context, candle
        if self._submitted:
            return ()
        self._submitted = True
        return (
            OrderIntent(
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=self.quantity,
                time_in_force=TimeInForce.GTC,
                client_tag="initial-buy",
            ),
        )

    def on_fill(
        self,
        context: StrategyContext,
        fill: Fill,
    ) -> tuple[OrderIntent, ...]:
        del context, fill
        return ()

    def on_end(self, context: StrategyContext) -> None:
        del context


@dataclass(slots=True)
class ScriptedStrategy:
    """Fixture-only deterministic strategy keyed by processed candle index."""

    candle_intents: tuple[tuple[int, tuple[OrderIntent, ...]], ...] = ()
    fill_intents: tuple[tuple[str, tuple[OrderIntent, ...]], ...] = ()
    descriptor: StrategyDescriptor = field(
        default_factory=lambda: StrategyDescriptor("scripted-test", "1")
    )
    _candle_map: dict[int, tuple[OrderIntent, ...]] = field(init=False, repr=False)
    _fill_map: dict[str, tuple[OrderIntent, ...]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._candle_map = dict(self.candle_intents)
        self._fill_map = dict(self.fill_intents)
        if len(self._candle_map) != len(self.candle_intents):
            raise ValueError("scripted candle indexes must be unique")
        if len(self._fill_map) != len(self.fill_intents):
            raise ValueError("scripted fill ids must be unique")
        if any(index < 0 for index in self._candle_map):
            raise ValueError("scripted candle indexes must be nonnegative")

    def on_start(self, context: StrategyContext) -> tuple[OrderIntent, ...]:
        del context
        return ()

    def on_candle(
        self,
        context: StrategyContext,
        candle: Candle,
    ) -> tuple[OrderIntent, ...]:
        del candle
        return self._candle_map.get(context.candle_index, ())

    def on_fill(
        self,
        context: StrategyContext,
        fill: Fill,
    ) -> tuple[OrderIntent, ...]:
        del context
        return self._fill_map.get(fill.fill_id, ())

    def on_end(self, context: StrategyContext) -> None:
        del context
