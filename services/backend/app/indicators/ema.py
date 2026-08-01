"""Deterministic exponential moving average."""

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


@dataclass(frozen=True, slots=True)
class ExponentialMovingAverage:
    """EMA seeded by the arithmetic mean of the first ``period`` values."""

    period: int
    descriptor: IndicatorDescriptor = field(init=False)

    def __post_init__(self) -> None:
        if isinstance(self.period, bool) or self.period < 1:
            raise InvalidIndicatorInputError("EMA period must be a positive integer")
        object.__setattr__(
            self,
            "descriptor",
            IndicatorDescriptor("ema", "1", (("period", self.period),)),
        )

    @property
    def warmup_points(self) -> int:
        return self.period - 1

    def calculate(self, source: DecimalSeries) -> IndicatorSeries:
        values: list[Decimal | None] = [None] * min(self.warmup_points, len(source))
        if len(source) >= self.period:
            with indicator_decimal_context():
                period_decimal = Decimal(self.period)
                seed = contextual(
                    sum(
                        (point.value for point in source.points[: self.period]),
                        Decimal("0"),
                    )
                    / period_decimal
                )
                values.append(seed)
                alpha = contextual(Decimal("2") / Decimal(self.period + 1))
                previous = seed
                for point in source.points[self.period :]:
                    previous = contextual(previous + alpha * (point.value - previous))
                    values.append(previous)

        return IndicatorSeries(
            descriptor=self.descriptor,
            warmup_points=self.warmup_points,
            points=tuple(
                IndicatorPoint(source_point.event_time, value)
                for source_point, value in zip(source.points, values, strict=True)
            ),
        )
