"""Data-quality report tests."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app.market_data.domain import DataRange, QualitySeverity
from app.market_data.quality import MarketDataQualityValidator
from app.market_data.timeframes import get_timeframe
from tests.market_data_helpers import candle, utc


def test_complete_ordered_interval_has_no_quality_issues() -> None:
    start = utc(2026, 1, 1)
    candles = (candle(start), candle(start + timedelta(hours=1)))

    report = MarketDataQualityValidator().validate(
        candles,
        timeframe=get_timeframe("1h"),
        expected_range=DataRange(start, start + timedelta(hours=2)),
        now=utc(2026, 2, 1),
    )

    assert report.is_valid
    assert report.issues == ()
    assert report.expected_count == 2


def test_gap_duplicate_order_overlap_and_missing_range_are_reported() -> None:
    start = utc(2026, 1, 1)
    first = candle(start)
    duplicate = candle(start)
    after_gap = candle(start + timedelta(hours=3))

    report = MarketDataQualityValidator().validate(
        (first, duplicate, after_gap),
        timeframe=get_timeframe("1h"),
        expected_range=DataRange(start, start + timedelta(hours=4)),
        now=utc(2026, 2, 1),
    )
    codes = {issue.code for issue in report.issues}

    assert not report.is_valid
    assert {"duplicate", "gap", "missing_interval", "incomplete_range"} <= codes
    assert any(issue.severity is QualitySeverity.INFO for issue in report.issues)


def test_misalignment_ohlc_overlap_and_precision_are_blocking() -> None:
    start = utc(2026, 1, 1)
    first = candle(start)
    second = candle(start + timedelta(hours=1))
    object.__setattr__(first, "high", Decimal("50"))
    object.__setattr__(first, "close_time", second.open_time + timedelta(minutes=2))
    object.__setattr__(second, "open_time", second.open_time + timedelta(minutes=1))
    object.__setattr__(second, "open", Decimal("1.1234567890123456789"))

    report = MarketDataQualityValidator().validate(
        (first, second),
        timeframe=get_timeframe("1h"),
        now=utc(2026, 2, 1),
    )
    codes = {issue.code for issue in report.issues}

    assert {"invalid_ohlc", "overlapping_interval", "misaligned_timestamp"} <= codes
    assert "unexpected_precision" in codes
    assert not report.is_valid


def test_open_candle_is_a_warning() -> None:
    future = utc(2027, 1, 1)
    item = candle(future, is_closed=False)
    report = MarketDataQualityValidator().validate(
        (item,),
        timeframe=get_timeframe("1h"),
        now=utc(2026, 1, 1),
    )

    assert report.is_valid
    assert [(issue.code, issue.severity) for issue in report.issues] == [
        ("open_candle", QualitySeverity.WARNING)
    ]


def test_out_of_order_and_negative_volume_are_blocking() -> None:
    start = utc(2026, 1, 1)
    earlier = candle(start)
    later = candle(start + timedelta(hours=1))
    object.__setattr__(earlier, "volume", Decimal("-1"))

    report = MarketDataQualityValidator().validate(
        (later, earlier),
        timeframe=get_timeframe("1h"),
        now=utc(2026, 2, 1),
    )

    assert {"out_of_order", "negative_volume"} <= {issue.code for issue in report.issues}
    assert not report.is_valid
