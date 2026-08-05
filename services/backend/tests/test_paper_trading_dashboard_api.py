"""Authenticated HTTP boundary regressions for the paper dashboard."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException, status

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_paper_dashboard_read_service,
    get_paper_runner_state_store,
)
from app.api.routes import admin_paper_dashboard
from app.main import app
from app.paper_trading.dashboard import PaperDashboardReadService
from app.paper_trading.domain import paper_session_id
from app.paper_trading.repository import PaperTradingRepository
from tests.test_paper_trading import FakeSource, _candle, _config, _service
from tests.test_paper_trading_dashboard import _regime_policy, _runner_state


class FakeRunnerStore:
    def __init__(self, state: object | None) -> None:
        self._state = state

    def read(self) -> object | None:
        return self._state


@pytest_asyncio.fixture
async def dashboard_api_client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    candles = tuple(
        _candle(index, close) for index, close in enumerate(("100", "105", "110", "120"))
    )
    source = FakeSource(candles)
    service = _service(tmp_path, source)
    config = replace(
        _config(),
        market_regime_policy=_regime_policy(),
        schema_version=2,
    )
    session_id = paper_session_id(config)
    service.create(config)
    state = service.run_once(session_id).state
    runner = _runner_state(
        session_id,
        state_id=state.state_id,
        candles_processed=state.candles_processed,
        last_candle_open_time=state.last_candle_open_time,
    )
    dashboard = PaperDashboardReadService(PaperTradingRepository(tmp_path))

    app.dependency_overrides[get_paper_dashboard_read_service] = lambda: dashboard
    app.dependency_overrides[get_paper_runner_state_store] = lambda: FakeRunnerStore(runner)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_dashboard_api_requires_administrator(
    dashboard_api_client: httpx.AsyncClient,
) -> None:
    async def reject_administrator() -> UUID:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    app.dependency_overrides[require_administrator] = reject_administrator

    response = await dashboard_api_client.get("/api/v1/admin/paper-trading/dashboard")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.asyncio
async def test_dashboard_api_serializes_verified_read_model(
    dashboard_api_client: httpx.AsyncClient,
) -> None:
    app.dependency_overrides[require_administrator] = lambda: UUID(
        "11111111-1111-1111-1111-111111111111"
    )

    response = await dashboard_api_client.get(
        "/api/v1/admin/paper-trading/dashboard",
        params={"page": 1, "page_size": 20},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == 1
    assert payload["page_size"] == 20
    assert payload["total"] == 1
    assert payload["totals"]["scope"] == "page"
    assert payload["totals"]["configured_capital"] == "1000"
    assert payload["runner"]["cycle_index"] == 7
    assert payload["runner"]["status"] == "COMPLETED"

    item = payload["items"][0]
    assert item["symbol"] == "BTC/USDT"
    assert item["base_asset"] == "BTC"
    assert item["quote_asset"] == "USDT"
    assert item["metrics"]["equity"] == item["portfolio"]["equity"]
    assert isinstance(item["metrics"]["return_pct"], str)
    assert item["latest_market_regime"]["regime"] in {
        "warmup",
        "trend",
        "range",
        "volatile",
    }
    assert item["runner"]["matches_current_state"] is True


@pytest.mark.asyncio
async def test_dashboard_api_accepts_missing_runner_state(
    dashboard_api_client: httpx.AsyncClient,
) -> None:
    app.dependency_overrides[require_administrator] = lambda: UUID(
        "11111111-1111-1111-1111-111111111111"
    )
    app.dependency_overrides[get_paper_runner_state_store] = lambda: FakeRunnerStore(None)

    response = await dashboard_api_client.get("/api/v1/admin/paper-trading/dashboard")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runner"] is None
    assert payload["items"][0]["runner"] is None


@pytest.mark.asyncio
async def test_dashboard_api_rejects_unbounded_pagination(
    dashboard_api_client: httpx.AsyncClient,
) -> None:
    app.dependency_overrides[require_administrator] = lambda: UUID(
        "11111111-1111-1111-1111-111111111111"
    )

    response = await dashboard_api_client.get(
        "/api/v1/admin/paper-trading/dashboard",
        params={"page_size": 101},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_dashboard_http_boundary_is_read_only_and_admin_scoped() -> None:
    routes = tuple(admin_paper_dashboard.router.routes)
    assert len(routes) == 1
    route = routes[0]
    assert route.path == "/api/v1/admin/paper-trading/dashboard"
    assert route.methods == {"GET"}


def test_dashboard_openapi_contract_preserves_domain_enums() -> None:
    contract = app.openapi()
    operation = contract["paths"]["/api/v1/admin/paper-trading/dashboard"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/PaperDashboardResponse"
    )

    schemas = contract["components"]["schemas"]
    assert schemas["MarketRegimeKind"]["enum"] == [
        "warmup",
        "trend",
        "range",
        "volatile",
    ]
    assert schemas["TrendDirection"]["enum"] == ["none", "up", "down"]
    assert schemas["PaperRunnerSessionStatus"]["enum"] == [
        "UPDATED",
        "NOOP",
        "FAILED",
    ]
    assert schemas["PaperRunnerCycleStatus"]["enum"] == [
        "COMPLETED",
        "PARTIALLY_FAILED",
        "FAILED",
    ]
