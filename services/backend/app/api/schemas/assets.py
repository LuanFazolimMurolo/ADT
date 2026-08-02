"""Public live-asset API contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, PlainSerializer

from app.api.schemas.common import ApiSchema
from app.market_data.asset_catalog import AssetCatalogPage
from app.market_data.domain import Instrument, MarketPrice, validate_instrument
from app.market_data.timeframes import TIMEFRAMES


def _validate_market_decimal(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError("Market price must be finite and positive.")
    return value


def _serialize_market_decimal(value: Decimal) -> str:
    return format(value, "f")


MarketDecimal = Annotated[
    Decimal,
    AfterValidator(_validate_market_decimal),
    PlainSerializer(_serialize_market_decimal, return_type=str, when_used="json"),
]


class AssetResponse(ApiSchema):
    """One normalized market instrument."""

    symbol: str
    base_asset: str
    quote_asset: str
    exchange: str
    market_type: str
    native_symbol: str
    active: bool
    price_precision: int | None
    quantity_precision: int | None
    supported_timeframes: list[str]

    @classmethod
    def from_domain(cls, instrument: Instrument) -> AssetResponse:
        validate_instrument(instrument)
        return cls(
            symbol=instrument.symbol,
            base_asset=instrument.pair.base,
            quote_asset=instrument.pair.quote,
            exchange=instrument.exchange.value,
            market_type=instrument.market_type.value,
            native_symbol=instrument.native_symbol,
            active=instrument.active,
            price_precision=instrument.price_precision,
            quantity_precision=instrument.quantity_precision,
            supported_timeframes=list(TIMEFRAMES),
        )


class AssetListResponse(ApiSchema):
    """Paginated live asset catalog with explicit source freshness."""

    items: list[AssetResponse]
    page: int
    page_size: int
    total: int
    total_pages: int
    catalog_fetched_at: datetime
    catalog_expires_at: datetime
    source: str

    @classmethod
    def from_domain(cls, page: AssetCatalogPage) -> AssetListResponse:
        if not isinstance(page, AssetCatalogPage):
            raise ValueError("Asset catalog page is invalid.")
        AssetCatalogPage.__post_init__(page)
        return cls(
            items=[AssetResponse.from_domain(item) for item in page.items],
            page=page.page,
            page_size=page.page_size,
            total=page.total,
            total_pages=page.total_pages,
            catalog_fetched_at=page.fetched_at,
            catalog_expires_at=page.expires_at,
            source=page.source,
        )


class AssetPriceResponse(ApiSchema):
    """Current public price for one normalized asset."""

    asset: AssetResponse
    price: MarketDecimal
    observed_at: datetime
    source: str

    @classmethod
    def from_domain(cls, observation: MarketPrice) -> AssetPriceResponse:
        if not isinstance(observation, MarketPrice):
            raise ValueError("Market price observation is invalid.")
        MarketPrice.__post_init__(observation)
        return cls(
            asset=AssetResponse.from_domain(observation.instrument),
            price=observation.price,
            observed_at=observation.observed_at,
            source=observation.source,
        )
