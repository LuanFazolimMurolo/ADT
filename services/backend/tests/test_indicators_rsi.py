from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from app.indicators import (
    DecimalSeries,
    InvalidIndicatorInputError,
    RelativeStrengthIndex,
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
    return tuple(point.value for point in RelativeStrengthIndex(period).calculate(source).points)


def test_rsi_uses_wilder_smoothing() -> None:
    source = _series("1", "2", "1", "2", "1")
    indicator = RelativeStrengthIndex(2)
    result = indicator.calculate(source)

    assert indicator.warmup_points == 2
    assert indicator.descriptor.parameters == (("period", 2),)
    assert tuple(point.value for point in result.points) == (
        None,
        None,
        Decimal("50"),
        Decimal("75"),
        Decimal("37.5"),
    )


def test_rsi_returns_boundaries_for_only_gains_or_only_losses() -> None:
    assert _values(_series("1", "2", "3", "4"), 3) == (
        None,
        None,
        None,
        Decimal("100"),
    )
    assert _values(_series("4", "3", "2", "1"), 3) == (
        None,
        None,
        None,
        Decimal("0"),
    )


def test_rsi_returns_neutral_value_for_flat_market() -> None:
    assert _values(_series("5", "5", "5", "5"), 3) == (
        None,
        None,
        None,
        Decimal("50"),
    )


def test_rsi_short_source_contains_only_warmup_values() -> None:
    assert _values(_series("1", "2"), 3) == (None, None)


@pytest.mark.parametrize("period", [0, -1, False])
def test_rsi_rejects_invalid_period(period: object) -> None:
    with pytest.raises(InvalidIndicatorInputError):
        RelativeStrengthIndex(period)  # type: ignore[arg-type]


def test_rsi_is_independent_from_ambient_decimal_precision() -> None:
    source = _series("1", "1.1", "1.05", "1.2", "1.17", "1.4")

    with localcontext() as context:
        context.prec = 6
        low_precision = _values(source, 3)
    with localcontext() as context:
        context.prec = 40
        high_precision = _values(source, 3)

    assert low_precision == high_precision


def test_rsi_prefix_matches_the_same_prefix_of_full_calculation() -> None:
    source = _series("1", "2", "1", "2", "1", "3")
    indicator = RelativeStrengthIndex(2)
    full = indicator.calculate(source)
    bounded = calculate_as_of(indicator, source, as_of_index=3)

    assert bounded.points == full.points[:4]
