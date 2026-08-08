"""Administrator-only persisted paper portfolio timeline endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import get_paper_portfolio_timeline_read_service
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.paper_portfolio_timeline import (
    PaperPortfolioTimelinePageResponse,
)
from app.paper_trading.portfolio_timeline_query import (
    PAPER_PORTFOLIO_TIMELINE_DEFAULT_LIMIT,
    PAPER_PORTFOLIO_TIMELINE_MAX_LIMIT,
    PaperPortfolioTimelinePageQuery,
    PaperPortfolioTimelineReadService,
)

_PORTFOLIO_TIMELINE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "description": "Bounded verified persisted paper portfolio timeline page.",
        "headers": {
            "X-ADT-Paper-Timeline-ID": {"schema": {"type": "string"}},
            "X-ADT-Paper-Timeline-State-Checksum": {"schema": {"type": "string"}},
            "X-ADT-Paper-Timeline-Content-Checksum": {"schema": {"type": "string"}},
            "X-ADT-Paper-Timeline-Rows": {"schema": {"type": "integer"}},
        },
    }
}

router = APIRouter(
    prefix="/api/v1/admin/paper-trading",
    tags=["admin paper trading"],
    responses=ADMIN_ERROR_RESPONSES,
)

SessionId = Annotated[
    str,
    Path(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
]


@router.get(
    "/sessions/{session_id}/portfolio-timeline",
    response_model=PaperPortfolioTimelinePageResponse,
    responses=_PORTFOLIO_TIMELINE_RESPONSES,
)
def get_paper_portfolio_timeline(
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
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
) -> PaperPortfolioTimelinePageResponse:
    """Return one backward page from the immutable timeline of the current state."""

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
    return PaperPortfolioTimelinePageResponse.from_domain(page)
