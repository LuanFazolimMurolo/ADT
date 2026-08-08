"""Authenticated HTTP regressions for persisted paper portfolio timelines."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException, status

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_paper_portfolio_timeline_read_service,
)
from app.api.routes import admin_paper_portfolio_timeline
from app.main import app
from app.paper_trading.portfolio_timeline_artifacts import (
    PaperPortfolioTimelineArtifactStore,
)
from app.paper_trading.portfolio_timeline_query import (
    PaperPortfolioTimelineReadService,
)
from app.paper_trading.repository import PaperTradingRepository
from tests.test_paper_trading_portfolio_timeline_service import _running_session


@pytest_asyncio.fixture
async def portfolio_timeline_api_client(
    tmp_path: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, str, Path]]:
    _, paper_service, _, session_id = _running_session(tmp_path)
    paper_service.run_once(session_id)

    service = PaperPortfolioTimelineReadService(
        PaperTradingRepository(tmp_path),
        PaperPortfolioTimelineArtifactStore(tmp_path),
    )
    app.dependency_overrides[get_paper_portfolio_timeline_read_service] = lambda: service

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client, session_id, tmp_path

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_portfolio_timeline_api_requires_administrator(
    portfolio_timeline_api_client: tuple[httpx.AsyncClient, str, Path],
) -> None:
    client, session_id, _ = portfolio_timeline_api_client

    async def reject_administrator() -> UUID:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    app.dependency_overrides[require_administrator] = reject_administrator

    response = await client.get(
        f"/api/v1/admin/paper-trading/sessions/{session_id}/portfolio-timeline"
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.asyncio
async def test_portfolio_timeline_api_serializes_verified_backward_page(
    portfolio_timeline_api_client: tuple[httpx.AsyncClient, str, Path],
) -> None:
    client, session_id, _ = portfolio_timeline_api_client
    app.dependency_overrides[require_administrator] = lambda: UUID(
        "11111111-1111-1111-1111-111111111111"
    )

    response = await client.get(
        f"/api/v1/admin/paper-trading/sessions/{session_id}/portfolio-timeline",
        params={"limit": "2"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["session_id"] == session_id
    assert payload["symbol"] == "BTC/USDT"
    assert payload["base_asset"] == "BTC"
    assert payload["quote_asset"] == "USDT"
    assert payload["timeframe"] == "1m"
    assert payload["count"] == 2
    assert payload["total_observations"] == 4
    assert payload["has_more_before"] is True
    assert payload["next_before"] == payload["range_start"]
    assert [item["candle_index"] for item in payload["items"]] == [2, 3]
    assert isinstance(payload["items"][0]["equity"], str)
    assert isinstance(payload["items"][0]["drawdown_pct"], str)
    assert len(payload["timeline_id"]) == 64
    assert len(payload["timeline_content_checksum"]) == 64
    assert len(payload["content_checksum"]) == 64
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-adt-paper-timeline-id"] == payload["timeline_id"]
    assert response.headers["x-adt-paper-timeline-state-checksum"] == payload["state_checksum"]
    assert response.headers["x-adt-paper-timeline-content-checksum"] == payload["content_checksum"]
    assert response.headers["x-adt-paper-timeline-rows"] == "2"


@pytest.mark.asyncio
async def test_portfolio_timeline_api_returns_404_when_reference_is_missing(
    portfolio_timeline_api_client: tuple[httpx.AsyncClient, str, Path],
) -> None:
    client, session_id, tmp_path = portfolio_timeline_api_client
    app.dependency_overrides[require_administrator] = lambda: UUID(
        "11111111-1111-1111-1111-111111111111"
    )

    repository = PaperTradingRepository(tmp_path)
    state = repository.load_state(session_id)
    assert state is not None
    reference = (
        tmp_path
        / "market"
        / "paper-trading"
        / session_id
        / "portfolio-timeline-refs"
        / f"{state.checksum}.json"
    )
    reference.unlink()

    response = await client.get(
        f"/api/v1/admin/paper-trading/sessions/{session_id}/portfolio-timeline"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "paper_portfolio_timeline_not_found"


def test_portfolio_timeline_http_boundary_is_read_only_and_admin_scoped() -> None:
    routes = {route.path: route for route in admin_paper_portfolio_timeline.router.routes}
    assert set(routes) == {"/api/v1/admin/paper-trading/sessions/{session_id}/portfolio-timeline"}
    assert all(route.methods == {"GET"} for route in routes.values())


def test_portfolio_timeline_openapi_contract_exposes_integrity_headers() -> None:
    contract = app.openapi()
    operation = contract["paths"][
        "/api/v1/admin/paper-trading/sessions/{session_id}/portfolio-timeline"
    ]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/PaperPortfolioTimelinePageResponse"
    )

    headers = operation["responses"]["200"]["headers"]
    assert "X-Request-ID" in headers
    assert "X-ADT-Paper-Timeline-ID" in headers
    assert "X-ADT-Paper-Timeline-State-Checksum" in headers
    assert "X-ADT-Paper-Timeline-Content-Checksum" in headers
    assert "X-ADT-Paper-Timeline-Rows" in headers
