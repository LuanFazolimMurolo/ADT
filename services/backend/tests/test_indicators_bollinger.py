from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from app.indicators import (
    BollingerBands,
    DecimalSeries,
    InvalidIndicatorInputError,
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


def _values(
    component: str,
    source: DecimalSeries,
    *,
    period: int = 2,
    deviations: Decimal = Decimal("2"),
) -> tuple[Decimal | None, ...]:
    result = BollingerBands(period, deviations).calculate(source)
    return tuple(point.value for point in result.component(component).points)


def test_bollinger_uses_population_deviation_and_explicit_warmup() -> None:
    source = _series("1", "3", "5")
    indicator = BollingerBands(2, Decimal("2"))
    result = indicator.calculate(source)

    assert indicator.warmup_points == 1
    assert result.warmup_points == 1
    assert _values("middle", source) == (
        None,
        Decimal("2"),
        Decimal("4"),
    )
    assert _values("upper", source) == (
        None,
        Decimal("4"),
        Decimal("6"),
    )
    assert _values("lower", source) == (
        None,
        Decimal("0"),
        Decimal("2"),
    )


def test_bollinger_zero_volatility_collapses_all_bands() -> None:
    source = _series("7", "7", "7")
    result = BollingerBands(3).calculate(source)

    for name in ("middle", "upper", "lower"):
        assert tuple(point.value for point in result.component(name).points) == (
            None,
            None,
            Decimal("7"),
        )


def test_bollinger_period_one_has_zero_width() -> None:
    source = _series("1.5", "2.5")
    result = BollingerBands(1).calculate(source)

    expected = (Decimal("1.5"), Decimal("2.5"))
    for name in ("middle", "upper", "lower"):
        assert tuple(point.value for point in result.component(name).points) == expected


@pytest.mark.parametrize("period", [0, -1, True])
def test_bollinger_rejects_invalid_period(period: object) -> None:
    with pytest.raises(InvalidIndicatorInputError):
        BollingerBands(period)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "deviations",
    [Decimal("0"), Decimal("-1"), Decimal("NaN"), 2.0],
)
def test_bollinger_rejects_invalid_standard_deviations(deviations: object) -> None:
    with pytest.raises(InvalidIndicatorInputError):
        BollingerBands(20, deviations)  # type: ignore[arg-type]


def test_bollinger_descriptor_is_canonical_and_versioned() -> None:
    indicator = BollingerBands(10, Decimal("2.5"))

    assert indicator.descriptor.name == "bollinger_bands"
    assert indicator.descriptor.version == "1"
    assert indicator.descriptor.parameters == (
        ("period", 10),
        ("standard_deviations", Decimal("2.5")),
    )


def test_bollinger_prefix_matches_the_same_prefix_of_full_calculation() -> None:
    source = _series("1", "2", "3", "4", "5")
    indicator = BollingerBands(3)
    full = indicator.calculate(source)
    bounded = calculate_composite_as_of(indicator, source, as_of_index=3)

    for name in ("middle", "upper", "lower"):
        assert bounded.component(name).points == full.component(name).points[:4]


def test_bollinger_is_independent_from_ambient_decimal_precision() -> None:
    source = _series("1", "1.1", "1.9", "3.7", "8.2")
    indicator = BollingerBands(3, Decimal("2.25"))

    with localcontext() as context:
        context.prec = 6
        low_precision = indicator.calculate(source)
    with localcontext() as context:
        context.prec = 40
        high_precision = indicator.calculate(source)

    assert low_precision == high_precision
