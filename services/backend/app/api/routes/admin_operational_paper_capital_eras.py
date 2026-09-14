"""Administrator-only official paper-capital era endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_operational_paper_capital_era_service,
)
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.operational_paper_capital_eras import (
    OperationalPaperCapitalEraDesignationRequest,
    OperationalPaperCapitalEraResponse,
)
from app.services.operational_paper_capital_eras import (
    OperationalPaperCapitalEraService,
)

router = APIRouter(
    prefix="/api/v1/admin/operational-paper-capital-eras",
    tags=["admin operational paper-capital eras"],
    responses=ADMIN_ERROR_RESPONSES,
)


@router.post(
    "",
    response_model=OperationalPaperCapitalEraResponse,
    status_code=status.HTTP_201_CREATED,
)
async def designate_operational_paper_capital_era(
    payload: OperationalPaperCapitalEraDesignationRequest,
    administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        OperationalPaperCapitalEraService,
        Depends(get_operational_paper_capital_era_service),
    ],
) -> OperationalPaperCapitalEraResponse:
    """Designate or replay one official simulation era."""

    era = await service.designate(
        payload.to_domain(),
        actor_id=administrator_id,
        idempotency_key=payload.idempotency_key,
    )
    return OperationalPaperCapitalEraResponse.from_domain(era)
