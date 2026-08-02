"""Safe built-in strategy plugins and non-financial examples."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.backtesting.domain import (
    Fill,
    OrderIntent,
    OrderSide,
    OrderType,
    StrategyDescriptor,
    StrategyParameters,
    TimeInForce,
)
from app.backtesting.strategy import NoOpStrategy, StrategyContext
from app.indicators.domain import DecimalSeries
from app.indicators.ema import ExponentialMovingAverage
from app.indicators.protocols import calculate_as_of
from app.market_data.domain import Candle
from app.strategies.domain import (
    IndicatorCapability,
    StrategyIndicatorRequirement,
    StrategyParameterKind,
    StrategyParameterSpec,
    StrategyPluginDescriptor,
)
from app.strategies.errors import StrategyParameterValidationError

_EMA_CAPABILITY = IndicatorCapability("ema", "1", 1)
_EMA_PARAMETERS = (
    StrategyParameterSpec(
        "fast_period",
        StrategyParameterKind.INTEGER,
        required=False,
        default=3,
        minimum=1,
    ),
    StrategyParameterSpec(
        "quantity",
        StrategyParameterKind.DECIMAL,
        minimum=Decimal("0"),
    ),
    StrategyParameterSpec(
        "slow_period",
        StrategyParameterKind.INTEGER,
        required=False,
        default=5,
        minimum=2,
    ),
)
_EMA_INDICATORS = (
    StrategyIndicatorRequirement("fast_ema", _EMA_CAPABILITY),
    StrategyIndicatorRequirement("slow_ema", _EMA_CAPABILITY),
)


@dataclass(frozen=True, slots=True)
class NoOpStrategyPlugin:
    """Lifecycle 1 factory preserving the original no-op@1 identity."""

    descriptor: StrategyPluginDescriptor = StrategyPluginDescriptor(
        name="no-op",
        version="1",
        description="Technical no-order strategy used to validate plugin execution.",
    )

    def build(self, parameters: StrategyParameters) -> NoOpStrategy:
        if parameters:
            raise StrategyParameterValidationError("no-op does not accept parameters")
        return NoOpStrategy()


@dataclass(frozen=True, slots=True)
class NoOpStrategyPluginV2:
    """Lifecycle 2 no-op factory with explicit warmup capability."""

    descriptor: StrategyPluginDescriptor = StrategyPluginDescriptor(
        name="no-op",
        version="2",
        description="Warmup-aware technical no-order strategy.",
        lifecycle_version=2,
    )

    def build(self, parameters: StrategyParameters) -> NoOpStrategy:
        if parameters:
            raise StrategyParameterValidationError("no-op does not accept parameters")
        return NoOpStrategy(StrategyDescriptor("no-op", "2"))


@dataclass(slots=True)
class EmaCrossExampleStrategy:
    """Non-financial example reacting to an EMA relation change."""

    fast_period: int
    slow_period: int
    quantity: Decimal
    version: str = "1"
    descriptor: StrategyDescriptor = field(init=False)
    _previous_relation: int | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.fast_period, bool)
            or not isinstance(self.fast_period, int)
            or isinstance(self.slow_period, bool)
            or not isinstance(self.slow_period, int)
            or self.fast_period < 1
            or self.slow_period <= self.fast_period
        ):
            raise StrategyParameterValidationError("EMA periods must satisfy 1 <= fast < slow")
        if (
            not isinstance(self.quantity, Decimal)
            or not self.quantity.is_finite()
            or self.quantity <= 0
        ):
            raise StrategyParameterValidationError("EMA example quantity must be positive")
        self.descriptor = StrategyDescriptor(
            "ema-cross-example",
            self.version,
            (
                ("fast_period", self.fast_period),
                ("quantity", self.quantity),
                ("slow_period", self.slow_period),
            ),
        )

    def on_start(self, context: StrategyContext) -> tuple[OrderIntent, ...]:
        del context
        self._previous_relation = None
        return ()

    def on_warmup_candle(
        self,
        context: StrategyContext,
        candle: Candle,
    ) -> None:
        del candle
        relation = self._relation(context)
        if relation is not None:
            self._previous_relation = relation

    def on_candle(
        self,
        context: StrategyContext,
        candle: Candle,
    ) -> tuple[OrderIntent, ...]:
        del candle
        if context.risk_halt or context.open_orders:
            return ()
        relation = self._relation(context)
        if relation is None:
            return ()
        previous = self._previous_relation
        self._previous_relation = relation
        if previous is None:
            return ()
        if previous <= 0 < relation and context.portfolio.base_quantity == 0:
            return (
                OrderIntent(
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=self.quantity,
                    time_in_force=TimeInForce.GTC,
                    client_tag="ema-cross-entry",
                ),
            )
        if previous >= 0 > relation and context.portfolio.base_quantity > 0:
            return (
                OrderIntent(
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=context.portfolio.base_quantity,
                    time_in_force=TimeInForce.GTC,
                    client_tag="ema-cross-exit",
                ),
            )
        return ()

    def _relation(self, context: StrategyContext) -> int | None:
        source = DecimalSeries.from_candles(context.history)
        if len(source) < self.slow_period:
            return None
        as_of_index = len(source) - 1
        fast = (
            calculate_as_of(
                ExponentialMovingAverage(self.fast_period),
                source,
                as_of_index=as_of_index,
            )
            .at(as_of_index)
            .value
        )
        slow = (
            calculate_as_of(
                ExponentialMovingAverage(self.slow_period),
                source,
                as_of_index=as_of_index,
            )
            .at(as_of_index)
            .value
        )
        if fast is None or slow is None:
            return None
        return (fast > slow) - (fast < slow)

    def on_fill(self, context: StrategyContext, fill: Fill) -> tuple[OrderIntent, ...]:
        del context, fill
        return ()

    def on_end(self, context: StrategyContext) -> None:
        del context


@dataclass(frozen=True, slots=True)
class EmaCrossExamplePlugin:
    """Lifecycle 1 factory preserving the original EMA example identity."""

    descriptor: StrategyPluginDescriptor = StrategyPluginDescriptor(
        name="ema-cross-example",
        version="1",
        description=(
            "Non-financial example that emits orders only after an observed EMA relation change."
        ),
        parameters=_EMA_PARAMETERS,
        indicators=_EMA_INDICATORS,
    )

    def build(self, parameters: StrategyParameters) -> EmaCrossExampleStrategy:
        values = dict(parameters)
        fast_period = values.get("fast_period")
        slow_period = values.get("slow_period")
        quantity = values.get("quantity")
        if (
            isinstance(fast_period, bool)
            or not isinstance(fast_period, int)
            or isinstance(slow_period, bool)
            or not isinstance(slow_period, int)
            or not isinstance(quantity, Decimal)
        ):
            raise StrategyParameterValidationError("normalized EMA example parameters are invalid")
        return EmaCrossExampleStrategy(fast_period, slow_period, quantity)


@dataclass(frozen=True, slots=True)
class EmaCrossExamplePluginV2:
    """Lifecycle 2 factory for the warmup-aware EMA-cross example."""

    descriptor: StrategyPluginDescriptor = StrategyPluginDescriptor(
        name="ema-cross-example",
        version="2",
        description=(
            "Warmup-aware non-financial example that emits orders after an EMA relation change."
        ),
        parameters=_EMA_PARAMETERS,
        indicators=_EMA_INDICATORS,
        lifecycle_version=2,
    )

    def build(self, parameters: StrategyParameters) -> EmaCrossExampleStrategy:
        values = dict(parameters)
        fast_period = values.get("fast_period")
        slow_period = values.get("slow_period")
        quantity = values.get("quantity")
        if (
            isinstance(fast_period, bool)
            or not isinstance(fast_period, int)
            or isinstance(slow_period, bool)
            or not isinstance(slow_period, int)
            or not isinstance(quantity, Decimal)
        ):
            raise StrategyParameterValidationError("normalized EMA example parameters are invalid")
        return EmaCrossExampleStrategy(fast_period, slow_period, quantity, version="2")
