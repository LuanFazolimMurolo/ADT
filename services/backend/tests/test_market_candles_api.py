"""Authenticated HTTP contract tests for bounded local RAW candles."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException, status

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import get_market_candle_read_service
from app.api.routes import admin_market_candles
from app.main import app
from app.market_data.candle_query import LocalMarketCandleReadService
from tests.market_data_helpers import candle, utc
from tests.test_market_candle_query import ingest_cataloged_candles


@pytest_asyncio.fixture
async def market_candle_api_client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    opening = utc(2024, 1, 1)
    candles = tuple(candle(opening + timedelta(hours=index)) for index in range(4))
    await ingest_cataloged_candles(
        tmp_path,
        candles,
        start=opening,
        end=opening + timedelta(hours=4),
    )
    app.dependency_overrides[get_market_candle_read_service] = lambda: LocalMarketCandleReadService(
        tmp_path
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_market_candle_api_requires_administrator(
    market_candle_api_client: httpx.AsyncClient,
) -> None:
    async def reject_administrator() -> UUID:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    app.dependency_overrides[require_administrator] = reject_administrator

    response = await market_candle_api_client.get(
        "/api/v1/admin/market-data/candles/BTC/USDT",
        params={"timeframe": "1h", "limit": 2},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.asyncio
async def test_market_candle_api_serializes_bounded_decimal_page(
    market_candle_api_client: httpx.AsyncClient,
) -> None:
    app.dependency_overrides[require_administrator] = lambda: UUID(
        "11111111-1111-1111-1111-111111111111"
    )

    response = await market_candle_api_client.get(
        "/api/v1/admin/market-data/candles/btc/usdt",
        params={"timeframe": "1h", "limit": 2},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["symbol"] == "BTC/USDT"
    assert payload["timeframe"] == "1h"
    assert payload["count"] == 2
    assert payload["dataset_candle_count"] == 4
    assert payload["range_start"] == "2024-01-01T02:00:00Z"
    assert payload["range_end"] == "2024-01-01T04:00:00Z"
    assert payload["has_more_before"] is True
    assert payload["next_before"] == "2024-01-01T02:00:00Z"
    assert payload["items"][0]["open"] == "100.000000000000000000"
    assert payload["items"][0]["volume"] == "2.500000000000000000"
    assert isinstance(payload["items"][0]["open"], str)
    assert len(payload["dataset_version"]) == 64
    assert len(payload["content_checksum"]) == 64
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-adt-candle-dataset-version"] == payload["dataset_version"]
    assert response.headers["x-adt-candle-content-checksum"] == payload["content_checksum"]
    assert response.headers["x-adt-candle-rows"] == "2"


@pytest.mark.asyncio
async def test_market_candle_api_uses_exclusive_backward_cursor(
    market_candle_api_client: httpx.AsyncClient,
) -> None:
    app.dependency_overrides[require_administrator] = lambda: UUID(
        "11111111-1111-1111-1111-111111111111"
    )

    response = await market_candle_api_client.get(
        "/api/v1/admin/market-data/candles/BTC/USDT",
        params={
            "timeframe": "1h",
            "before": "2024-01-01T02:00:00Z",
            "limit": 2,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["open_time"] for item in payload["items"]] == [
        "2024-01-01T00:00:00Z",
        "2024-01-01T01:00:00Z",
    ]
    assert payload["has_more_before"] is False
    assert payload["next_before"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "expected_status", "expected_code"),
    [
        ({"timeframe": "2h", "limit": 2}, 400, "unsupported_timeframe"),
        ({"timeframe": "1h", "limit": 5001}, 422, "validation_error"),
        (
            {"timeframe": "1h", "before": "2024-01-01T00:30:00Z", "limit": 2},
            400,
            "invalid_data_range",
        ),
    ],
)
async def test_market_candle_api_rejects_invalid_bounded_queries(
    market_candle_api_client: httpx.AsyncClient,
    params: dict[str, str | int],
    expected_status: int,
    expected_code: str,
) -> None:
    app.dependency_overrides[require_administrator] = lambda: UUID(
        "11111111-1111-1111-1111-111111111111"
    )

    response = await market_candle_api_client.get(
        "/api/v1/admin/market-data/candles/BTC/USDT",
        params=params,
    )

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code


@pytest.mark.asyncio
async def test_market_candle_api_missing_dataset_uses_stable_404(
    market_candle_api_client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    app.dependency_overrides[require_administrator] = lambda: UUID(
        "11111111-1111-1111-1111-111111111111"
    )
    app.dependency_overrides[get_market_candle_read_service] = lambda: LocalMarketCandleReadService(
        tmp_path / "missing"
    )

    response = await market_candle_api_client.get(
        "/api/v1/admin/market-data/candles/ETH/USDT",
        params={"timeframe": "1h"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "market_candle_dataset_not_found"


def test_market_candle_http_boundary_is_read_only_and_admin_scoped() -> None:
    routes = {route.path: route for route in admin_market_candles.router.routes}
    assert set(routes) == {"/api/v1/admin/market-data/candles/{base_asset}/{quote_asset}"}
    assert all(route.methods == {"GET"} for route in routes.values())


def test_market_candle_openapi_contract_is_bounded_and_decimal_safe() -> None:
    contract = app.openapi()
    operation = contract["paths"]["/api/v1/admin/market-data/candles/{base_asset}/{quote_asset}"][
        "get"
    ]
    response = operation["responses"]["200"]
    headers = response["headers"]
    assert "X-Request-ID" in headers
    assert "X-ADT-Candle-Dataset-Version" in headers
    assert "X-ADT-Candle-Content-Checksum" in headers
    assert "X-ADT-Candle-Rows" in headers

    parameters = {item["name"]: item for item in operation["parameters"]}
    assert parameters["limit"]["schema"]["default"] == 1000
    assert parameters["limit"]["schema"]["maximum"] == 5000
    assert parameters["timeframe"]["required"] is True

    candle_schema = contract["components"]["schemas"]["MarketCandleResponse"]["properties"]
    assert candle_schema["open"]["type"] == "string"
    assert candle_schema["volume"]["type"] == "string"
