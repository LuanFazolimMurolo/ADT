"""Small deterministic fixtures for Phase 2A tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.market_data.domain import (
    Candle,
    DataRange,
    Exchange,
    Instrument,
    MarketType,
    Timeframe,
    TradingPair,
)
from app.market_data.timeframes import get_timeframe

PAIR = TradingPair("BTC", "USDT")
INSTRUMENT = Instrument(
    exchange=Exchange.BINANCE,
    market_type=MarketType.SPOT,
    pair=PAIR,
    native_symbol="BTCUSDT",
    active=True,
    price_precision=8,
    quantity_precision=8,
)


def candle(
    open_time: datetime,
    *,
    timeframe: Timeframe | None = None,
    open_price: str = "100.00000000",
    high: str = "110.00000000",
    low: str = "90.00000000",
    close: str = "105.00000000",
    volume: str = "2.50000000",
    quote_volume: str | None = "250.00000000",
    trade_count: int | None = 10,
    is_closed: bool = True,
) -> Candle:
    selected_timeframe = timeframe or get_timeframe("1h")
    return Candle(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        symbol=PAIR.symbol,
        timeframe=selected_timeframe,
        open_time=open_time,
        close_time=open_time + selected_timeframe.duration - timedelta(milliseconds=1),
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        quote_volume=Decimal(quote_volume) if quote_volume is not None else None,
        trade_count=trade_count,
        is_closed=is_closed,
        source="test_fixture",
    )


def hourly_range(start: datetime, count: int) -> DataRange:
    return DataRange(start, start + timedelta(hours=count))


def utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def binance_kline(open_time: datetime, *, price: str = "100.00000000") -> list[object]:
    open_ms = round(open_time.timestamp() * 1000)
    close_ms = open_ms + 3_600_000 - 1
    return [
        open_ms,
        price,
        "110.00000000",
        "90.00000000",
        "105.00000000",
        "2.50000000",
        close_ms,
        "250.00000000",
        10,
        "1.00000000",
        "100.00000000",
        "0",
    ]


def exchange_info_payload() -> dict[str, object]:
    return {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "status": "TRADING",
                "baseAsset": "BTC",
                "baseAssetPrecision": 8,
                "quoteAsset": "USDT",
                "quoteAssetPrecision": 8,
            }
        ]
    }
