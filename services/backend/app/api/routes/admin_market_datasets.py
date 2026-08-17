"""Administrator-only persisted RAW dataset inspection endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_raw_dataset_read_service,
    get_raw_gap_read_service,
    get_raw_quality_read_service,
)
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.market_datasets import (
    RawDatasetPageResponse,
    RawDatasetResponse,
    RawGapPageResponse,
    RawQualityResponse,
)
from app.market_data.operations import MAX_DATASET_ID_LENGTH
from app.market_data.raw_dataset_query import (
    RAW_DATASET_DEFAULT_PAGE_SIZE,
    RAW_DATASET_MAX_PAGE,
    RAW_DATASET_MAX_PAGE_SIZE,
    LocalRawDatasetReadService,
    RawDatasetPageQuery,
)
from app.market_data.raw_gap_query import (
    RAW_GAP_DEFAULT_PAGE_SIZE,
    RAW_GAP_MAX_PAGE,
    RAW_GAP_MAX_PAGE_SIZE,
    LocalRawGapReadService,
    RawGapPageQuery,
)
from app.market_data.raw_quality_query import LocalRawQualityReadService

router = APIRouter(
    prefix="/api/v1/admin/market-data",
    tags=["admin market data"],
    responses=ADMIN_ERROR_RESPONSES,
)

DatasetId = Annotated[
    str,
    Path(
        min_length=1,
        max_length=MAX_DATASET_ID_LENGTH,
        pattern=r"^[A-Za-z0-9_-]+$",
    ),
]


@router.get("/datasets", response_model=RawDatasetPageResponse)
def list_raw_datasets(
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        LocalRawDatasetReadService,
        Depends(get_raw_dataset_read_service),
    ],
    page: Annotated[int, Query(ge=1, le=RAW_DATASET_MAX_PAGE)] = 1,
    page_size: Annotated[
        int,
        Query(ge=1, le=RAW_DATASET_MAX_PAGE_SIZE),
    ] = RAW_DATASET_DEFAULT_PAGE_SIZE,
    symbol: Annotated[
        str | None,
        Query(
            min_length=3,
            max_length=65,
            pattern=r"^[A-Za-z0-9._-]{1,32}/[A-Za-z0-9._-]{1,32}$",
        ),
    ] = None,
    timeframe: Annotated[
        str | None,
        Query(
            min_length=2,
            max_length=16,
            pattern=r"^[1-9][0-9]*[smhdwM]$",
        ),
    ] = None,
) -> RawDatasetPageResponse:
    """List transactionally cataloged RAW datasets without storage paths."""

    response.headers["Cache-Control"] = "no-store"
    result = service.list(
        RawDatasetPageQuery(
            page=page,
            page_size=page_size,
            symbol=symbol,
            timeframe=timeframe,
        )
    )
    return RawDatasetPageResponse.from_domain(result)


@router.get("/datasets/{dataset_id}", response_model=RawDatasetResponse)
def get_raw_dataset(
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        LocalRawDatasetReadService,
        Depends(get_raw_dataset_read_service),
    ],
    dataset_id: DatasetId,
) -> RawDatasetResponse:
    """Inspect one cataloged RAW dataset by canonical opaque identifier."""

    response.headers["Cache-Control"] = "no-store"
    return RawDatasetResponse.from_domain(service.get(dataset_id))


@router.get(
    "/datasets/{dataset_id}/gaps",
    response_model=RawGapPageResponse,
)
def get_raw_dataset_gaps(
    response: Response,
    _administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        LocalRawGapReadService,
        Depends(get_raw_gap_read_service),
    ],
    dataset_id: DatasetId,
    start: Annotated[datetime, Query()],
    end: Annotated[datetime, Query()],
    page: Annotated[
        int,
        Query(ge=1, le=RAW_GAP_MAX_PAGE),
    ] = 1,
    page_size: Annotated[
        int,
        Query(ge=1, le=RAW_GAP_MAX_PAGE_SIZE),
    ] = RAW_GAP_DEFAULT_PAGE_SIZE,
) -> RawGapPageResponse:
    """Inspect bounded missing-candle ranges in one RAW dataset."""

    response.headers["Cache-Control"] = "no-store"
    result = service.inspect(
        dataset_id,
        RawGapPageQuery(
            start=start,
            end=end,
            page=page,
            page_size=page_size,
        ),
    )
    return RawGapPageResponse.from_domain(result)


@router.get(
    "/datasets/{dataset_id}/quality",
    response_model=RawQualityResponse,
)
def get_raw_dataset_quality(
    response: Response,
    _administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        LocalRawQualityReadService,
        Depends(get_raw_quality_read_service),
    ],
    dataset_id: DatasetId,
) -> RawQualityResponse:
    """Inspect the persisted FULL_DATASET RAW quality baseline."""

    response.headers["Cache-Control"] = "no-store"
    return RawQualityResponse.from_domain(service.inspect(dataset_id))
