"""Deterministic Bollinger bands."""

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
)
from app.indicators.errors import InvalidIndicatorInputError

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class BollingerBands:
    """Population-standard-deviation bands around a rolling arithmetic mean."""

    period: int = 20
    standard_deviations: Decimal = Decimal("2")
    descriptor: IndicatorDescriptor = field(init=False)

    def __post_init__(self) -> None:
        if isinstance(self.period, bool) or self.period < 1:
            raise InvalidIndicatorInputError("Bollinger period must be a positive integer")
        if (
            not isinstance(self.standard_deviations, Decimal)
            or not self.standard_deviations.is_finite()
            or self.standard_deviations <= _ZERO
        ):
            raise InvalidIndicatorInputError(
                "Bollinger standard_deviations must be a positive finite Decimal"
            )
        object.__setattr__(
            self,
            "descriptor",
            IndicatorDescriptor(
                "bollinger_bands",
                "1",
                (
                    ("period", self.period),
                    ("standard_deviations", self.standard_deviations),
                ),
            ),
        )

    @property
    def warmup_points(self) -> int:
        return self.period - 1

    def calculate(self, source: DecimalSeries) -> IndicatorBundle:
        middle_values: list[Decimal | None] = [None] * min(
            self.warmup_points,
            len(source),
        )
        upper_values: list[Decimal | None] = list(middle_values)
        lower_values: list[Decimal | None] = list(middle_values)

        with indicator_decimal_context():
            period_decimal = Decimal(self.period)
            for end_index in range(self.period - 1, len(source)):
                window = source.points[end_index - self.period + 1 : end_index + 1]
                middle = contextual(sum((point.value for point in window), _ZERO) / period_decimal)
                variance = contextual(
                    sum(
                        (contextual((point.value - middle) ** 2) for point in window),
                        _ZERO,
                    )
                    / period_decimal
                )
                deviation = contextual(variance.sqrt())
                distance = contextual(self.standard_deviations * deviation)
                middle_values.append(middle)
                upper_values.append(contextual(middle + distance))
                lower_values.append(contextual(middle - distance))

        parameters = self.descriptor.parameters
        return IndicatorBundle(
            descriptor=self.descriptor,
            components=(
                (
                    "middle",
                    _aligned_series(
                        IndicatorDescriptor("bollinger_middle", "1", parameters),
                        self.warmup_points,
                        source,
                        middle_values,
                    ),
                ),
                (
                    "upper",
                    _aligned_series(
                        IndicatorDescriptor("bollinger_upper", "1", parameters),
                        self.warmup_points,
                        source,
                        upper_values,
                    ),
                ),
                (
                    "lower",
                    _aligned_series(
                        IndicatorDescriptor("bollinger_lower", "1", parameters),
                        self.warmup_points,
                        source,
                        lower_values,
                    ),
                ),
            ),
        )


def _aligned_series(
    descriptor: IndicatorDescriptor,
    warmup_points: int,
    source: DecimalSeries,
    values: list[Decimal | None],
) -> IndicatorSeries:
    return IndicatorSeries(
        descriptor=descriptor,
        warmup_points=warmup_points,
        points=tuple(
            IndicatorPoint(source_point.event_time, value)
            for source_point, value in zip(source.points, values, strict=True)
        ),
    )
