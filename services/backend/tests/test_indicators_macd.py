from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from app.indicators import (
    DecimalSeries,
    InvalidIndicatorInputError,
    MovingAverageConvergenceDivergence,
    SeriesPoint,
    calculate_composite_as_of,
)


def _series(*values: str) -> DecimalSeries:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return DecimalSeries(
        tuple(
            SeriesPoint(start + timedelta(minutes=index), Decimal(value))
            for index, value in enumerate(values)
        )
    )


def _values(result_name: str, source: DecimalSeries) -> tuple[Decimal | None, ...]:
    result = MovingAverageConvergenceDivergence(2, 3, 2).calculate(source)
    return tuple(point.value for point in result.component(result_name).points)


def test_macd_uses_seeded_emas_and_explicit_signal_warmup() -> None:
    source = _series("1", "2", "3", "4", "5", "6")
    indicator = MovingAverageConvergenceDivergence(2, 3, 2)
    result = indicator.calculate(source)

    assert indicator.warmup_points == 3
    assert result.warmup_points == 3
    assert _values("macd", source) == (
        None,
        None,
        Decimal("0.5"),
        Decimal("0.5"),
        Decimal("0.5"),
        Decimal("0.5"),
    )
    assert _values("signal", source) == (
        None,
        None,
        None,
        Decimal("0.5"),
        Decimal("0.5"),
        Decimal("0.5"),
    )
    assert _values("histogram", source) == (
        None,
        None,
        None,
        Decimal("0.0"),
        Decimal("0.0"),
        Decimal("0.0"),
    )


def test_macd_signal_period_one_is_available_with_the_macd_line() -> None:
    source = _series("1", "2", "3", "4")
    result = MovingAverageConvergenceDivergence(2, 3, 1).calculate(source)

    assert tuple(point.value for point in result.component("signal").points) == (
        None,
        None,
        Decimal("0.5"),
        Decimal("0.5"),
    )
    assert tuple(point.value for point in result.component("histogram").points) == (
        None,
        None,
        Decimal("0.0"),
        Decimal("0.0"),
    )


def test_macd_short_source_contains_only_unavailable_values() -> None:
    result = MovingAverageConvergenceDivergence(2, 5, 3).calculate(_series("1", "2", "3"))

    assert all(
        point.value is None for _, component in result.components for point in component.points
    )


@pytest.mark.parametrize(
    ("fast", "slow", "signal"),
    [
        (0, 3, 2),
        (2, 0, 2),
        (2, 3, 0),
        (True, 3, 2),
        (3, 3, 2),
        (4, 3, 2),
    ],
)
def test_macd_rejects_invalid_periods(fast: object, slow: object, signal: object) -> None:
    with pytest.raises(InvalidIndicatorInputError):
        MovingAverageConvergenceDivergence(  # type: ignore[arg-type]
            fast,
            slow,
            signal,
        )


def test_macd_descriptor_is_canonical_and_versioned() -> None:
    indicator = MovingAverageConvergenceDivergence(5, 13, 4)

    assert indicator.descriptor.name == "macd"
    assert indicator.descriptor.version == "1"
    assert indicator.descriptor.parameters == (
        ("fast_period", 5),
        ("signal_period", 4),
        ("slow_period", 13),
    )


def test_macd_prefix_matches_the_same_prefix_of_full_calculation() -> None:
    source = _series("1", "2", "3", "4", "5", "6", "7")
    indicator = MovingAverageConvergenceDivergence(2, 3, 2)
    full = indicator.calculate(source)
    bounded = calculate_composite_as_of(indicator, source, as_of_index=4)

    for name in ("macd", "signal", "histogram"):
        assert bounded.component(name).points == full.component(name).points[:5]


def test_macd_is_independent_from_ambient_decimal_precision() -> None:
    source = _series("1", "1.1", "1.4", "2.8", "4.2", "9.1")
    indicator = MovingAverageConvergenceDivergence(2, 3, 2)

    with localcontext() as context:
        context.prec = 6
        low_precision = indicator.calculate(source)
    with localcontext() as context:
        context.prec = 40
        high_precision = indicator.calculate(source)

    assert low_precision == high_precision
