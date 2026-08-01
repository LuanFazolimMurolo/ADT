"""Deterministic Wilder relative strength index."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.indicators._math import contextual, indicator_decimal_context
from app.indicators.domain import (
    DecimalSeries,
    IndicatorDescriptor,
    IndicatorPoint,
    IndicatorSeries,
)
from app.indicators.errors import InvalidIndicatorInputError

_HUNDRED = Decimal("100")
_NEUTRAL = Decimal("50")
_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class RelativeStrengthIndex:
    """Wilder RSI with explicit ``period``-change warmup."""

    period: int
    descriptor: IndicatorDescriptor = field(init=False)

    def __post_init__(self) -> None:
        if isinstance(self.period, bool) or self.period < 1:
            raise InvalidIndicatorInputError("RSI period must be a positive integer")
        object.__setattr__(
            self,
            "descriptor",
            IndicatorDescriptor("rsi", "1", (("period", self.period),)),
        )

    @property
    def warmup_points(self) -> int:
        return self.period

    def calculate(self, source: DecimalSeries) -> IndicatorSeries:
        values: list[Decimal | None] = [None] * min(self.warmup_points, len(source))
        if len(source) > self.period:
            with indicator_decimal_context():
                changes = tuple(
                    right.value - left.value
                    for left, right in zip(source.points, source.points[1:])
                )
                period_decimal = Decimal(self.period)
                seed_changes = changes[: self.period]
                average_gain = contextual(
                    sum((max(change, _ZERO) for change in seed_changes), _ZERO) / period_decimal
                )
                average_loss = contextual(
                    sum((max(-change, _ZERO) for change in seed_changes), _ZERO) / period_decimal
                )
                values.append(_rsi_value(average_gain, average_loss))

                smoothing_weight = Decimal(self.period - 1)
                for change in changes[self.period :]:
                    gain = max(change, _ZERO)
                    loss = max(-change, _ZERO)
                    average_gain = contextual(
                        (average_gain * smoothing_weight + gain) / period_decimal
                    )
                    average_loss = contextual(
                        (average_loss * smoothing_weight + loss) / period_decimal
                    )
                    values.append(_rsi_value(average_gain, average_loss))

        return IndicatorSeries(
            descriptor=self.descriptor,
            warmup_points=self.warmup_points,
            points=tuple(
                IndicatorPoint(source_point.event_time, value)
                for source_point, value in zip(source.points, values, strict=True)
            ),
        )


def _rsi_value(average_gain: Decimal, average_loss: Decimal) -> Decimal:
    if average_gain == _ZERO and average_loss == _ZERO:
        return _NEUTRAL
    if average_loss == _ZERO:
        return _HUNDRED
    if average_gain == _ZERO:
        return _ZERO
    relative_strength = contextual(average_gain / average_loss)
    return contextual(_HUNDRED - _HUNDRED / (Decimal("1") + relative_strength))
