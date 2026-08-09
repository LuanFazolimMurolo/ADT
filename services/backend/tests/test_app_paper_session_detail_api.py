"""Authorized app paper-session detail, chart, and trade HTTP tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException, status

from app.api.dependencies.auth import require_app_paper_session_reader
from app.api.dependencies.resources import (
    get_paper_chart_annotation_read_service,
    get_paper_trade_journal_read_service,
    get_paper_trading_read_service,
)
from app.api.routes import app_paper_session_detail
from app.main import app
from app.paper_trading.chart_annotations import (
    PaperChartAnnotationPage,
    PaperChartAnnotationQuery,
    PaperChartAnnotationReadService,
)
from app.paper_trading.journal_query import (
    PaperTradeJournalFilter,
    PaperTradeJournalReadService,
    PaperTradePage,
)
from app.paper_trading.query import PaperTradingReadService
from tests.test_paper_trading_journal_query import _populated_repository

MISSING_SESSION_ID = "f" * 64
FORBIDDEN_FIELDS = {
    "strategy_parameters",
    "risk_limits",
    "execution",
    "portfolio",
    "equity",
    "config_checksum",
    "state_id",
    "state_checksum",
    "replayed_at",
    "client_tag",
}


class RecordingAnnotationService:
    def __init__(self, delegate: PaperChartAnnotationReadService) -> None:
        self.delegate = delegate
        self.queries: list[PaperChartAnnotationQuery] = []

    def read_page(self, query: PaperChartAnnotationQuery) -> PaperChartAnnotationPage:
        self.queries.append(query)
        return self.delegate.read_page(query)


class RecordingJournalService:
    def __init__(self, delegate: PaperTradeJournalReadService) -> None:
        self.delegate = delegate
        self.calls: list[tuple[PaperTradeJournalFilter, int, int]] = []

    def list_trades(
        self,
        filters: PaperTradeJournalFilter,
        *,
        page: int,
        page_size: int,
    ) -> PaperTradePage:
        self.calls.append((filters, page, page_size))
        return self.delegate.list_trades(filters, page=page, page_size=page_size)


@pytest_asyncio.fixture
async def authorized_session_client(
    tmp_path: Path,
) -> AsyncIterator[
    tuple[
        httpx.AsyncClient,
        str,
        RecordingAnnotationService,
        RecordingJournalService,
    ]
]:
    repository, session_id = _populated_repository(tmp_path)
    annotations = RecordingAnnotationService(PaperChartAnnotationReadService(repository))
    journal = RecordingJournalService(PaperTradeJournalReadService(repository))
    app.dependency_overrides[require_app_paper_session_reader] = lambda: UUID(int=1)
    app.dependency_overrides[get_paper_trading_read_service] = lambda: PaperTradingReadService(
        repository
    )
    app.dependency_overrides[get_paper_chart_annotation_read_service] = lambda: annotations
    app.dependency_overrides[get_paper_trade_journal_read_service] = lambda: journal

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client, session_id, annotations, journal

    app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suffix", "params"),
    [
        ("", {}),
        (
            "/chart-annotations",
            {
                "start": "2026-01-01T00:00:00Z",
                "before": "2026-01-01T01:00:00Z",
                "limit": 100,
            },
        ),
        ("/trades", {"page": 1, "page_size": 20}),
    ],
)
async def test_non_admin_is_denied_before_any_session_read_for_existing_and_missing_ids(
    suffix: str,
    params: dict[str, object],
) -> None:
    async def reject_reader() -> UUID:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    def forbidden_service() -> None:
        pytest.fail("session service resolved before project-owner authorization")

    app.dependency_overrides[require_app_paper_session_reader] = reject_reader
    app.dependency_overrides[get_paper_trading_read_service] = forbidden_service
    app.dependency_overrides[get_paper_chart_annotation_read_service] = forbidden_service
    app.dependency_overrides[get_paper_trade_journal_read_service] = forbidden_service

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
async def test_project_owner_detail_is_minimal_and_missing_session_is_404(
    authorized_session_client: tuple[
        httpx.AsyncClient,
        str,
        RecordingAnnotationService,
        RecordingJournalService,
    ],
) -> None:
    client, session_id, _, _ = authorized_session_client

    existing = await client.get(f"/api/v1/app/paper-trading/sessions/{session_id}")
    missing = await client.get(f"/api/v1/app/paper-trading/sessions/{MISSING_SESSION_ID}")

    assert existing.status_code == status.HTTP_200_OK
    assert missing.status_code == status.HTTP_404_NOT_FOUND
    assert set(existing.json()) == {
        "session_id",
        "base_asset",
        "quote_asset",
        "timeframe",
        "strategy_name",
        "strategy_version",
        "state_available",
        "last_candle_open_time",
    }
    assert existing.json()["session_id"] == session_id
    assert existing.json()["state_available"] is True
    assert FORBIDDEN_FIELDS.isdisjoint(existing.json())


@pytest.mark.asyncio
async def test_detail_rejects_noncanonical_session_id(
    authorized_session_client: tuple[
        httpx.AsyncClient,
        str,
        RecordingAnnotationService,
        RecordingJournalService,
    ],
) -> None:
    client, _, _, _ = authorized_session_client

    response = await client.get("/api/v1/app/paper-trading/sessions/not-a-session")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_project_owner_annotations_preserve_bounds_and_minimal_provenance(
    authorized_session_client: tuple[
        httpx.AsyncClient,
        str,
        RecordingAnnotationService,
        RecordingJournalService,
    ],
) -> None:
    client, session_id, annotations, _ = authorized_session_client
    detail = await client.get(f"/api/v1/app/paper-trading/sessions/{session_id}")
    before = detail.json()["last_candle_open_time"]
    assert isinstance(before, str)
    start = "2026-01-01T00:00:00Z"
    before_exclusive = (
        datetime.fromisoformat(before).astimezone(UTC) + timedelta(minutes=1)
    ).isoformat()

    response = await client.get(
        f"/api/v1/app/paper-trading/sessions/{session_id}/chart-annotations",
        params={"start": start, "before": before_exclusive, "limit": 5000},
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert annotations.queries[-1].session_id == session_id
    assert annotations.queries[-1].limit == 5000
    assert payload["session_id"] == session_id
    assert payload["dataset_version"] is not None
    assert payload["count"] == payload["orders_count"] + payload["fills_count"]
    assert payload["fills_count"] > 0
    assert payload["content_checksum"]
    assert FORBIDDEN_FIELDS.isdisjoint(payload)
    assert "client_tag" not in response.text
    assert "strategy_parameters" not in response.text

    invalid = await client.get(
        f"/api/v1/app/paper-trading/sessions/{session_id}/chart-annotations",
        params={"start": start, "before": before_exclusive, "limit": 5001},
    )
    assert invalid.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_project_owner_trades_are_path_scoped_and_preserve_decimal_totals(
    authorized_session_client: tuple[
        httpx.AsyncClient,
        str,
        RecordingAnnotationService,
        RecordingJournalService,
    ],
) -> None:
    client, session_id, _, journal = authorized_session_client

    response = await client.get(
        f"/api/v1/app/paper-trading/sessions/{session_id}/trades",
        params={"status": "CLOSED", "page": 1, "page_size": 100},
    )

    assert response.status_code == status.HTTP_200_OK
    filters, page, page_size = journal.calls[-1]
    assert filters.session_id == session_id
    assert filters.base_asset is None
    assert page == 1
    assert page_size == 100
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["status"] == "CLOSED"
    assert isinstance(payload["items"][0]["net_pnl"], str)
    assert isinstance(payload["totals"]["total_net_pnl"], str)
    assert FORBIDDEN_FIELDS.isdisjoint(payload["items"][0])
    assert "client_tag" not in response.text
    assert "strategy_parameters" not in response.text

    invalid = await client.get(
        f"/api/v1/app/paper-trading/sessions/{session_id}/trades",
        params={"page_size": 101},
    )
    assert invalid.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_c3_router_is_get_only_without_export_or_cross_session_query() -> None:
    routes = {route.path: route for route in app_paper_session_detail.router.routes}
    assert set(routes) == {
        "/api/v1/app/paper-trading/sessions/{session_id}",
        "/api/v1/app/paper-trading/sessions/{session_id}/chart-annotations",
        "/api/v1/app/paper-trading/sessions/{session_id}/trades",
    }
    assert all(route.methods == {"GET"} for route in routes.values())
    assert all("export" not in path for path in routes)

    contract = app.openapi()
    trades = contract["paths"]["/api/v1/app/paper-trading/sessions/{session_id}/trades"]["get"]
    parameters = {(item["in"], item["name"]) for item in trades["parameters"]}
    assert ("path", "session_id") in parameters
    assert ("query", "session_id") not in parameters
    assert {"401", "403", "404", "409", "422", "500", "503"}.issubset(trades["responses"])
