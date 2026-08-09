"""Authenticated application contract tests for bounded local RAW candles."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException, status

from app.api.dependencies.auth import get_authenticated_user, require_administrator
from app.api.dependencies.resources import get_market_candle_read_service
from app.api.routes import app_market_candles
from app.main import app
from app.market_data.binance import BinanceSpotAdapter
from app.market_data.candle_query import LocalMarketCandleReadService
from tests.market_data_helpers import candle, utc
from tests.test_market_candle_query import ingest_cataloged_candles

NON_ADMIN_ID = UUID("22222222-2222-2222-2222-222222222222")
ADMIN_ID = UUID("11111111-1111-1111-1111-111111111111")
ROUTE = "/api/v1/app/market-data/candles/BTC/USDT"


@pytest_asyncio.fixture
async def app_market_candle_client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
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
@pytest.mark.parametrize("user_id", [NON_ADMIN_ID, ADMIN_ID])
async def test_app_market_candles_accepts_any_authenticated_user(
    app_market_candle_client: httpx.AsyncClient,
    user_id: UUID,
) -> None:
    app.dependency_overrides[get_authenticated_user] = lambda: user_id

    response = await app_market_candle_client.get(
        ROUTE,
        params={"timeframe": "1h", "limit": 2},
    )

    assert response.status_code == 200
    assert response.json()["symbol"] == "BTC/USDT"


@pytest.mark.asyncio
async def test_app_market_candles_rejects_missing_or_invalid_authentication(
    app_market_candle_client: httpx.AsyncClient,
) -> None:
    async def reject_authentication() -> UUID:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    app.dependency_overrides[get_authenticated_user] = reject_authentication

    response = await app_market_candle_client.get(
        ROUTE,
        params={"timeframe": "1h"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.asyncio
async def test_app_market_candles_uses_local_service_and_preserves_contract(
    app_market_candle_client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    service = LocalMarketCandleReadService(tmp_path)
    app.dependency_overrides[get_authenticated_user] = lambda: NON_ADMIN_ID
    app.dependency_overrides[get_market_candle_read_service] = lambda: service

    response = await app_market_candle_client.get(
        "/api/v1/app/market-data/candles/btc/usdt",
        params={"timeframe": "1h", "limit": 2},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["symbol"] == "BTC/USDT"
    assert payload["timeframe"] == "1h"
    assert payload["count"] == 2
    assert payload["dataset_candle_count"] == 4
    assert payload["available_start"] == "2024-01-01T00:00:00Z"
    assert payload["available_end"] == "2024-01-01T04:00:00Z"
    assert payload["next_before"] == "2024-01-01T02:00:00Z"
    assert payload["has_more_before"] is True
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
async def test_app_market_candles_accepts_maximum_limit_and_rejects_overflow(
    app_market_candle_client: httpx.AsyncClient,
) -> None:
    app.dependency_overrides[get_authenticated_user] = lambda: NON_ADMIN_ID

    accepted = await app_market_candle_client.get(
        ROUTE,
        params={"timeframe": "1h", "limit": 5000},
    )
    rejected = await app_market_candle_client.get(
        ROUTE,
        params={"timeframe": "1h", "limit": 5001},
    )

    assert accepted.status_code == 200
    assert accepted.json()["limit"] == 5000
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_app_market_candles_forwards_exclusive_before_cursor(
    app_market_candle_client: httpx.AsyncClient,
) -> None:
    app.dependency_overrides[get_authenticated_user] = lambda: NON_ADMIN_ID

    response = await app_market_candle_client.get(
        ROUTE,
        params={
            "timeframe": "1h",
            "before": "2024-01-01T02:00:00Z",
            "limit": 2,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["requested_before"] == "2024-01-01T02:00:00Z"
    assert [item["open_time"] for item in payload["items"]] == [
        "2024-01-01T00:00:00Z",
        "2024-01-01T01:00:00Z",
    ]


@pytest.mark.asyncio
async def test_app_market_candles_does_not_depend_on_administrator_authorization(
    app_market_candle_client: httpx.AsyncClient,
) -> None:
    async def fail_if_called() -> UUID:
        pytest.fail("require_administrator must not protect the application route")

    app.dependency_overrides[get_authenticated_user] = lambda: NON_ADMIN_ID
    app.dependency_overrides[require_administrator] = fail_if_called

    response = await app_market_candle_client.get(
        ROUTE,
        params={"timeframe": "1h"},
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_app_market_candles_never_fetches_from_binance(
    app_market_candle_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_network(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("the authenticated chart must only read local persisted candles")

    monkeypatch.setattr(BinanceSpotAdapter, "fetch_candles", fail_network)
    app.dependency_overrides[get_authenticated_user] = lambda: NON_ADMIN_ID

    response = await app_market_candle_client.get(
        ROUTE,
        params={"timeframe": "1h"},
    )

    assert response.status_code == 200


def test_app_market_candle_http_boundary_is_read_only_and_app_scoped() -> None:
    routes = {route.path: route for route in app_market_candles.router.routes}
    assert set(routes) == {"/api/v1/app/market-data/candles/{base_asset}/{quote_asset}"}
    assert all(route.methods == {"GET"} for route in routes.values())


def test_app_market_candle_openapi_is_bounded_authenticated_and_not_admin() -> None:
    contract = app.openapi()
    operation = contract["paths"]["/api/v1/app/market-data/candles/{base_asset}/{quote_asset}"][
        "get"
    ]
    assert {"400", "401", "404", "409", "422", "500", "503"}.issubset(operation["responses"])
    assert "403" not in operation["responses"]

    headers = operation["responses"]["200"]["headers"]
    assert {
        "Cache-Control",
        "X-ADT-Candle-Dataset-Version",
        "X-ADT-Candle-Content-Checksum",
        "X-ADT-Candle-Rows",
        "X-Request-ID",
    }.issubset(headers)

    parameters = {item["name"]: item for item in operation["parameters"]}
    assert parameters["limit"]["schema"]["default"] == 1000
    assert parameters["limit"]["schema"]["maximum"] == 5000
    assert parameters["timeframe"]["required"] is True

    candle_schema = contract["components"]["schemas"]["MarketCandleResponse"]["properties"]
    assert candle_schema["open"]["type"] == "string"
    assert candle_schema["volume"]["type"] == "string"
