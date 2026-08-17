"""Remote-free HTTP boundary tests for persisted RAW dataset inspection."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import get_raw_dataset_read_service
from app.api.routes import admin_market_datasets
from app.main import create_app
from app.market_data.catalog import DatasetMetadata
from app.market_data.domain import Exchange, MarketType, TradingPair
from app.market_data.integrity import (
    RAW_DATASET_VERSION_ALGORITHM,
    RawPartitionIntegrityEntry,
    build_raw_partition_integrity_manifest,
)
from app.market_data.operations import MarketDatasetSelector, encode_dataset_id
from app.market_data.raw_dataset_query import LocalRawDatasetReadService
from app.market_data.storage import compose_raw_dataset_version
from app.market_data.timeframes import get_timeframe

ADMIN_ID: Final = UUID("10000000-0000-4000-8000-000000000001")
AUTH_HEADERS: Final = {"Authorization": "Bearer phase-7-03-test-token"}

SECRET_LOCATION: Final = "/srv/ADT_DATA_DIR/private-do-not-expose"
SECRET_PARTITION_NAME: Final = "candles.parquet"


def _selector(
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
) -> MarketDatasetSelector:
    return MarketDatasetSelector(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        pair=TradingPair.parse(symbol),
        timeframe=get_timeframe(timeframe),
    )


def _metadata() -> DatasetMetadata:
    selector = _selector()

    entry = RawPartitionIntegrityEntry(
        relative_path=(
            "exchange=binance/"
            "market=spot/"
            "base=BTC/"
            "quote=USDT/"
            "timeframe=1h/"
            "year=2026/"
            "month=08/"
            f"{SECRET_PARTITION_NAME}"
        ),
        checksum="a" * 64,
    )
    entries = (entry,)

    version = compose_raw_dataset_version((item.relative_path, item.checksum) for item in entries)

    return DatasetMetadata(
        key=selector.canonical_key,
        exchange=selector.exchange.value,
        market_type=selector.market_type.value,
        symbol=selector.pair.symbol,
        native_symbol="BTCUSDT",
        timeframe=selector.timeframe.code,
        location=SECRET_LOCATION,
        first_open_time="2026-08-01T00:00:00+00:00",
        last_open_time="2026-08-01T02:00:00+00:00",
        candle_count=3,
        version=version,
        updated_at=datetime(
            2026,
            8,
            16,
            12,
            0,
            tzinfo=UTC,
        ).isoformat(),
        version_algorithm=RAW_DATASET_VERSION_ALGORITHM,
        partition_integrity=build_raw_partition_integrity_manifest(
            version,
            entries,
        ),
    )


class FakeRawCatalog:
    """Expose only the approved read-only catalog surface."""

    def __init__(self) -> None:
        metadata = _metadata()
        self.datasets = {metadata.key: metadata}
        self.list_calls = 0
        self.get_calls: list[str] = []

    def list_datasets_snapshot(
        self,
        *,
        timeout_seconds: float,
    ) -> tuple[DatasetMetadata, ...]:
        self.list_calls += 1
        return tuple(self.datasets.values())

    def get_dataset_snapshot(
        self,
        key: str,
        *,
        timeout_seconds: float,
    ) -> DatasetMetadata | None:
        self.get_calls.append(key)
        return self.datasets.get(key)


@pytest.fixture
def api() -> tuple[FastAPI, FakeRawCatalog]:
    application = create_app()
    catalog = FakeRawCatalog()
    service = LocalRawDatasetReadService(catalog)

    async def administrator_override() -> UUID:
        return ADMIN_ID

    def service_override() -> LocalRawDatasetReadService:
        return service

    application.dependency_overrides[require_administrator] = administrator_override
    application.dependency_overrides[get_raw_dataset_read_service] = service_override

    return application, catalog


@pytest.fixture
async def client(
    api: tuple[FastAPI, FakeRawCatalog],
) -> AsyncIterator[AsyncClient]:
    application, _catalog = api

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as http_client:
        yield http_client


@pytest.mark.asyncio
async def test_list_returns_bounded_sanitized_raw_dataset_page(
    client: AsyncClient,
    api: tuple[FastAPI, FakeRawCatalog],
) -> None:
    response = await client.get(
        "/api/v1/admin/market-data/datasets",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"

    body = response.json()

    assert body["page"] == 1
    assert body["page_size"] == 25
    assert body["total"] == 1
    assert body["total_pages"] == 1

    item = body["items"][0]

    assert item["dataset_id"] == encode_dataset_id(_selector())
    assert item["exchange"] == "binance"
    assert item["market_type"] == "spot"
    assert item["symbol"] == "BTC/USDT"
    assert item["base_asset"] == "BTC"
    assert item["quote_asset"] == "USDT"
    assert item["timeframe"] == "1h"

    assert item["first_open_time"] == "2026-08-01T00:00:00Z"
    assert item["last_open_time"] == "2026-08-01T02:00:00Z"
    assert item["coverage_start"] == "2026-08-01T00:00:00Z"
    assert item["coverage_end"] == "2026-08-01T03:00:00Z"

    assert item["candle_count"] == 3
    assert item["integrity"]["present"] is True
    assert item["integrity"]["partition_count"] == 1

    response_text = response.text

    assert "location" not in response_text
    assert "relative_path" not in response_text
    assert "ADT_DATA_DIR" not in response_text
    assert SECRET_LOCATION not in response_text
    assert SECRET_PARTITION_NAME not in response_text
    assert "year=2026" not in response_text

    assert api[1].list_calls == 1
    assert api[1].get_calls == []


@pytest.mark.asyncio
async def test_detail_uses_canonical_dataset_id_and_hides_storage(
    client: AsyncClient,
    api: tuple[FastAPI, FakeRawCatalog],
) -> None:
    dataset_id = encode_dataset_id(_selector())

    response = await client.get(
        f"/api/v1/admin/market-data/datasets/{dataset_id}",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"

    body = response.json()

    assert body["dataset_id"] == dataset_id
    assert body["symbol"] == "BTC/USDT"
    assert body["version_algorithm"] == RAW_DATASET_VERSION_ALGORITHM
    assert body["integrity"] == {
        "present": True,
        "schema_version": 1,
        "checksum_algorithm": RAW_DATASET_VERSION_ALGORITHM,
        "partition_count": 1,
    }

    assert "location" not in response.text
    assert "relative_path" not in response.text
    assert "ADT_DATA_DIR" not in response.text
    assert SECRET_LOCATION not in response.text
    assert SECRET_PARTITION_NAME not in response.text

    assert api[1].get_calls == [_selector().canonical_key]


@pytest.mark.asyncio
async def test_list_filters_are_bounded_and_canonical(
    client: AsyncClient,
    api: tuple[FastAPI, FakeRawCatalog],
) -> None:
    response = await client.get(
        "/api/v1/admin/market-data/datasets",
        headers=AUTH_HEADERS,
        params={
            "symbol": "btc/usdt",
            "timeframe": "1h",
            "page": 1,
            "page_size": 10,
        },
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["symbol"] == "BTC/USDT"
    assert api[1].list_calls == 1


@pytest.mark.asyncio
async def test_oversized_page_is_rejected_before_catalog_access(
    client: AsyncClient,
    api: tuple[FastAPI, FakeRawCatalog],
) -> None:
    response = await client.get(
        "/api/v1/admin/market-data/datasets?page_size=101",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 422
    assert api[1].list_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code",
    [
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    ],
)
async def test_routes_require_admin_before_catalog_access(
    client: AsyncClient,
    api: tuple[FastAPI, FakeRawCatalog],
    status_code: int,
) -> None:
    async def reject_administrator() -> UUID:
        raise HTTPException(status_code=status_code)

    api[0].dependency_overrides[require_administrator] = reject_administrator

    list_response = await client.get("/api/v1/admin/market-data/datasets")
    detail_response = await client.get(
        f"/api/v1/admin/market-data/datasets/{encode_dataset_id(_selector())}"
    )

    assert list_response.status_code == status_code
    assert detail_response.status_code == status_code
    assert api[1].list_calls == 0
    assert api[1].get_calls == []


@pytest.mark.asyncio
async def test_malformed_dataset_id_is_rejected_before_catalog_lookup(
    client: AsyncClient,
    api: tuple[FastAPI, FakeRawCatalog],
) -> None:
    response = await client.get(
        "/api/v1/admin/market-data/datasets/abc",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_dataset_id"
    assert api[1].get_calls == []


@pytest.mark.asyncio
async def test_missing_dataset_returns_sanitized_404(
    client: AsyncClient,
    api: tuple[FastAPI, FakeRawCatalog],
) -> None:
    missing = _selector("ETH/USDT", "1h")

    response = await client.get(
        f"/api/v1/admin/market-data/datasets/{encode_dataset_id(missing)}",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "raw_dataset_not_found"
    assert api[1].get_calls == [missing.canonical_key]


@pytest.mark.asyncio
async def test_invalid_filter_is_rejected_without_catalog_access(
    client: AsyncClient,
    api: tuple[FastAPI, FakeRawCatalog],
) -> None:
    response = await client.get(
        "/api/v1/admin/market-data/datasets",
        headers=AUTH_HEADERS,
        params={"timeframe": "99x"},
    )

    assert response.status_code in {400, 422}
    assert api[1].list_calls == 0


def test_http_boundary_contains_only_get_routes() -> None:
    routes = {route.path: route for route in admin_market_datasets.router.routes}

    assert set(routes) == {
        "/api/v1/admin/market-data/datasets",
        "/api/v1/admin/market-data/datasets/{dataset_id}",
        "/api/v1/admin/market-data/datasets/{dataset_id}/gaps",
        "/api/v1/admin/market-data/datasets/{dataset_id}/quality",
    }

    assert all(route.methods == {"GET"} for route in routes.values())


def test_openapi_declares_bounded_read_only_contract(
    api: tuple[FastAPI, FakeRawCatalog],
) -> None:
    schema = api[0].openapi()
    paths = schema["paths"]

    list_operation = paths["/api/v1/admin/market-data/datasets"]["get"]

    detail_operation = paths["/api/v1/admin/market-data/datasets/{dataset_id}"]["get"]

    parameters = {item["name"]: item for item in list_operation["parameters"]}

    assert parameters["page"]["schema"]["default"] == 1
    assert parameters["page_size"]["schema"]["default"] == 25
    assert parameters["page_size"]["schema"]["maximum"] == 100

    assert "post" not in paths["/api/v1/admin/market-data/datasets"]
    assert "patch" not in paths["/api/v1/admin/market-data/datasets/{dataset_id}"]

    assert "200" in list_operation["responses"]
    assert "200" in detail_operation["responses"]

    components = schema["components"]["schemas"]
    dataset_properties = components["RawDatasetResponse"]["properties"]

    assert "location" not in dataset_properties
    assert "relative_path" not in dataset_properties
