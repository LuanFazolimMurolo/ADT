"""Canonical market-data domain and timeframe tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.market_data.domain import Candle, DataRange, Exchange, TradingPair
from app.market_data.errors import (
    InvalidDataRangeError,
    MarketDataInconsistencyError,
    UnsupportedTimeframeError,
)
from app.market_data.quality import MarketDataQualityValidator
from app.market_data.timeframes import TIMEFRAMES, get_timeframe
from tests.market_data_helpers import candle, utc


def test_candle_preserves_decimal_and_utc() -> None:
    item = candle(utc(2026, 1, 1))

    assert isinstance(item.open, Decimal)
    assert item.open_time.tzinfo is UTC
    assert item.key == (Exchange.BINANCE, "BTC/USDT", "1h", utc(2026, 1, 1))


@pytest.mark.parametrize(
    "changes",
    [
        {"high": "99"},
        {"low": "106"},
        {"volume": "-1"},
        {"quote_volume": "-1"},
    ],
)
def test_candle_rejects_invalid_ohlcv(changes: dict[str, str]) -> None:
    with pytest.raises(MarketDataInconsistencyError):
        candle(utc(2026, 1, 1), **changes)


def test_candle_rejects_float_financial_values() -> None:
    valid = candle(utc(2026, 1, 1))
    values = {field_name: getattr(valid, field_name) for field_name in Candle.__dataclass_fields__}
    values["open"] = 100.0

    with pytest.raises(MarketDataInconsistencyError):
        Candle(**values)


def test_closed_future_candle_is_rejected_but_open_future_candle_is_explicit() -> None:
    future = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(days=2)
    closed = candle(future, is_closed=True)
    assert candle(future, is_closed=False).is_closed is False
    report = MarketDataQualityValidator(clock=lambda: utc(2026, 1, 1)).validate(
        (closed,),
        timeframe=get_timeframe("1h"),
    )
    assert any(issue.code == "future_candle" for issue in report.issues)


def test_non_utc_datetime_is_rejected_even_when_aware() -> None:
    non_utc = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=-3)))

    with pytest.raises(MarketDataInconsistencyError):
        candle(non_utc)


def test_submillisecond_timestamp_is_rejected() -> None:
    with pytest.raises(MarketDataInconsistencyError):
        candle(utc(2026, 1, 1) + timedelta(microseconds=1))


@pytest.mark.parametrize("code", ["1m", "5m", "15m", "30m", "1h", "4h", "1d"])
def test_configured_timeframes_align_and_advance(code: str) -> None:
    timeframe = get_timeframe(code)
    opening = utc(2026, 1, 1)

    assert timeframe.validate_open_time(opening)
    assert timeframe.next_open_time(opening) == opening + timeframe.duration
    assert timeframe.native_code(Exchange.BINANCE) == code


def test_timeframe_rejects_misalignment_and_unknown_code() -> None:
    assert not get_timeframe("5m").validate_open_time(utc(2026, 1, 1) + timedelta(minutes=1))
    with pytest.raises(UnsupportedTimeframeError):
        get_timeframe("2h")
    assert len(TIMEFRAMES) == 7


def test_trading_pair_is_canonical_and_blocks_path_traversal() -> None:
    assert TradingPair.parse("btc/usdt").symbol == "BTC/USDT"
    assert TradingPair.parse("btc/usdt").safe_path_component == "BTC_USDT"

    for unsafe in ("../USDT", "BTC/../../tmp", "BTCUSDT", "BTC/BTC"):
        with pytest.raises(MarketDataInconsistencyError):
            TradingPair.parse(unsafe)


def test_data_range_is_half_open_utc_and_ordered() -> None:
    data_range = DataRange(utc(2026, 1, 1), utc(2026, 1, 2))
    assert data_range.end - data_range.start == timedelta(days=1)

    with pytest.raises(InvalidDataRangeError):
        DataRange(data_range.end, data_range.start)
