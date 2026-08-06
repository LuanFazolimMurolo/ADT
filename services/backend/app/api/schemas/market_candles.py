"""Administrator-only local RAW candle chart contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, PlainSerializer

from app.api.schemas.common import ApiSchema
from app.market_data.candle_query import MarketCandlePage
from app.market_data.domain import Candle


def _validate_market_data_decimal(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("Market-data values must be finite Decimals.")
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -18:
        raise ValueError("Market-data values may have at most 18 decimal places.")
    return value


def _serialize_market_data_decimal(value: Decimal) -> str:
    return format(value, "f")


MarketDataDecimal = Annotated[
    Decimal,
    AfterValidator(_validate_market_data_decimal),
    PlainSerializer(_serialize_market_data_decimal, return_type=str, when_used="json"),
]


class MarketCandleResponse(ApiSchema):
    """One canonical closed OHLCV candle."""

    open_time: datetime
    close_time: datetime
    open: MarketDataDecimal
    high: MarketDataDecimal
    low: MarketDataDecimal
    close: MarketDataDecimal
    volume: MarketDataDecimal
    quote_volume: MarketDataDecimal | None
    trade_count: int | None
    is_closed: bool
    source: str

    @classmethod
    def from_domain(cls, candle: Candle) -> MarketCandleResponse:
        Candle.__post_init__(candle)
        return cls(
            open_time=candle.open_time,
            close_time=candle.close_time,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
            quote_volume=candle.quote_volume,
            trade_count=candle.trade_count,
            is_closed=candle.is_closed,
            source=candle.source,
        )


class MarketCandlePageResponse(ApiSchema):
    """One bounded backward page from a verified local RAW dataset."""

    schema_version: int
    exchange: str
    market_type: str
    symbol: str
    base_asset: str
    quote_asset: str
    timeframe: str
    requested_before: datetime | None
    available_start: datetime
    available_end: datetime
    range_start: datetime
    range_end: datetime
    limit: int
    count: int
    dataset_candle_count: int
    dataset_version: str
    dataset_version_algorithm: str
    content_checksum: str
    has_more_before: bool
    next_before: datetime | None
    items: list[MarketCandleResponse]

    @classmethod
    def from_domain(cls, page: MarketCandlePage) -> MarketCandlePageResponse:
        if not isinstance(page, MarketCandlePage):
            raise ValueError("Market candle page is invalid.")
        MarketCandlePage.__post_init__(page)
        return cls(
            schema_version=page.schema_version,
            exchange=page.exchange.value,
            market_type=page.market_type.value,
            symbol=page.pair.symbol,
            base_asset=page.pair.base,
            quote_asset=page.pair.quote,
            timeframe=page.timeframe.code,
            requested_before=page.requested_before,
            available_start=page.available_range.start,
            available_end=page.available_range.end,
            range_start=page.data_range.start,
            range_end=page.data_range.end,
            limit=page.limit,
            count=len(page.candles),
            dataset_candle_count=page.dataset_candle_count,
            dataset_version=page.dataset_version,
            dataset_version_algorithm=page.dataset_version_algorithm,
            content_checksum=page.content_checksum,
            has_more_before=page.has_more_before,
            next_before=page.next_before,
            items=[MarketCandleResponse.from_domain(item) for item in page.candles],
        )
