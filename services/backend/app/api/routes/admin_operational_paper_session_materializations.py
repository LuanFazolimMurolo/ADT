"""Administrator-only operational paper-session materialization endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_operational_paper_session_materialization_service,
)
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.operational_paper_session_materializations import (
    OperationalPaperSessionMaterializationCreateRequest,
    OperationalPaperSessionMaterializationListResponse,
    OperationalPaperSessionMaterializationResponse,
)
from app.operational_paper_session_materializations import (
    OperationalPaperSessionMaterializationState,
)
from app.services import OperationalPaperSessionMaterializationService

router = APIRouter(
    prefix="/api/v1/admin/operational-paper-session-materializations",
    tags=["admin operational paper-session materializations"],
    responses=ADMIN_ERROR_RESPONSES,
)


@router.get("", response_model=OperationalPaperSessionMaterializationListResponse)
async def list_operational_paper_session_materializations(
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperSessionMaterializationService,
        Depends(get_operational_paper_session_materialization_service),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    state: Annotated[
        OperationalPaperSessionMaterializationState | None,
        Query(),
    ] = None,
) -> OperationalPaperSessionMaterializationListResponse:
    """List one bounded materialization catalog page."""

    response.headers["Cache-Control"] = "no-store"
    items, total = await service.list(
        limit=limit,
        offset=offset,
        state=state,
    )
    return OperationalPaperSessionMaterializationListResponse.from_domain(
        items,
        limit=limit,
        offset=offset,
        total=total,
    )


@router.post(
    "",
    response_model=OperationalPaperSessionMaterializationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def materialize_operational_paper_session_authorization(
    payload: OperationalPaperSessionMaterializationCreateRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperSessionMaterializationService,
        Depends(get_operational_paper_session_materialization_service),
    ],
) -> OperationalPaperSessionMaterializationResponse:
    """Materialize or reconcile one authoritative capital authorization."""

    materialization = await service.materialize_authorization(
        payload.authorization_id,
        actor_id=administrator_id,
    )
    return OperationalPaperSessionMaterializationResponse.from_domain(materialization)


@router.get(
    "/{materialization_id}",
    response_model=OperationalPaperSessionMaterializationResponse,
)
async def get_operational_paper_session_materialization(
    materialization_id: UUID,
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperSessionMaterializationService,
        Depends(get_operational_paper_session_materialization_service),
    ],
) -> OperationalPaperSessionMaterializationResponse:
    """Return one exact operational paper-session materialization."""

    response.headers["Cache-Control"] = "no-store"
    return OperationalPaperSessionMaterializationResponse.from_domain(
        await service.get(materialization_id)
    )
