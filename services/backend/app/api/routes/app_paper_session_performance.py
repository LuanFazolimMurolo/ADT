"""Authorized read-only paper-session performance routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.dependencies.auth import require_app_paper_session_reader
from app.api.dependencies.resources import (
    get_paper_period_metrics_service,
    get_paper_portfolio_timeline_read_service,
    get_paper_trading_read_service,
)
from app.api.openapi import APP_PAPER_SESSION_ERROR_RESPONSES
from app.api.schemas.app_paper_session_performance import (
    AppPaperPeriodMetricsSeriesResponse,
    AppPaperPortfolioTimelinePageResponse,
)
from app.paper_trading.period_metrics import (
    PaperPeriodGranularity,
    PaperPeriodMetricsFilter,
    PaperPeriodMetricsService,
)
from app.paper_trading.portfolio_timeline_query import (
    PAPER_PORTFOLIO_TIMELINE_DEFAULT_LIMIT,
    PAPER_PORTFOLIO_TIMELINE_MAX_LIMIT,
    PaperPortfolioTimelinePageQuery,
    PaperPortfolioTimelineReadService,
)
from app.paper_trading.query import PaperTradingReadService

_TIMELINE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "description": "Bounded authorized persisted paper portfolio timeline.",
        "headers": {
            "X-ADT-Paper-Timeline-ID": {"schema": {"type": "string"}},
            "X-ADT-Paper-Timeline-State-Checksum": {"schema": {"type": "string"}},
            "X-ADT-Paper-Timeline-Content-Checksum": {"schema": {"type": "string"}},
            "X-ADT-Paper-Timeline-Rows": {"schema": {"type": "integer"}},
        },
    }
}

_PERIOD_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "description": "Authorized realized-only UTC period metrics.",
        "headers": {
            "X-ADT-Period-Metrics-Query-Checksum": {"schema": {"type": "string"}},
            "X-ADT-Period-Metrics-Content-Checksum": {"schema": {"type": "string"}},
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


@router.get(
    "/{session_id}/portfolio-timeline",
    response_model=AppPaperPortfolioTimelinePageResponse,
    responses=_TIMELINE_RESPONSES,
)
def get_app_paper_portfolio_timeline(
    response: Response,
    _reader_id: Annotated[UUID, Depends(require_app_paper_session_reader)],
    service: Annotated[
        PaperPortfolioTimelineReadService,
        Depends(get_paper_portfolio_timeline_read_service),
    ],
    session_id: SessionId,
    before: Annotated[datetime | None, Query()] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=PAPER_PORTFOLIO_TIMELINE_MAX_LIMIT),
    ] = PAPER_PORTFOLIO_TIMELINE_DEFAULT_LIMIT,
) -> AppPaperPortfolioTimelinePageResponse:
    """Read one backward page from the existing persisted timeline only."""

    page = service.read_page(
        PaperPortfolioTimelinePageQuery(
            session_id=session_id,
            before=before,
            limit=limit,
        )
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-ADT-Paper-Timeline-ID"] = page.timeline_id
    response.headers["X-ADT-Paper-Timeline-State-Checksum"] = page.state_checksum
    response.headers["X-ADT-Paper-Timeline-Content-Checksum"] = page.content_checksum
    response.headers["X-ADT-Paper-Timeline-Rows"] = str(len(page.observations))
    return AppPaperPortfolioTimelinePageResponse.from_domain(page)


@router.get(
    "/{session_id}/period-metrics",
    response_model=AppPaperPeriodMetricsSeriesResponse,
    responses=_PERIOD_RESPONSES,
)
def get_app_paper_period_metrics(
    response: Response,
    _reader_id: Annotated[UUID, Depends(require_app_paper_session_reader)],
    session_service: Annotated[
        PaperTradingReadService,
        Depends(get_paper_trading_read_service),
    ],
    metrics_service: Annotated[
        PaperPeriodMetricsService,
        Depends(get_paper_period_metrics_service),
    ],
    session_id: SessionId,
    period_from: Annotated[datetime, Query()],
    period_before: Annotated[datetime, Query()],
    granularity: Annotated[PaperPeriodGranularity, Query()] = PaperPeriodGranularity.DAILY,
) -> AppPaperPeriodMetricsSeriesResponse:
    """Build realized-only metrics for exactly the authorized path session."""

    session = session_service.get_session(session_id)
    filters = PaperPeriodMetricsFilter(
        session_id=session_id,
        quote_asset=session.config.pair.quote,
        period_from=period_from,
        period_before=period_before,
    )
    result = metrics_service.build_series(filters, granularity=granularity)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-ADT-Period-Metrics-Query-Checksum"] = result.query_checksum
    response.headers["X-ADT-Period-Metrics-Content-Checksum"] = result.content_checksum
    return AppPaperPeriodMetricsSeriesResponse.from_domain(result, session_id=session_id)
