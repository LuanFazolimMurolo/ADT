"""Administrator-only read endpoint for deterministic period metrics."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import get_paper_period_metrics_service
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.paper_period_metrics import PaperPeriodMetricsSeriesResponse
from app.paper_trading.period_metrics import (
    PaperPeriodGranularity,
    PaperPeriodMetricsFilter,
    PaperPeriodMetricsService,
)

_PERIOD_METRICS_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "description": "Deterministic UTC calendar-period performance series.",
        "headers": {
            "X-ADT-Period-Metrics-Query-Checksum": {"schema": {"type": "string"}},
            "X-ADT-Period-Metrics-Content-Checksum": {"schema": {"type": "string"}},
        },
    }
}


router = APIRouter(
    prefix="/api/v1/admin/paper-trading",
    tags=["admin paper trading"],
    responses=ADMIN_ERROR_RESPONSES,
)


def paper_period_metrics_filters(
    quote_asset: Annotated[str, Query(min_length=1, max_length=32)],
    period_from: Annotated[datetime, Query()],
    period_before: Annotated[datetime, Query()],
    session_id: Annotated[
        str | None,
        Query(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
    ] = None,
    base_asset: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
    timeframe: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    strategy_name: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    strategy_version: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> PaperPeriodMetricsFilter:
    """Build one canonical single-quote-asset period query."""

    return PaperPeriodMetricsFilter(
        quote_asset=quote_asset,
        period_from=period_from,
        period_before=period_before,
        session_id=session_id,
        base_asset=base_asset,
        timeframe_code=timeframe,
        strategy_name=strategy_name,
        strategy_version=strategy_version,
    )


@router.get(
    "/period-metrics",
    response_model=PaperPeriodMetricsSeriesResponse,
    responses=_PERIOD_METRICS_RESPONSES,
)
def get_paper_period_metrics(
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        PaperPeriodMetricsService,
        Depends(get_paper_period_metrics_service),
    ],
    filters: Annotated[
        PaperPeriodMetricsFilter,
        Depends(paper_period_metrics_filters),
    ],
    granularity: Annotated[
        PaperPeriodGranularity,
        Query(),
    ] = PaperPeriodGranularity.DAILY,
) -> PaperPeriodMetricsSeriesResponse:
    """Return one bounded deterministic UTC calendar-period series."""

    result = service.build_series(filters, granularity=granularity)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-ADT-Period-Metrics-Query-Checksum"] = result.query_checksum
    response.headers["X-ADT-Period-Metrics-Content-Checksum"] = result.content_checksum
    return PaperPeriodMetricsSeriesResponse.from_domain(result)
