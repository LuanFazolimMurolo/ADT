"""Authenticated HTTP boundary regressions for the deterministic trade journal."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException, status

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_paper_trade_journal_export_service,
    get_paper_trade_journal_read_service,
)
from app.api.routes import admin_paper_journal
from app.main import app
from app.paper_trading.journal_export import PaperTradeJournalExportService
from app.paper_trading.journal_query import PaperTradeJournalReadService
from app.paper_trading.repository import PaperTradingRepository
from tests.test_paper_trading_journal_query import (
    _persist_resigned_state,
    _populated_repository,
    _state_verifier,
)


@pytest_asyncio.fixture
async def journal_api_client(
    tmp_path: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    repository, session_id = _populated_repository(tmp_path)
    verifier = _state_verifier(tmp_path)
    reader = PaperTradeJournalReadService(repository, verifier)
    exporter = PaperTradeJournalExportService(repository, verifier)

    app.dependency_overrides[get_paper_trade_journal_read_service] = lambda: reader
    app.dependency_overrides[get_paper_trade_journal_export_service] = lambda: exporter

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client, session_id

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_journal_api_requires_administrator(
    journal_api_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = journal_api_client

    async def reject_administrator() -> UUID:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    app.dependency_overrides[require_administrator] = reject_administrator

    response = await client.get("/api/v1/admin/paper-trading/journal")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.asyncio
async def test_journal_api_serializes_verified_newest_first_page(
    journal_api_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, session_id = journal_api_client
    app.dependency_overrides[require_administrator] = lambda: UUID(
        "11111111-1111-1111-1111-111111111111"
    )

    response = await client.get(
        "/api/v1/admin/paper-trading/journal",
        params={"session_id": session_id, "page": 1, "page_size": 20},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["filters"]["session_id"] == session_id
    assert payload["page"] == 1
    assert payload["page_size"] == 20
    assert payload["total"] == 2
    assert payload["total_pages"] == 1
    assert payload["totals"]["trades_count"] == 2
    assert payload["totals"]["closed_trades_count"] == 1
    assert payload["totals"]["open_trades_count"] == 1
    assert isinstance(payload["totals"]["total_net_pnl"], str)

    newest, oldest = payload["items"]
    assert newest["trade"]["status"] == "OPEN"
    assert oldest["trade"]["status"] == "CLOSED"
    assert newest["trade"]["opened_at"] > oldest["trade"]["opened_at"]
    assert newest["symbol"] == "BTC/USDT"
    assert newest["trade"]["opened_quantity"] == "0.5"
    assert newest["trade"]["entry_executions"][0]["client_tag"] == "entry-b"
    assert oldest["trade"]["entry_executions"][0]["client_tag"] == "entry-a"
    assert oldest["trade"]["exit_executions"][0]["client_tag"] == "close-a"


@pytest.mark.asyncio
async def test_journal_api_applies_status_filter_and_pagination(
    journal_api_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, session_id = journal_api_client
    app.dependency_overrides[require_administrator] = lambda: UUID(
        "11111111-1111-1111-1111-111111111111"
    )

    response = await client.get(
        "/api/v1/admin/paper-trading/journal",
        params={
            "session_id": session_id,
            "status": "CLOSED",
            "page": 1,
            "page_size": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["trade"]["status"] == "CLOSED"


@pytest.mark.asyncio
async def test_journal_api_exports_csv_and_jsonl_with_verifiable_headers(
    journal_api_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, session_id = journal_api_client
    app.dependency_overrides[require_administrator] = lambda: UUID(
        "11111111-1111-1111-1111-111111111111"
    )

    csv_response = await client.get(
        "/api/v1/admin/paper-trading/journal/export",
        params={"session_id": session_id, "format": "csv"},
    )
    jsonl_response = await client.get(
        "/api/v1/admin/paper-trading/journal/export",
        params={"session_id": session_id, "format": "jsonl"},
    )

    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert csv_response.headers["content-disposition"].endswith('.csv"')
    assert csv_response.headers["x-adt-journal-rows"] == "2"
    assert len(csv_response.headers["x-adt-journal-query-checksum"]) == 64
    assert len(csv_response.headers["x-adt-journal-content-checksum"]) == 64
    assert csv_response.text.startswith("session_id,trade_id,sequence,status,")

    assert jsonl_response.status_code == 200
    assert jsonl_response.headers["content-type"].startswith("application/x-ndjson")
    assert jsonl_response.headers["content-disposition"].endswith('.jsonl"')
    lines = jsonl_response.text.splitlines()
    assert json.loads(lines[0])["kind"] == "manifest"
    assert [json.loads(line)["record"]["trade"]["status"] for line in lines[1:]] == [
        "OPEN",
        "CLOSED",
    ]
    assert (
        csv_response.headers["x-adt-journal-query-checksum"]
        == jsonl_response.headers["x-adt-journal-query-checksum"]
    )


@pytest.mark.asyncio
async def test_journal_api_rejects_unbounded_pagination(
    journal_api_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = journal_api_client
    app.dependency_overrides[require_administrator] = lambda: UUID(
        "11111111-1111-1111-1111-111111111111"
    )

    response = await client.get(
        "/api/v1/admin/paper-trading/journal",
        params={"page_size": 101},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_journal_api_rejects_resigned_state_with_safe_conflict(
    journal_api_client: tuple[httpx.AsyncClient, str],
    tmp_path: Path,
) -> None:
    client, session_id = journal_api_client
    _persist_resigned_state(
        tmp_path,
        PaperTradingRepository(tmp_path),
        session_id,
        source_checksum="d" * 64,
    )
    app.dependency_overrides[require_administrator] = lambda: UUID(int=1)

    response = await client.get(
        "/api/v1/admin/paper-trading/journal",
        params={"session_id": session_id},
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["error"] == {
        "code": "paper_session_verification_failed",
        "message": "A sessão de paper trading não pôde ser verificada.",
    }


def test_journal_http_boundary_is_read_only_and_admin_scoped() -> None:
    routes = {route.path: route for route in admin_paper_journal.router.routes}
    assert set(routes) == {
        "/api/v1/admin/paper-trading/journal",
        "/api/v1/admin/paper-trading/journal/export",
    }
    assert all(route.methods == {"GET"} for route in routes.values())


def test_journal_openapi_contract_preserves_domain_enums() -> None:
    contract = app.openapi()
    operation = contract["paths"]["/api/v1/admin/paper-trading/journal"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/PaperTradeJournalPageResponse"
    )

    export_operation = contract["paths"]["/api/v1/admin/paper-trading/journal/export"]["get"]
    assert "application/x-ndjson" in export_operation["responses"]["200"]["content"]
    assert "text/csv" in export_operation["responses"]["200"]["content"]

    schemas = contract["components"]["schemas"]
    assert schemas["PaperTradeStatus"]["enum"] == ["OPEN", "CLOSED"]
    assert schemas["PaperTradeExportFormat"]["enum"] == ["jsonl", "csv"]
