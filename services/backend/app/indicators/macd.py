"""Deterministic moving average convergence divergence."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.indicators._math import contextual, indicator_decimal_context
from app.indicators.domain import (
    DecimalSeries,
    IndicatorBundle,
    IndicatorDescriptor,
    IndicatorPoint,
    IndicatorSeries,
    SeriesPoint,
)
from app.indicators.ema import ExponentialMovingAverage
from app.indicators.errors import InvalidIndicatorInputError


@dataclass(frozen=True, slots=True)
class MovingAverageConvergenceDivergence:
    """MACD with SMA-seeded EMAs and an EMA signal line."""

    fast_period: int = 12
    slow_period: int = 26
    signal_period: int = 9
    descriptor: IndicatorDescriptor = field(init=False)

    def __post_init__(self) -> None:
        periods = (self.fast_period, self.slow_period, self.signal_period)
        if any(isinstance(period, bool) or period < 1 for period in periods):
            raise InvalidIndicatorInputError("MACD periods must be positive integers")
        if self.fast_period >= self.slow_period:
            raise InvalidIndicatorInputError("MACD fast_period must be smaller than slow_period")
        object.__setattr__(
            self,
            "descriptor",
            IndicatorDescriptor(
                "macd",
                "1",
                (
                    ("fast_period", self.fast_period),
                    ("slow_period", self.slow_period),
                    ("signal_period", self.signal_period),
                ),
            ),
        )

    @property
    def warmup_points(self) -> int:
        return self.slow_period + self.signal_period - 2

    def calculate(self, source: DecimalSeries) -> IndicatorBundle:
        fast = ExponentialMovingAverage(self.fast_period).calculate(source)
        slow = ExponentialMovingAverage(self.slow_period).calculate(source)

        with indicator_decimal_context():
            macd_values = tuple(
                None
                if slow_point.value is None
                else contextual(_required(fast_point.value) - slow_point.value)
                for fast_point, slow_point in zip(
                    fast.points,
                    slow.points,
                    strict=True,
                )
            )

            compact_macd = DecimalSeries(
                tuple(
                    SeriesPoint(source_point.event_time, _required(value))
                    for source_point, value in zip(
                        source.points,
                        macd_values,
                        strict=True,
                    )
                    if value is not None
                )
            )
            compact_signal = ExponentialMovingAverage(self.signal_period).calculate(compact_macd)
            unavailable_prefix = min(self.slow_period - 1, len(source))
            signal_values = (None,) * unavailable_prefix + tuple(
                point.value for point in compact_signal.points
            )
            histogram_values = tuple(
                None if signal_value is None else contextual(_required(macd_value) - signal_value)
                for macd_value, signal_value in zip(
                    macd_values,
                    signal_values,
                    strict=True,
                )
            )

        parameters = self.descriptor.parameters
        macd_line = _aligned_series(
            IndicatorDescriptor("macd_line", "1", parameters),
            self.slow_period - 1,
            source,
            macd_values,
        )
        signal_line = _aligned_series(
            IndicatorDescriptor("macd_signal", "1", parameters),
            self.warmup_points,
            source,
            signal_values,
        )
        histogram = _aligned_series(
            IndicatorDescriptor("macd_histogram", "1", parameters),
            self.warmup_points,
            source,
            histogram_values,
        )
        return IndicatorBundle(
            descriptor=self.descriptor,
            components=(
                ("macd", macd_line),
                ("signal", signal_line),
                ("histogram", histogram),
            ),
        )


def _required(value: Decimal | None) -> Decimal:
    if value is None:
        raise AssertionError("an aligned MACD value was unexpectedly unavailable")
    return value


def _aligned_series(
    descriptor: IndicatorDescriptor,
    warmup_points: int,
    source: DecimalSeries,
    values: tuple[Decimal | None, ...],
) -> IndicatorSeries:
    return IndicatorSeries(
        descriptor=descriptor,
        warmup_points=warmup_points,
        points=tuple(
            IndicatorPoint(source_point.event_time, value)
            for source_point, value in zip(source.points, values, strict=True)
        ),
    )
