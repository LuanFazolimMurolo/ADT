"""Deterministic true range and Wilder average true range."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.indicators._math import contextual, indicator_decimal_context
from app.indicators.domain import (
    CandleSeries,
    IndicatorDescriptor,
    IndicatorPoint,
    IndicatorSeries,
)
from app.indicators.errors import InvalidIndicatorInputError

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class TrueRange:
    """Per-candle true range using the previous closed candle when available."""

    descriptor: IndicatorDescriptor = IndicatorDescriptor("true_range", "1")
    warmup_points: int = 0

    def calculate(self, source: CandleSeries) -> IndicatorSeries:
        with indicator_decimal_context():
            values = _true_ranges(source)
        return _aligned_series(self.descriptor, self.warmup_points, source, values)


@dataclass(frozen=True, slots=True)
class AverageTrueRange:
    """Wilder ATR seeded by the arithmetic mean of the first true ranges."""

    period: int
    descriptor: IndicatorDescriptor = field(init=False)

    def __post_init__(self) -> None:
        if isinstance(self.period, bool) or self.period < 1:
            raise InvalidIndicatorInputError("ATR period must be a positive integer")
        object.__setattr__(
            self,
            "descriptor",
            IndicatorDescriptor("atr", "1", (("period", self.period),)),
        )

    @property
    def warmup_points(self) -> int:
        return self.period - 1

    def calculate(self, source: CandleSeries) -> IndicatorSeries:
        values: list[Decimal | None] = [None] * min(self.warmup_points, len(source))
        if len(source) >= self.period:
            with indicator_decimal_context():
                true_ranges = _true_ranges(source)
                period_decimal = Decimal(self.period)
                previous = contextual(sum(true_ranges[: self.period], _ZERO) / period_decimal)
                values.append(previous)
                smoothing_weight = Decimal(self.period - 1)
                for true_range in true_ranges[self.period :]:
                    previous = contextual(
                        (previous * smoothing_weight + true_range) / period_decimal
                    )
                    values.append(previous)

        return _aligned_series(self.descriptor, self.warmup_points, source, values)


def _true_ranges(source: CandleSeries) -> tuple[Decimal, ...]:
    values: list[Decimal] = []
    previous_close: Decimal | None = None
    for candle in source.candles:
        intrabar_range = candle.high - candle.low
        if previous_close is None:
            true_range = intrabar_range
        else:
            true_range = max(
                intrabar_range,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        values.append(contextual(true_range))
        previous_close = candle.close
    return tuple(values)


def _aligned_series(
    descriptor: IndicatorDescriptor,
    warmup_points: int,
    source: CandleSeries,
    values: list[Decimal | None] | tuple[Decimal, ...],
) -> IndicatorSeries:
    return IndicatorSeries(
        descriptor=descriptor,
        warmup_points=warmup_points,
        points=tuple(
            IndicatorPoint(candle.close_time, value)
            for candle, value in zip(source.candles, values, strict=True)
        ),
    )
