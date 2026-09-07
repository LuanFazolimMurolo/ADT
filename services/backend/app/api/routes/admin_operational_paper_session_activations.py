"""Administrator-only operational paper-session activation endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_operational_paper_session_activation_service,
)
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.operational_paper_session_activations import (
    OperationalPaperSessionActivationAuthorizeRequest,
    OperationalPaperSessionActivationListResponse,
    OperationalPaperSessionActivationResponse,
    OperationalPaperSessionActivationRevokeRequest,
)
from app.operational_paper_session_activations import (
    OperationalPaperSessionActivationState,
)
from app.services import OperationalPaperSessionActivationService

router = APIRouter(
    prefix="/api/v1/admin/operational-paper-session-activations",
    tags=["admin operational paper-session activations"],
    responses=ADMIN_ERROR_RESPONSES,
)


@router.get("", response_model=OperationalPaperSessionActivationListResponse)
async def list_operational_paper_session_activations(
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperSessionActivationService,
        Depends(get_operational_paper_session_activation_service),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    state: Annotated[
        OperationalPaperSessionActivationState | None,
        Query(),
    ] = None,
    materialization_id: Annotated[UUID | None, Query()] = None,
) -> OperationalPaperSessionActivationListResponse:
    """List one bounded newest-first activation catalog page."""

    response.headers["Cache-Control"] = "no-store"
    items, total = await service.list(
        limit=limit,
        offset=offset,
        state=state,
        materialization_id=materialization_id,
    )
    return OperationalPaperSessionActivationListResponse.from_domain(
        items,
        limit=limit,
        offset=offset,
        total=total,
    )


@router.post(
    "",
    response_model=OperationalPaperSessionActivationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def authorize_operational_paper_session_activation(
    payload: OperationalPaperSessionActivationAuthorizeRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperSessionActivationService,
        Depends(get_operational_paper_session_activation_service),
    ],
) -> OperationalPaperSessionActivationResponse:
    """Create or replay one administrator-scoped paper-session activation."""

    activation = await service.authorize(
        payload.materialization_id,
        actor_id=administrator_id,
        idempotency_key=payload.idempotency_key,
    )
    return OperationalPaperSessionActivationResponse.from_domain(activation)


@router.get(
    "/{activation_id}",
    response_model=OperationalPaperSessionActivationResponse,
)
async def get_operational_paper_session_activation(
    activation_id: UUID,
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperSessionActivationService,
        Depends(get_operational_paper_session_activation_service),
    ],
) -> OperationalPaperSessionActivationResponse:
    """Return one exact paper-session activation."""

    response.headers["Cache-Control"] = "no-store"
    return OperationalPaperSessionActivationResponse.from_domain(await service.get(activation_id))


@router.post(
    "/{activation_id}/revoke",
    response_model=OperationalPaperSessionActivationResponse,
)
async def revoke_operational_paper_session_activation(
    activation_id: UUID,
    payload: OperationalPaperSessionActivationRevokeRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperSessionActivationService,
        Depends(get_operational_paper_session_activation_service),
    ],
) -> OperationalPaperSessionActivationResponse:
    """Revoke one activation without route-level lifecycle interpretation."""

    activation = await service.revoke(
        activation_id,
        expected_record_version=payload.expected_record_version,
        actor_id=administrator_id,
    )
    return OperationalPaperSessionActivationResponse.from_domain(activation)
