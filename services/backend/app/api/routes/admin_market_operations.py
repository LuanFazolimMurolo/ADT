"""Administrator-only market-data operation control-plane endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import get_asset_market_service, get_market_operation_service
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.market_operations import (
    DatasetId,
    IncrementalMarketOperationPlanPreviewResponse,
    MarketOperationBackfillPreviewRequest,
    MarketOperationControlRequest,
    MarketOperationIncrementalPreviewRequest,
    MarketOperationListResponse,
    MarketOperationPlanPreviewResponse,
    MarketOperationResponse,
    MarketOperationSubmitRequest,
    MarketOperationTargetListResponse,
)
from app.market_data.asset_catalog import AssetCatalogQuery, AssetMarketService
from app.market_data.operations import MarketOperationState, decode_dataset_id
from app.services import MarketOperationService

router = APIRouter(
    prefix="/api/v1/admin/market-data/operations",
    tags=["admin market data operations"],
    responses=ADMIN_ERROR_RESPONSES,
)


@router.get("/targets", response_model=MarketOperationTargetListResponse)
async def list_operation_targets(
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[AssetMarketService, Depends(get_asset_market_service)],
    active_only: Annotated[bool, Query()] = True,
    quote_asset: Annotated[
        str | None,
        Query(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$"),
    ] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> MarketOperationTargetListResponse:
    """Resolve bounded valid operation targets through the existing asset catalog."""
    result = await service.list_assets(
        AssetCatalogQuery(
            active_only=active_only,
            quote_asset=quote_asset,
            search=search,
            page=page,
            page_size=page_size,
        )
    )
    return MarketOperationTargetListResponse.from_domain(result)


@router.post(
    "/preview/backfill",
    response_model=MarketOperationPlanPreviewResponse,
)
def preview_backfill(
    payload: MarketOperationBackfillPreviewRequest,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[MarketOperationService, Depends(get_market_operation_service)],
) -> MarketOperationPlanPreviewResponse:
    """Create a bounded backend-owned backfill preview without executing a job."""
    preview = service.plan_backfill(
        dataset=payload.dataset(),
        data_range=payload.data_range(),
    )
    return MarketOperationPlanPreviewResponse.from_domain(preview)


@router.post(
    "/preview/incremental",
    response_model=IncrementalMarketOperationPlanPreviewResponse,
)
def preview_incremental(
    payload: MarketOperationIncrementalPreviewRequest,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[MarketOperationService, Depends(get_market_operation_service)],
) -> IncrementalMarketOperationPlanPreviewResponse:
    """Read local state under a recovered lease and return a bounded preview."""
    preview = service.plan_incremental(
        dataset=payload.dataset(),
        overlap_candles=payload.overlap_candles,
        start=payload.start,
    )
    return IncrementalMarketOperationPlanPreviewResponse.from_domain(preview)


@router.post(
    "",
    response_model=MarketOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_operation(
    payload: MarketOperationSubmitRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[MarketOperationService, Depends(get_market_operation_service)],
) -> MarketOperationResponse:
    """Persist confirmed operational intent; execution remains worker-only."""
    operation = await service.submit(
        operation_type=payload.operation_type,
        dataset=payload.dataset(),
        data_range=payload.data_range(),
        plan_checksum=payload.plan_checksum,
        idempotency_key=payload.idempotency_key,
        requested_by=administrator_id,
    )
    return MarketOperationResponse.from_domain(operation, observed_at=service.observed_at())


@router.get("", response_model=MarketOperationListResponse)
async def list_operations(
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[MarketOperationService, Depends(get_market_operation_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    state: Annotated[MarketOperationState | None, Query()] = None,
    requested_by: Annotated[UUID | None, Query()] = None,
    dataset_id: Annotated[DatasetId | None, Query()] = None,
) -> MarketOperationListResponse:
    """List a bounded operation page without performing an unbounded count."""
    operations = await service.list(
        limit=limit + 1,
        offset=offset,
        state=state,
        requested_by=requested_by,
        dataset=None if dataset_id is None else decode_dataset_id(dataset_id),
    )
    has_more = len(operations) > limit
    selected = operations[:limit]
    observed_at = service.observed_at()
    return MarketOperationListResponse(
        items=[
            MarketOperationResponse.from_domain(item, observed_at=observed_at) for item in selected
        ],
        limit=limit,
        offset=offset,
        count=len(selected),
        has_more=has_more,
    )


@router.get("/{operation_id}", response_model=MarketOperationResponse)
async def get_operation(
    operation_id: UUID,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[MarketOperationService, Depends(get_market_operation_service)],
) -> MarketOperationResponse:
    """Return one sanitized persisted operation."""
    operation = await service.get(operation_id)
    return MarketOperationResponse.from_domain(operation, observed_at=service.observed_at())


@router.post("/{operation_id}/pause", response_model=MarketOperationResponse)
async def pause_operation(
    operation_id: UUID,
    payload: MarketOperationControlRequest,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[MarketOperationService, Depends(get_market_operation_service)],
) -> MarketOperationResponse:
    """Request cooperative pause using optimistic concurrency."""
    operation = await service.pause(
        operation_id,
        expected_version=payload.expected_version,
    )
    return MarketOperationResponse.from_domain(operation, observed_at=service.observed_at())


@router.post("/{operation_id}/resume", response_model=MarketOperationResponse)
async def resume_operation(
    operation_id: UUID,
    payload: MarketOperationControlRequest,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[MarketOperationService, Depends(get_market_operation_service)],
) -> MarketOperationResponse:
    """Return a paused operation to the pending queue."""
    operation = await service.resume(
        operation_id,
        expected_version=payload.expected_version,
    )
    return MarketOperationResponse.from_domain(operation, observed_at=service.observed_at())


@router.post("/{operation_id}/cancel", response_model=MarketOperationResponse)
async def cancel_operation(
    operation_id: UUID,
    payload: MarketOperationControlRequest,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[MarketOperationService, Depends(get_market_operation_service)],
) -> MarketOperationResponse:
    """Request cooperative cancellation using optimistic concurrency."""
    operation = await service.cancel(
        operation_id,
        expected_version=payload.expected_version,
    )
    return MarketOperationResponse.from_domain(operation, observed_at=service.observed_at())
