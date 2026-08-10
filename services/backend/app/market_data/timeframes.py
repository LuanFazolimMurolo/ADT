"""Configured canonical timeframe registry."""

from __future__ import annotations

from datetime import timedelta
from types import MappingProxyType

from app.market_data.domain import Exchange, Timeframe
from app.market_data.errors import UnsupportedTimeframeError


def _timeframe(
    code: str,
    duration: timedelta,
    binance_code: str,
    *,
    alignment: timedelta = timedelta(0),
) -> Timeframe:
    return Timeframe(
        code=code,
        duration=duration,
        alignment=alignment,
        native_codes=MappingProxyType({Exchange.BINANCE: binance_code}),
    )


TIMEFRAMES = MappingProxyType(
    {
        item.code: item
        for item in (
            _timeframe("1m", timedelta(minutes=1), "1m"),
            _timeframe("5m", timedelta(minutes=5), "5m"),
            _timeframe("15m", timedelta(minutes=15), "15m"),
            _timeframe("30m", timedelta(minutes=30), "30m"),
            _timeframe("1h", timedelta(hours=1), "1h"),
            _timeframe("4h", timedelta(hours=4), "4h"),
            _timeframe("12h", timedelta(hours=12), "12h"),
            _timeframe("1d", timedelta(days=1), "1d"),
            _timeframe(
                "1w",
                timedelta(days=7),
                "1w",
                alignment=timedelta(days=4),
            ),
        )
    }
)


def get_timeframe(code: str) -> Timeframe:
    """Resolve a configured timeframe by canonical code."""
    try:
        return TIMEFRAMES[code]
    except KeyError:
        raise UnsupportedTimeframeError() from None
