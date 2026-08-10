"""Authenticated HTTP boundary regressions for paper period metrics."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException, status

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import get_paper_period_metrics_service
from app.api.routes import admin_paper_period_metrics
from app.main import app
from app.paper_trading.repository import PaperTradingRepository
from tests.test_paper_trading_journal_query import _persist_resigned_state
from tests.test_paper_trading_period_metrics import (
    _period_service,
    _populated_period_repository,
)


@pytest_asyncio.fixture
async def period_metrics_api_client(
    tmp_path: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    repository, session_id, _ = _populated_period_repository(tmp_path)
    del repository
    service = _period_service(tmp_path)
    app.dependency_overrides[get_paper_period_metrics_service] = lambda: service

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client, session_id

    app.dependency_overrides.clear()


def _params(session_id: str) -> dict[str, str]:
    return {
        "quote_asset": "usdt",
        "period_from": "2026-08-01T00:00:00Z",
        "period_before": "2026-08-04T00:00:00Z",
        "session_id": session_id,
        "base_asset": "btc",
        "timeframe": "1m",
        "strategy_name": "paper-journal-test",
        "strategy_version": "1",
    }


@pytest.mark.asyncio
async def test_period_metrics_api_requires_administrator(
    period_metrics_api_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, session_id = period_metrics_api_client

    async def reject_administrator() -> UUID:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    app.dependency_overrides[require_administrator] = reject_administrator

    response = await client.get(
        "/api/v1/admin/paper-trading/period-metrics",
        params=_params(session_id),
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.asyncio
async def test_period_metrics_api_serializes_verified_daily_series(
    period_metrics_api_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, session_id = period_metrics_api_client
    app.dependency_overrides[require_administrator] = lambda: UUID(
        "11111111-1111-1111-1111-111111111111"
    )

    response = await client.get(
        "/api/v1/admin/paper-trading/period-metrics",
        params={**_params(session_id), "granularity": "DAILY"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["granularity"] == "DAILY"
    assert payload["filters"]["quote_asset"] == "USDT"
    assert payload["filters"]["base_asset"] == "BTC"
    assert payload["filters"]["session_id"] == session_id
    assert len(payload["source_states"]) == 1
    assert payload["source_states"][0]["state_id"]
    assert len(payload["items"]) == 3
    assert [item["realizations_count"] for item in payload["items"]] == [1, 1, 0]
    assert payload["items"][0]["period_start"].startswith("2026-08-01T00:00:00")
    assert payload["items"][2]["realized_pnl"] == "0"
    assert payload["items"][2]["win_rate_pct"] is None
    assert payload["items"][2]["profit_factor"] is None
    assert payload["totals"]["periods_count"] == 3
    assert payload["totals"]["active_periods_count"] == 2
    assert payload["totals"]["realizations_count"] == 2
    assert isinstance(payload["totals"]["realized_pnl"], str)
    assert len(payload["query_checksum"]) == 64
    assert len(payload["content_checksum"]) == 64
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-adt-period-metrics-query-checksum"] == payload["query_checksum"]
    assert response.headers["x-adt-period-metrics-content-checksum"] == payload["content_checksum"]


@pytest.mark.asyncio
async def test_period_metrics_api_requires_bounded_query_parameters(
    period_metrics_api_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = period_metrics_api_client
    app.dependency_overrides[require_administrator] = lambda: UUID(
        "11111111-1111-1111-1111-111111111111"
    )

    response = await client.get("/api/v1/admin/paper-trading/period-metrics")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_admin_period_metrics_reject_resigned_source_state_with_safe_conflict(
    period_metrics_api_client: tuple[httpx.AsyncClient, str],
    tmp_path: Path,
) -> None:
    client, session_id = period_metrics_api_client
    _persist_resigned_state(
        tmp_path,
        PaperTradingRepository(tmp_path),
        session_id,
        source_checksum="d" * 64,
    )
    app.dependency_overrides[require_administrator] = lambda: UUID(int=1)

    response = await client.get(
        "/api/v1/admin/paper-trading/period-metrics",
        params={**_params(session_id), "granularity": "DAILY"},
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["error"] == {
        "code": "paper_session_verification_failed",
        "message": "A sessão de paper trading não pôde ser verificada.",
    }


def test_period_metrics_http_boundary_is_read_only_and_admin_scoped() -> None:
    routes = {route.path: route for route in admin_paper_period_metrics.router.routes}
    assert set(routes) == {"/api/v1/admin/paper-trading/period-metrics"}
    assert all(route.methods == {"GET"} for route in routes.values())


def test_period_metrics_openapi_contract_preserves_granularity_enum() -> None:
    contract = app.openapi()
    operation = contract["paths"]["/api/v1/admin/paper-trading/period-metrics"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/PaperPeriodMetricsSeriesResponse"
    )

    headers = operation["responses"]["200"]["headers"]
    assert "X-Request-ID" in headers
    assert "X-ADT-Period-Metrics-Query-Checksum" in headers
    assert "X-ADT-Period-Metrics-Content-Checksum" in headers

    schemas = contract["components"]["schemas"]
    assert schemas["PaperPeriodGranularity"]["enum"] == [
        "DAILY",
        "WEEKLY",
        "MONTHLY",
    ]
