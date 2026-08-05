"""Authenticated read-only paper-trading performance dashboard route."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_paper_dashboard_read_service,
    get_paper_runner_state_store,
)
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.paper_dashboard import PaperDashboardResponse
from app.paper_trading.continuous import PaperRunnerStateStore
from app.paper_trading.dashboard import PaperDashboardReadService

router = APIRouter(
    prefix="/api/v1/admin/paper-trading",
    tags=["admin paper trading"],
    responses=ADMIN_ERROR_RESPONSES,
)


@router.get("/dashboard", response_model=PaperDashboardResponse)
def get_paper_dashboard(
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        PaperDashboardReadService,
        Depends(get_paper_dashboard_read_service),
    ],
    runner_store: Annotated[
        PaperRunnerStateStore,
        Depends(get_paper_runner_state_store),
    ],
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaperDashboardResponse:
    """Return one bounded, administrator-only performance dashboard page."""

    dashboard = service.build_page(
        page=page,
        page_size=page_size,
        runner_state=runner_store.read(),
    )
    return PaperDashboardResponse.from_domain(dashboard)
