"""Remote-free HTTP tests for the Phase 5-01 public asset API."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio

from app.api.dependencies.resources import (
    get_asset_market_service,
    get_continuous_collection_state_store,
)
from app.main import app
from app.market_data.asset_catalog import AssetCatalogPage, AssetCatalogQuery
from app.market_data.continuous import (
    ContinuousCollectionPolicy,
    ContinuousCollectionState,
    ContinuousCollectionTarget,
    ContinuousCycleStatus,
    ContinuousTargetResult,
    ContinuousTargetStatus,
)
from app.market_data.domain import Instrument, MarketPrice, TradingPair
from app.market_data.errors import UnknownInstrumentError
from app.market_data.timeframes import get_timeframe
from tests.market_data_helpers import INSTRUMENT, utc


class FakeContinuousCollectionStateStore:
    def __init__(self, state: ContinuousCollectionState | None) -> None:
        self.state = state

    def read(self) -> ContinuousCollectionState | None:
        return self.state


def _collection_state() -> ContinuousCollectionState:
    target = ContinuousCollectionTarget(TradingPair("BTC", "USDT"), get_timeframe("1h"), 24)
    result = ContinuousTargetResult(
        target=target,
        status=ContinuousTargetStatus.NOOP,
        started_at=utc(2026, 8, 2),
        finished_at=utc(2026, 8, 2),
        latest_closed_end=utc(2026, 8, 2),
    )
    return ContinuousCollectionState(
        cycle_index=7,
        status=ContinuousCycleStatus.COMPLETED,
        policy=ContinuousCollectionPolicy(30, 2, 10),
        started_at=utc(2026, 8, 2),
        finished_at=utc(2026, 8, 2),
        next_cycle_at=utc(2026, 8, 2) + timedelta(seconds=30),
        results=(result,),
    )


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
    app.dependency_overrides[get_continuous_collection_state_store] = (
        lambda: FakeContinuousCollectionStateStore(_collection_state())
    )
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


@pytest.mark.asyncio
async def test_collection_status_exposes_latest_atomic_cycle(
    asset_client: httpx.AsyncClient,
) -> None:
    response = await asset_client.get("/api/v1/market/collection/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cycle_index"] == 7
    assert payload["status"] == "COMPLETED"
    assert payload["interval_seconds"] == 30
    assert payload["overlap_candles"] == 2
    assert payload["results"][0]["target"] == "BTC/USDT:1h"
    assert payload["results"][0]["status"] == "NOOP"
    assert payload["results"][0]["started_at"] == "2026-08-02T00:00:00Z"
    assert payload["results"][0]["finished_at"] == "2026-08-02T00:00:00Z"
    assert len(payload["checksum"]) == len(payload["cycle_id"]) == 64


@pytest.mark.asyncio
async def test_collection_status_missing_uses_stable_404(
    asset_client: httpx.AsyncClient,
) -> None:
    app.dependency_overrides[get_continuous_collection_state_store] = (
        lambda: FakeContinuousCollectionStateStore(None)
    )

    response = await asset_client.get("/api/v1/market/collection/status")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "continuous_collection_state_not_found"
