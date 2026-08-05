"""Authenticated read-only trade-journal query and export routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_paper_trade_journal_export_service,
    get_paper_trade_journal_read_service,
)
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.paper_journal import PaperTradeJournalPageResponse
from app.paper_trading.journal import PaperTradeStatus
from app.paper_trading.journal_export import (
    PaperTradeExportFormat,
    PaperTradeJournalExportService,
)
from app.paper_trading.journal_query import (
    PaperTradeJournalFilter,
    PaperTradeJournalReadService,
)

router = APIRouter(
    prefix="/api/v1/admin/paper-trading",
    tags=["admin paper trading"],
    responses=ADMIN_ERROR_RESPONSES,
)

_EXPORT_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "description": "Deterministic journal export.",
        "content": {
            "application/x-ndjson": {"schema": {"type": "string", "format": "binary"}},
            "text/csv": {"schema": {"type": "string", "format": "binary"}},
        },
        "headers": {
            "Content-Disposition": {"schema": {"type": "string"}},
            "X-ADT-Journal-Query-Checksum": {"schema": {"type": "string"}},
            "X-ADT-Journal-Content-Checksum": {"schema": {"type": "string"}},
            "X-ADT-Journal-Rows": {"schema": {"type": "integer"}},
        },
    }
}


def paper_trade_journal_filters(
    session_id: Annotated[
        str | None,
        Query(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
    ] = None,
    base_asset: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
    quote_asset: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
    timeframe: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    strategy_name: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    strategy_version: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    status: Annotated[PaperTradeStatus | None, Query()] = None,
    opened_from: Annotated[datetime | None, Query()] = None,
    opened_before: Annotated[datetime | None, Query()] = None,
    closed_from: Annotated[datetime | None, Query()] = None,
    closed_before: Annotated[datetime | None, Query()] = None,
) -> PaperTradeJournalFilter:
    """Build one canonical filter from bounded HTTP query parameters."""

    return PaperTradeJournalFilter(
        session_id=session_id,
        base_asset=base_asset,
        quote_asset=quote_asset,
        timeframe_code=timeframe,
        strategy_name=strategy_name,
        strategy_version=strategy_version,
        status=status,
        opened_from=opened_from,
        opened_before=opened_before,
        closed_from=closed_from,
        closed_before=closed_before,
    )


@router.get("/journal", response_model=PaperTradeJournalPageResponse)
def get_paper_trade_journal(
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        PaperTradeJournalReadService,
        Depends(get_paper_trade_journal_read_service),
    ],
    filters: Annotated[
        PaperTradeJournalFilter,
        Depends(paper_trade_journal_filters),
    ],
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaperTradeJournalPageResponse:
    """Return one bounded, newest-first, administrator-only journal page."""

    result = service.list_trades(filters, page=page, page_size=page_size)
    return PaperTradeJournalPageResponse.from_domain(result)


@router.get(
    "/journal/export",
    response_class=Response,
    responses=_EXPORT_RESPONSES,
)
def export_paper_trade_journal(
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        PaperTradeJournalExportService,
        Depends(get_paper_trade_journal_export_service),
    ],
    filters: Annotated[
        PaperTradeJournalFilter,
        Depends(paper_trade_journal_filters),
    ],
    format: Annotated[PaperTradeExportFormat, Query()] = PaperTradeExportFormat.JSONL,
) -> Response:
    """Download one bounded deterministic export through the verified read path."""

    result = service.export(filters, format=format)
    return Response(
        content=result.content,
        media_type=result.media_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{result.filename}"',
            "X-ADT-Journal-Query-Checksum": result.query_checksum,
            "X-ADT-Journal-Content-Checksum": result.content_checksum,
            "X-ADT-Journal-Rows": str(result.rows_count),
        },
    )
