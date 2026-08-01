from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.indicators import (
    IndicatorBundle,
    IndicatorDescriptor,
    IndicatorPoint,
    IndicatorSeries,
    InvalidIndicatorInputError,
)

_START = datetime(2026, 1, 1, tzinfo=UTC)


def _series(*values: Decimal | None, minute_offset: int = 0) -> IndicatorSeries:
    warmup = next((index for index, value in enumerate(values) if value is not None), len(values))
    return IndicatorSeries(
        descriptor=IndicatorDescriptor("test_component", "1"),
        warmup_points=warmup,
        points=tuple(
            IndicatorPoint(
                _START + timedelta(minutes=index + minute_offset),
                value,
            )
            for index, value in enumerate(values)
        ),
    )


def test_indicator_bundle_exposes_named_aligned_components() -> None:
    first = _series(None, Decimal("1"))
    second = _series(None, None)
    bundle = IndicatorBundle(
        descriptor=IndicatorDescriptor("composite", "1"),
        components=(("first", first), ("second", second)),
    )

    assert len(bundle) == 2
    assert bundle.warmup_points == 2
    assert bundle.component("first") is first
    assert bundle.components[1][0] == "second"


def test_indicator_bundle_rejects_duplicate_or_unsafe_component_names() -> None:
    series = _series(Decimal("1"))

    with pytest.raises(InvalidIndicatorInputError):
        IndicatorBundle(
            IndicatorDescriptor("composite", "1"),
            (("same", series), ("same", series)),
        )
    with pytest.raises(InvalidIndicatorInputError):
        IndicatorBundle(
            IndicatorDescriptor("composite", "1"),
            (("bad name", series),),
        )


def test_indicator_bundle_rejects_misaligned_components() -> None:
    with pytest.raises(InvalidIndicatorInputError):
        IndicatorBundle(
            IndicatorDescriptor("composite", "1"),
            (
                ("first", _series(Decimal("1"))),
                ("second", _series(Decimal("1"), minute_offset=1)),
            ),
        )


def test_indicator_bundle_rejects_empty_and_unknown_components() -> None:
    with pytest.raises(InvalidIndicatorInputError):
        IndicatorBundle(IndicatorDescriptor("composite", "1"), ())

    bundle = IndicatorBundle(
        IndicatorDescriptor("composite", "1"),
        (("known", _series(Decimal("1"))),),
    )
    with pytest.raises(InvalidIndicatorInputError):
        bundle.component("missing")
