from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.indicators import (
    DecimalSeries,
    FutureDataAccessError,
    IndicatorDescriptor,
    IndicatorPoint,
    IndicatorSeries,
    InvalidIndicatorInputError,
    SeriesPoint,
    UnsupportedIndicatorSchemaError,
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


def test_descriptor_canonicalizes_parameter_order() -> None:
    descriptor = IndicatorDescriptor(
        " example ",
        " 2 ",
        (("zeta", Decimal("1.5")), ("alpha", 3)),
    )

    assert descriptor.name == "example"
    assert descriptor.version == "2"
    assert descriptor.parameters == (("alpha", 3), ("zeta", Decimal("1.5")))
    assert descriptor.canonical_key == (
        1,
        "example",
        "2",
        (("alpha", 3), ("zeta", Decimal("1.5"))),
    )


@pytest.mark.parametrize(
    "parameters",
    [
        (("period", 2), ("period", 3)),
        (("bad key", 2),),
        (("period", 2.0),),
        (("period", Decimal("NaN")),),
    ],
)
def test_descriptor_rejects_noncanonical_parameters(parameters: object) -> None:
    with pytest.raises(InvalidIndicatorInputError):
        IndicatorDescriptor("example", "1", parameters)  # type: ignore[arg-type]


def test_descriptor_rejects_future_schema() -> None:
    with pytest.raises(UnsupportedIndicatorSchemaError):
        IndicatorDescriptor("example", "1", schema_version=2)


def test_decimal_series_is_immutable_and_strictly_chronological() -> None:
    series = _series("1", "2", "3")

    assert tuple(point.value for point in series.points) == (
        Decimal("1"),
        Decimal("2"),
        Decimal("3"),
    )
    with pytest.raises(AttributeError):
        series.points = ()  # type: ignore[misc]

    repeated_time = series.points[0].event_time
    with pytest.raises(InvalidIndicatorInputError):
        DecimalSeries(
            (
                SeriesPoint(repeated_time, Decimal("1")),
                SeriesPoint(repeated_time, Decimal("2")),
            )
        )


def test_series_point_rejects_naive_time_and_nonfinite_value() -> None:
    with pytest.raises(ValueError):
        SeriesPoint(datetime(2026, 1, 1), Decimal("1"))
    with pytest.raises(InvalidIndicatorInputError):
        SeriesPoint(datetime(2026, 1, 1, tzinfo=UTC), Decimal("Infinity"))


def test_bounded_source_view_rejects_future_data() -> None:
    source = _series("10", "20", "30", "40")
    view = source.through(1)

    assert len(view) == 2
    assert view.latest.value == Decimal("20")
    assert view.at(0).value == Decimal("10")
    with pytest.raises(FutureDataAccessError):
        view.at(2)
    with pytest.raises(InvalidIndicatorInputError):
        view.at(-1)


@dataclass(frozen=True, slots=True)
class _LengthIndicator:
    descriptor: IndicatorDescriptor = IndicatorDescriptor("length-test", "1")
    warmup_points: int = 0

    def calculate(self, source: DecimalSeries) -> IndicatorSeries:
        return IndicatorSeries(
            self.descriptor,
            self.warmup_points,
            tuple(
                IndicatorPoint(point.event_time, Decimal(len(source))) for point in source.points
            ),
        )


def test_calculate_as_of_materializes_only_the_visible_prefix() -> None:
    result = calculate_as_of(_LengthIndicator(), _series("1", "2", "3", "4"), as_of_index=1)

    assert len(result) == 2
    assert tuple(point.value for point in result.points) == (Decimal("2"), Decimal("2"))


def test_indicator_series_requires_explicit_contiguous_warmup() -> None:
    source = _series("1", "2", "3")
    descriptor = IndicatorDescriptor("example", "1")

    with pytest.raises(InvalidIndicatorInputError):
        IndicatorSeries(
            descriptor,
            1,
            (
                IndicatorPoint(source.points[0].event_time, Decimal("1")),
                IndicatorPoint(source.points[1].event_time, Decimal("2")),
                IndicatorPoint(source.points[2].event_time, Decimal("3")),
            ),
        )

    with pytest.raises(InvalidIndicatorInputError):
        IndicatorSeries(
            descriptor,
            1,
            (
                IndicatorPoint(source.points[0].event_time, None),
                IndicatorPoint(source.points[1].event_time, None),
                IndicatorPoint(source.points[2].event_time, Decimal("3")),
            ),
        )


def test_bounded_indicator_view_rejects_future_output() -> None:
    source = _series("1", "2", "3")
    result = _LengthIndicator().calculate(source)
    view = result.through(1)

    assert view.latest.value == Decimal("3")
    with pytest.raises(FutureDataAccessError):
        view.at(2)
