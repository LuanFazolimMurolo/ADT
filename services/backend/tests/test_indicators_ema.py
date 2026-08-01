from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from app.indicators import (
    DecimalSeries,
    ExponentialMovingAverage,
    InvalidIndicatorInputError,
    SeriesPoint,
    calculate_as_of,
)


def _series(*values: str) -> DecimalSeries:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return DecimalSeries(
        tuple(
            SeriesPoint(start + timedelta(minutes=index), Decimal(value))
            for index, value in enumerate(values)
        )
    )


def _values(source: DecimalSeries, period: int) -> tuple[Decimal | None, ...]:
    return tuple(point.value for point in ExponentialMovingAverage(period).calculate(source).points)


def test_ema_uses_sma_seed_and_explicit_warmup() -> None:
    source = _series("1", "2", "3", "4", "5")
    indicator = ExponentialMovingAverage(3)
    result = indicator.calculate(source)

    assert indicator.warmup_points == 2
    assert indicator.descriptor.parameters == (("period", 3),)
    assert tuple(point.value for point in result.points) == (
        None,
        None,
        Decimal("2"),
        Decimal("3"),
        Decimal("4"),
    )
    assert tuple(point.event_time for point in result.points) == tuple(
        point.event_time for point in source.points
    )


def test_ema_period_one_equals_the_source() -> None:
    source = _series("1.25", "2.50", "8")

    assert _values(source, 1) == (
        Decimal("1.25"),
        Decimal("2.50"),
        Decimal("8"),
    )


def test_ema_short_source_contains_only_warmup_values() -> None:
    assert _values(_series("1", "2"), 4) == (None, None)


@pytest.mark.parametrize("period", [0, -1, True])
def test_ema_rejects_invalid_period(period: object) -> None:
    with pytest.raises(InvalidIndicatorInputError):
        ExponentialMovingAverage(period)  # type: ignore[arg-type]


def test_ema_is_independent_from_ambient_decimal_precision() -> None:
    source = _series("1", "2", "4", "8", "16")

    with localcontext() as context:
        context.prec = 6
        low_precision = _values(source, 2)
    with localcontext() as context:
        context.prec = 40
        high_precision = _values(source, 2)

    assert low_precision == high_precision


def test_ema_prefix_matches_the_same_prefix_of_full_calculation() -> None:
    source = _series("1", "2", "3", "4", "5", "6")
    indicator = ExponentialMovingAverage(3)
    full = indicator.calculate(source)
    bounded = calculate_as_of(indicator, source, as_of_index=3)

    assert bounded.points == full.points[:4]
