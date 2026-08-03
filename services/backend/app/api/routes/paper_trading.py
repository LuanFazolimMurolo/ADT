"""Public read-only paper-trading session and runner routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.dependencies.resources import (
    get_paper_runner_state_store,
    get_paper_trading_read_service,
)
from app.api.openapi import PAPER_TRADING_ERROR_RESPONSES
from app.api.schemas.paper_trading import (
    PaperFillListResponse,
    PaperOrderListResponse,
    PaperRunnerStatusResponse,
    PaperSessionDetailResponse,
    PaperSessionListResponse,
)
from app.paper_trading.continuous import PaperRunnerStateStore
from app.paper_trading.query import PaperTradingReadService

router = APIRouter(
    prefix="/api/v1/paper-trading",
    tags=["paper-trading"],
    responses=PAPER_TRADING_ERROR_RESPONSES,
)

SessionId = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]


@router.get("/runner/status", response_model=PaperRunnerStatusResponse)
def get_runner_status(
    store: Annotated[PaperRunnerStateStore, Depends(get_paper_runner_state_store)],
) -> PaperRunnerStatusResponse:
    return PaperRunnerStatusResponse.from_domain(store.require())


@router.get("/sessions", response_model=PaperSessionListResponse)
def list_sessions(
    service: Annotated[PaperTradingReadService, Depends(get_paper_trading_read_service)],
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> PaperSessionListResponse:
    return PaperSessionListResponse.from_domain(
        service.list_sessions(page=page, page_size=page_size)
    )


@router.get("/sessions/{session_id}", response_model=PaperSessionDetailResponse)
def get_session(
    session_id: SessionId,
    service: Annotated[PaperTradingReadService, Depends(get_paper_trading_read_service)],
) -> PaperSessionDetailResponse:
    return PaperSessionDetailResponse.from_domain(service.get_session(session_id))


@router.get("/sessions/{session_id}/orders", response_model=PaperOrderListResponse)
def list_orders(
    session_id: SessionId,
    service: Annotated[PaperTradingReadService, Depends(get_paper_trading_read_service)],
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> PaperOrderListResponse:
    return PaperOrderListResponse.from_domain(
        service.list_orders(session_id, page=page, page_size=page_size)
    )


@router.get("/sessions/{session_id}/fills", response_model=PaperFillListResponse)
def list_fills(
    session_id: SessionId,
    service: Annotated[PaperTradingReadService, Depends(get_paper_trading_read_service)],
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> PaperFillListResponse:
    return PaperFillListResponse.from_domain(
        service.list_fills(session_id, page=page, page_size=page_size)
    )
