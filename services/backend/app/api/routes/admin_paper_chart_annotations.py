"""Administrator-only bounded paper-session chart annotations."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_paper_chart_annotation_read_service,
)
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.paper_chart_annotations import (
    PaperChartAnnotationPageResponse,
)
from app.paper_trading.chart_annotations import (
    PAPER_CHART_ANNOTATION_DEFAULT_LIMIT,
    PAPER_CHART_ANNOTATION_MAX_LIMIT,
    PaperChartAnnotationQuery,
    PaperChartAnnotationReadService,
)

_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "description": "Bounded verified paper-session chart annotations.",
        "headers": {
            "X-ADT-Paper-Chart-Content-Checksum": {"schema": {"type": "string"}},
            "X-ADT-Paper-Chart-Rows": {"schema": {"type": "integer"}},
            "X-ADT-Paper-State-Checksum": {"schema": {"type": "string"}},
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
    "/sessions/{session_id}/chart-annotations",
    response_model=PaperChartAnnotationPageResponse,
    responses=_RESPONSES,
)
def get_paper_chart_annotations(
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
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
) -> PaperChartAnnotationPageResponse:
    """Return verified order and fill annotations in ``[start, before)``."""

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
    if page.state_checksum is not None:
        response.headers["X-ADT-Paper-State-Checksum"] = page.state_checksum
    return PaperChartAnnotationPageResponse.from_domain(page)
