"""Authorized app paper-session performance HTTP tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException, status

from app.api.dependencies.auth import (
    require_administrator,
    require_app_paper_session_reader,
)
from app.api.dependencies.resources import (
    get_paper_period_metrics_service,
    get_paper_portfolio_timeline_read_service,
    get_paper_trading_read_service,
)
from app.api.routes import app_paper_session_performance
from app.main import app
from app.paper_trading.period_metrics import (
    PaperPeriodGranularity,
    PaperPeriodMetricsFilter,
    PaperPeriodMetricsSeries,
    PaperPeriodMetricsService,
)
from app.paper_trading.portfolio_timeline_artifacts import (
    PaperPortfolioTimelineArtifactStore,
)
from app.paper_trading.portfolio_timeline_query import (
    PAPER_PORTFOLIO_TIMELINE_DEFAULT_LIMIT,
    PaperPortfolioTimelinePage,
    PaperPortfolioTimelinePageQuery,
    PaperPortfolioTimelineReadService,
)
from app.paper_trading.query import PaperSessionView, PaperTradingReadService
from app.paper_trading.repository import PaperTradingRepository
from tests.test_paper_trading_period_metrics import _populated_period_repository
from tests.test_paper_trading_portfolio_timeline_service import _running_session

MISSING_SESSION_ID = "f" * 64
PERIOD_PARAMS = {
    "period_from": "2026-08-01T00:00:00Z",
    "period_before": "2026-08-04T00:00:00Z",
    "granularity": "DAILY",
}
FORBIDDEN_FIELDS = {
    "config_checksum",
    "state_id",
    "state_replayed_at",
    "source_checksum",
    "source_states",
    "strategy_parameters",
    "sessions_count",
    "symbols_count",
}


class RecordingTimelineService:
    def __init__(self, delegate: PaperPortfolioTimelineReadService) -> None:
        self.delegate = delegate
        self.queries: list[PaperPortfolioTimelinePageQuery] = []

    def read_page(self, query: PaperPortfolioTimelinePageQuery) -> PaperPortfolioTimelinePage:
        self.queries.append(query)
        return self.delegate.read_page(query)


class RecordingSessionService:
    def __init__(self, delegate: PaperTradingReadService) -> None:
        self.delegate = delegate
        self.session_ids: list[str] = []

    def get_session(self, session_id: str) -> PaperSessionView:
        self.session_ids.append(session_id)
        return self.delegate.get_session(session_id)


class RecordingPeriodService:
    def __init__(self, delegate: PaperPeriodMetricsService) -> None:
        self.delegate = delegate
        self.calls: list[tuple[PaperPeriodMetricsFilter, PaperPeriodGranularity]] = []

    def build_series(
        self,
        filters: PaperPeriodMetricsFilter,
        *,
        granularity: PaperPeriodGranularity,
    ) -> PaperPeriodMetricsSeries:
        self.calls.append((filters, granularity))
        return self.delegate.build_series(filters, granularity=granularity)


@pytest_asyncio.fixture
async def timeline_client(
    tmp_path: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, str, RecordingTimelineService]]:
    _, paper_service, _, session_id = _running_session(tmp_path)
    paper_service.run_once(session_id)
    service = RecordingTimelineService(
        PaperPortfolioTimelineReadService(
            PaperTradingRepository(tmp_path),
            PaperPortfolioTimelineArtifactStore(tmp_path),
        )
    )
    app.dependency_overrides[require_app_paper_session_reader] = lambda: UUID(int=1)
    app.dependency_overrides[get_paper_portfolio_timeline_read_service] = lambda: service

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client, session_id, service

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def period_client(
    tmp_path: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, str, RecordingSessionService, RecordingPeriodService]]:
    repository, session_id, _ = _populated_period_repository(tmp_path)
    session_service = RecordingSessionService(PaperTradingReadService(repository))
    period_service = RecordingPeriodService(PaperPeriodMetricsService(repository))
    app.dependency_overrides[require_app_paper_session_reader] = lambda: UUID(int=1)
    app.dependency_overrides[get_paper_trading_read_service] = lambda: session_service
    app.dependency_overrides[get_paper_period_metrics_service] = lambda: period_service

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client, session_id, session_service, period_service

    app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suffix", "params"),
    [
        ("/portfolio-timeline", {}),
        ("/period-metrics", PERIOD_PARAMS),
    ],
)
async def test_non_admin_is_denied_before_all_performance_reads_for_existing_and_missing(
    suffix: str,
    params: dict[str, str],
) -> None:
    async def reject_reader() -> UUID:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    def forbidden_service() -> None:
        pytest.fail("performance service resolved before project-owner authorization")

    app.dependency_overrides[require_app_paper_session_reader] = reject_reader
    app.dependency_overrides[get_paper_trading_read_service] = forbidden_service
    app.dependency_overrides[get_paper_portfolio_timeline_read_service] = forbidden_service
    app.dependency_overrides[get_paper_period_metrics_service] = forbidden_service

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            existing = await client.get(
                f"/api/v1/app/paper-trading/sessions/{'a' * 64}{suffix}",
                params=params,
            )
            missing = await client.get(
                f"/api/v1/app/paper-trading/sessions/{MISSING_SESSION_ID}{suffix}",
                params=params,
            )
    finally:
        app.dependency_overrides.clear()

    assert existing.status_code == status.HTTP_403_FORBIDDEN
    assert missing.status_code == status.HTTP_403_FORBIDDEN
    assert existing.json() == missing.json()


@pytest.mark.asyncio
async def test_project_owner_timeline_uses_persisted_page_with_minimal_schema_and_headers(
    timeline_client: tuple[httpx.AsyncClient, str, RecordingTimelineService],
) -> None:
    client, session_id, service = timeline_client

    response = await client.get(
        f"/api/v1/app/paper-trading/sessions/{session_id}/portfolio-timeline",
        params={"limit": 5000},
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert service.queries[-1].session_id == session_id
    assert service.queries[-1].limit == 5000
    assert payload["session_id"] == session_id
    assert payload["count"] == 4
    assert payload["count"] == len(payload["items"])
    assert isinstance(payload["items"][0]["equity"], str)
    assert isinstance(payload["items"][0]["drawdown_pct"], str)
    assert FORBIDDEN_FIELDS.isdisjoint(payload)
    assert all(FORBIDDEN_FIELDS.isdisjoint(item) for item in payload["items"])
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-adt-paper-timeline-id"] == payload["timeline_id"]
    assert response.headers["x-adt-paper-timeline-state-checksum"] == payload["state_checksum"]
    assert response.headers["x-adt-paper-timeline-content-checksum"] == payload["content_checksum"]
    assert response.headers["x-adt-paper-timeline-rows"] == str(payload["count"])


@pytest.mark.asyncio
async def test_timeline_preserves_default_cursor_and_limit_bounds(
    timeline_client: tuple[httpx.AsyncClient, str, RecordingTimelineService],
) -> None:
    client, session_id, service = timeline_client
    path = f"/api/v1/app/paper-trading/sessions/{session_id}/portfolio-timeline"

    initial = await client.get(path, params={"limit": 2})
    before = initial.json()["next_before"]
    older = await client.get(path, params={"before": before})
    invalid = await client.get(path, params={"limit": 5001})

    assert initial.status_code == status.HTTP_200_OK
    assert older.status_code == status.HTTP_200_OK
    assert invalid.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert service.queries[-1].limit == PAPER_PORTFOLIO_TIMELINE_DEFAULT_LIMIT
    assert service.queries[-1].before == datetime.fromisoformat(before)


@pytest.mark.asyncio
async def test_authorized_missing_timeline_session_returns_404(
    timeline_client: tuple[httpx.AsyncClient, str, RecordingTimelineService],
) -> None:
    client, _, _ = timeline_client

    response = await client.get(
        f"/api/v1/app/paper-trading/sessions/{MISSING_SESSION_ID}/portfolio-timeline"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_period_metrics_force_path_session_and_authoritative_quote_asset(
    period_client: tuple[
        httpx.AsyncClient,
        str,
        RecordingSessionService,
        RecordingPeriodService,
    ],
) -> None:
    client, session_id, session_service, period_service = period_client

    response = await client.get(
        f"/api/v1/app/paper-trading/sessions/{session_id}/period-metrics",
        params={
            **PERIOD_PARAMS,
            "quote_asset": "BTC",
            "session_id": MISSING_SESSION_ID,
            "base_asset": "ETH",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    filters, granularity = period_service.calls[-1]
    assert session_service.session_ids == [session_id]
    assert filters.session_id == session_id
    assert filters.quote_asset == "USDT"
    assert filters.base_asset is None
    assert granularity is PaperPeriodGranularity.DAILY
    assert payload["session_id"] == session_id
    assert payload["quote_asset"] == "USDT"
    assert payload["period_from"].startswith("2026-08-01T00:00:00")
    assert payload["period_before"].startswith("2026-08-04T00:00:00")
    assert [item["realizations_count"] for item in payload["items"]] == [1, 1, 0]
    assert payload["totals"]["realizations_count"] == 2
    assert isinstance(payload["totals"]["realized_pnl"], str)
    assert FORBIDDEN_FIELDS.isdisjoint(payload)
    assert FORBIDDEN_FIELDS.isdisjoint(payload["totals"])
    assert "source_states" not in response.text
    assert "strategy_parameters" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-adt-period-metrics-query-checksum"] == payload["query_checksum"]
    assert response.headers["x-adt-period-metrics-content-checksum"] == payload["content_checksum"]


@pytest.mark.asyncio
async def test_period_metrics_propagate_range_granularity_and_missing_session(
    period_client: tuple[
        httpx.AsyncClient,
        str,
        RecordingSessionService,
        RecordingPeriodService,
    ],
) -> None:
    client, session_id, _, period_service = period_client
    path = f"/api/v1/app/paper-trading/sessions/{session_id}/period-metrics"

    monthly = await client.get(
        path,
        params={
            "period_from": "2026-08-01T00:00:00Z",
            "period_before": "2026-09-01T00:00:00Z",
            "granularity": "MONTHLY",
        },
    )
    missing = await client.get(
        f"/api/v1/app/paper-trading/sessions/{MISSING_SESSION_ID}/period-metrics",
        params=PERIOD_PARAMS,
    )

    assert monthly.status_code == status.HTTP_200_OK
    filters, granularity = period_service.calls[-1]
    assert filters.period_from == datetime.fromisoformat("2026-08-01T00:00:00+00:00")
    assert filters.period_before == datetime.fromisoformat("2026-09-01T00:00:00+00:00")
    assert granularity is PaperPeriodGranularity.MONTHLY
    assert missing.status_code == status.HTTP_404_NOT_FOUND


def test_app_performance_boundary_is_get_only_without_cross_session_or_export() -> None:
    routes = {route.path: route for route in app_paper_session_performance.router.routes}
    assert set(routes) == {
        "/api/v1/app/paper-trading/sessions/{session_id}/portfolio-timeline",
        "/api/v1/app/paper-trading/sessions/{session_id}/period-metrics",
    }
    assert all(route.methods == {"GET"} for route in routes.values())
    contract = app.openapi()
    period_parameters = contract["paths"][
        "/api/v1/app/paper-trading/sessions/{session_id}/period-metrics"
    ]["get"]["parameters"]
    names = {item["name"] for item in period_parameters}
    assert names == {
        "session_id",
        "period_from",
        "period_before",
        "granularity",
    }
    assert all(
        "export" not in path for path in contract["paths"] if path.startswith("/api/v1/app/")
    )


@pytest.mark.asyncio
async def test_original_admin_performance_routes_remain_admin_only(
    timeline_client: tuple[httpx.AsyncClient, str, RecordingTimelineService],
) -> None:
    client, session_id, _ = timeline_client

    async def reject_administrator() -> UUID:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    app.dependency_overrides[require_administrator] = reject_administrator
    try:
        timeline = await client.get(
            f"/api/v1/admin/paper-trading/sessions/{session_id}/portfolio-timeline"
        )
        period = await client.get(
            "/api/v1/admin/paper-trading/period-metrics",
            params={"quote_asset": "USDT", **PERIOD_PARAMS},
        )
    finally:
        app.dependency_overrides.pop(require_administrator, None)

    assert timeline.status_code == status.HTTP_401_UNAUTHORIZED
    assert period.status_code == status.HTTP_401_UNAUTHORIZED
