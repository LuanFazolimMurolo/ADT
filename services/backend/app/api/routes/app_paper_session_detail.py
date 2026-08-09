"""Authorized read-only paper-session chart and trade routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.dependencies.auth import require_app_paper_session_reader
from app.api.dependencies.resources import (
    get_paper_chart_annotation_read_service,
    get_paper_trade_journal_read_service,
    get_paper_trading_read_service,
)
from app.api.openapi import APP_PAPER_SESSION_ERROR_RESPONSES
from app.api.schemas.app_paper_session_detail import (
    AppPaperChartAnnotationPageResponse,
    AppPaperSessionDetailResponse,
    AppPaperTradePageResponse,
)
from app.paper_trading.chart_annotations import (
    PAPER_CHART_ANNOTATION_DEFAULT_LIMIT,
    PAPER_CHART_ANNOTATION_MAX_LIMIT,
    PaperChartAnnotationQuery,
    PaperChartAnnotationReadService,
)
from app.paper_trading.journal import PaperTradeStatus
from app.paper_trading.journal_query import (
    PaperTradeJournalFilter,
    PaperTradeJournalReadService,
)
from app.paper_trading.query import PaperTradingReadService

_ANNOTATION_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "description": "Bounded authorized paper-session chart annotations.",
        "headers": {
            "X-ADT-Paper-Chart-Content-Checksum": {"schema": {"type": "string"}},
            "X-ADT-Paper-Chart-Rows": {"schema": {"type": "integer"}},
        },
    }
}

router = APIRouter(
    prefix="/api/v1/app/paper-trading/sessions",
    tags=["app paper trading"],
    responses=APP_PAPER_SESSION_ERROR_RESPONSES,
)

SessionId = Annotated[
    str,
    Path(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
]


@router.get("/{session_id}", response_model=AppPaperSessionDetailResponse)
def get_app_paper_session(
    _reader_id: Annotated[UUID, Depends(require_app_paper_session_reader)],
    service: Annotated[
        PaperTradingReadService,
        Depends(get_paper_trading_read_service),
    ],
    session_id: SessionId,
) -> AppPaperSessionDetailResponse:
    """Return minimum identity only after project-owner authorization."""

    return AppPaperSessionDetailResponse.from_domain(service.get_session(session_id))


@router.get(
    "/{session_id}/chart-annotations",
    response_model=AppPaperChartAnnotationPageResponse,
    responses=_ANNOTATION_RESPONSES,
)
def get_app_paper_chart_annotations(
    response: Response,
    _reader_id: Annotated[UUID, Depends(require_app_paper_session_reader)],
    service: Annotated[
        PaperChartAnnotationReadService,
        Depends(get_paper_chart_annotation_read_service),
    ],
    session_id: SessionId,
    start: Annotated[datetime, Query()],
    before: Annotated[datetime, Query()],
    limit: Annotated[
        int,
        Query(ge=1, le=PAPER_CHART_ANNOTATION_MAX_LIMIT),
    ] = PAPER_CHART_ANNOTATION_DEFAULT_LIMIT,
) -> AppPaperChartAnnotationPageResponse:
    """Return minimum chart events in the requested half-open UTC range."""

    page = service.read_page(
        PaperChartAnnotationQuery(
            session_id=session_id,
            range_start=start,
            range_end=before,
            limit=limit,
        )
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-ADT-Paper-Chart-Content-Checksum"] = page.content_checksum
    response.headers["X-ADT-Paper-Chart-Rows"] = str(page.count)
    return AppPaperChartAnnotationPageResponse.from_domain(page)


@router.get("/{session_id}/trades", response_model=AppPaperTradePageResponse)
def get_app_paper_trades(
    _reader_id: Annotated[UUID, Depends(require_app_paper_session_reader)],
    service: Annotated[
        PaperTradeJournalReadService,
        Depends(get_paper_trade_journal_read_service),
    ],
    session_id: SessionId,
    status: Annotated[PaperTradeStatus | None, Query()] = None,
    opened_from: Annotated[datetime | None, Query()] = None,
    opened_before: Annotated[datetime | None, Query()] = None,
    closed_from: Annotated[datetime | None, Query()] = None,
    closed_before: Annotated[datetime | None, Query()] = None,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AppPaperTradePageResponse:
    """Return one server-paginated journal page scoped only by the path."""

    filters = PaperTradeJournalFilter(
        session_id=session_id,
        status=status,
        opened_from=opened_from,
        opened_before=opened_before,
        closed_from=closed_from,
        closed_before=closed_before,
    )
    return AppPaperTradePageResponse.from_domain(
        service.list_trades(filters, page=page, page_size=page_size)
    )
