"""Administrator-only operational paper-capital authorization endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_operational_paper_capital_authorization_service,
)
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.operational_paper_capital_authorizations import (
    OperationalPaperCapitalAuthorizationCreateRequest,
    OperationalPaperCapitalAuthorizationListResponse,
    OperationalPaperCapitalAuthorizationResponse,
    OperationalPaperCapitalAuthorizationRevokeRequest,
)
from app.operational_paper_capital_authorizations import (
    OperationalPaperCapitalAuthorizationState,
)
from app.services import OperationalPaperCapitalAuthorizationService

router = APIRouter(
    prefix="/api/v1/admin/operational-paper-capital-authorizations",
    tags=["admin operational paper-capital authorizations"],
    responses=ADMIN_ERROR_RESPONSES,
)


@router.get("", response_model=OperationalPaperCapitalAuthorizationListResponse)
async def list_operational_paper_capital_authorizations(
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperCapitalAuthorizationService,
        Depends(get_operational_paper_capital_authorization_service),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    state: Annotated[
        OperationalPaperCapitalAuthorizationState | None,
        Query(),
    ] = None,
) -> OperationalPaperCapitalAuthorizationListResponse:
    """List one bounded newest-first authorization catalog page."""

    response.headers["Cache-Control"] = "no-store"
    items, total = await service.list(
        limit=limit,
        offset=offset,
        state=state,
    )
    return OperationalPaperCapitalAuthorizationListResponse.from_domain(
        items,
        limit=limit,
        offset=offset,
        total=total,
    )


@router.post(
    "",
    response_model=OperationalPaperCapitalAuthorizationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_operational_paper_capital_authorization(
    payload: OperationalPaperCapitalAuthorizationCreateRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperCapitalAuthorizationService,
        Depends(get_operational_paper_capital_authorization_service),
    ],
) -> OperationalPaperCapitalAuthorizationResponse:
    """Create or replay one administrator-scoped paper-capital authorization."""

    authorization = await service.create(
        payload.intent.to_domain(),
        actor_id=administrator_id,
        idempotency_key=payload.idempotency_key,
    )
    return OperationalPaperCapitalAuthorizationResponse.from_domain(authorization)


@router.get(
    "/{authorization_id}",
    response_model=OperationalPaperCapitalAuthorizationResponse,
)
async def get_operational_paper_capital_authorization(
    authorization_id: UUID,
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperCapitalAuthorizationService,
        Depends(get_operational_paper_capital_authorization_service),
    ],
) -> OperationalPaperCapitalAuthorizationResponse:
    """Return one exact paper-capital authorization."""

    response.headers["Cache-Control"] = "no-store"
    return OperationalPaperCapitalAuthorizationResponse.from_domain(
        await service.get(authorization_id)
    )


@router.post(
    "/{authorization_id}/revoke",
    response_model=OperationalPaperCapitalAuthorizationResponse,
)
async def revoke_operational_paper_capital_authorization(
    authorization_id: UUID,
    payload: OperationalPaperCapitalAuthorizationRevokeRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperCapitalAuthorizationService,
        Depends(get_operational_paper_capital_authorization_service),
    ],
) -> OperationalPaperCapitalAuthorizationResponse:
    """Revoke one authorization without route-level lifecycle interpretation."""

    authorization = await service.revoke(
        authorization_id,
        expected_record_version=payload.expected_record_version,
        actor_id=administrator_id,
    )
    return OperationalPaperCapitalAuthorizationResponse.from_domain(authorization)
