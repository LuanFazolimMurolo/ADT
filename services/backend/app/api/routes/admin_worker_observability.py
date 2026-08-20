"""Administrator-only persistent worker observability endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_worker_runtime_observability_service,
)
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.worker_observability import (
    WorkerRuntimeEventListResponse,
    WorkerRuntimeListResponse,
)
from app.services.worker_observability import (
    WORKER_RUNTIME_READ_LIMIT_MAX,
    WorkerRuntimeObservabilityService,
)

router = APIRouter(
    prefix="/api/v1/admin/market-data/worker-observability",
    tags=["admin market data worker observability"],
    responses=ADMIN_ERROR_RESPONSES,
)


@router.get(
    "/runtimes",
    response_model=WorkerRuntimeListResponse,
)
async def list_worker_runtimes(
    response: Response,
    _administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        WorkerRuntimeObservabilityService,
        Depends(get_worker_runtime_observability_service),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=WORKER_RUNTIME_READ_LIMIT_MAX),
    ] = 20,
) -> WorkerRuntimeListResponse:
    """Read bounded recent worker runtime health observations."""

    response.headers["Cache-Control"] = "no-store"
    result = await service.list_runtimes(limit=limit)
    return WorkerRuntimeListResponse.from_domain(result)


@router.get(
    "/events",
    response_model=WorkerRuntimeEventListResponse,
)
async def list_worker_runtime_events(
    response: Response,
    _administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        WorkerRuntimeObservabilityService,
        Depends(get_worker_runtime_observability_service),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=WORKER_RUNTIME_READ_LIMIT_MAX),
    ] = 50,
) -> WorkerRuntimeEventListResponse:
    """Read bounded recent sanitized worker operational events."""

    response.headers["Cache-Control"] = "no-store"
    result = await service.list_events(limit=limit)
    return WorkerRuntimeEventListResponse.from_domain(result)
