"""Remote-free HTTP tests for the Phase 5-01 public asset API."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio

from app.api.dependencies.resources import get_asset_market_service
from app.main import app
from app.market_data.asset_catalog import AssetCatalogPage, AssetCatalogQuery
from app.market_data.domain import Instrument, MarketPrice, TradingPair
from app.market_data.errors import UnknownInstrumentError
from tests.market_data_helpers import INSTRUMENT, utc


class FakeAssetMarketService:
    async def list_assets(self, query: AssetCatalogQuery) -> AssetCatalogPage:
        return AssetCatalogPage(
            items=(INSTRUMENT,),
            page=query.page,
            page_size=query.page_size,
            total=1,
            fetched_at=utc(2026, 8, 2),
            expires_at=utc(2026, 8, 2) + timedelta(minutes=5),
            source="binance_spot_exchange_info",
        )

    async def get_asset(self, pair: TradingPair) -> Instrument:
        if pair != INSTRUMENT.pair:
            raise UnknownInstrumentError()
        return INSTRUMENT

    async def get_price(self, pair: TradingPair) -> MarketPrice:
        if pair != INSTRUMENT.pair:
            raise UnknownInstrumentError()
        return MarketPrice(
            instrument=INSTRUMENT,
            price=Decimal("67234.12000000"),
            observed_at=utc(2026, 8, 2),
            source="binance_spot_ticker_price_rest",
        )


@pytest_asyncio.fixture
async def asset_client() -> AsyncIterator[httpx.AsyncClient]:
    app.dependency_overrides[get_asset_market_service] = lambda: FakeAssetMarketService()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_asset_list_exposes_normalized_catalog_and_freshness(
    asset_client: httpx.AsyncClient,
) -> None:
    response = await asset_client.get(
        "/api/v1/market/assets",
        params={"quote_asset": "USDT", "page_size": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["symbol"] == "BTC/USDT"
    assert payload["items"][0]["supported_timeframes"] == [
        "1m",
        "5m",
        "15m",
        "30m",
        "1h",
        "4h",
        "1d",
    ]
    assert payload["total"] == 1
    assert payload["total_pages"] == 1
    assert payload["source"] == "binance_spot_exchange_info"


@pytest.mark.asyncio
async def test_asset_detail_and_price_use_canonical_pair_path(
    asset_client: httpx.AsyncClient,
) -> None:
    detail = await asset_client.get("/api/v1/market/assets/btc/usdt")
    price = await asset_client.get("/api/v1/market/assets/BTC/USDT/price")

    assert detail.status_code == 200
    assert detail.json()["native_symbol"] == "BTCUSDT"
    assert price.status_code == 200
    assert price.json()["price"] == "67234.12000000"
    assert price.json()["asset"]["symbol"] == "BTC/USDT"
    assert isinstance(json.loads(price.text)["price"], str)


@pytest.mark.asyncio
async def test_unknown_asset_uses_stable_domain_error(
    asset_client: httpx.AsyncClient,
) -> None:
    response = await asset_client.get("/api/v1/market/assets/ETH/USDT")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_instrument"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/market/assets/BTC!/USDT",
        "/api/v1/market/assets/BTC/BTC",
    ],
)
async def test_invalid_asset_path_is_rejected_without_source_access(
    asset_client: httpx.AsyncClient,
    path: str,
) -> None:
    response = await asset_client.get(path)
    assert response.status_code in {409, 422}
