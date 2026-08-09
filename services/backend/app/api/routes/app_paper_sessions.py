"""Authenticated project-owner paper-session catalog endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.auth import (
    AppPaperSessionReadAccess,
    get_app_paper_session_read_access,
)
from app.api.dependencies.resources import get_paper_trading_read_service
from app.api.openapi import AUTHENTICATED_QUERY_ERROR_RESPONSES
from app.api.schemas.app_paper_sessions import AppPaperSessionCatalogResponse
from app.paper_trading.query import PaperTradingReadService

router = APIRouter(
    prefix="/api/v1/app/paper-trading",
    tags=["app paper trading"],
    responses=AUTHENTICATED_QUERY_ERROR_RESPONSES,
)


@router.get("/sessions", response_model=AppPaperSessionCatalogResponse)
def get_app_paper_sessions(
    access: Annotated[
        AppPaperSessionReadAccess,
        Depends(get_app_paper_session_read_access),
    ],
    service: Annotated[
        PaperTradingReadService,
        Depends(get_paper_trading_read_service),
    ],
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AppPaperSessionCatalogResponse:
    """Return an empty or project-owner catalog without leaking global state."""

    if not access.is_project_owner_reader:
        return AppPaperSessionCatalogResponse.empty(page=page, page_size=page_size)
    return AppPaperSessionCatalogResponse.from_domain(
        service.list_sessions(page=page, page_size=page_size)
    )
