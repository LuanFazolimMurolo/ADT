"""Administrator-only operational paper-session settlement endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_operational_paper_session_settlement_service,
)
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.operational_paper_session_settlements import (
    OperationalPaperSessionSettlementRequest,
    OperationalPaperSessionSettlementResponse,
)
from app.services.operational_paper_session_settlements import (
    OperationalPaperSessionSettlementService,
)

router = APIRouter(
    prefix="/api/v1/admin/operational-paper-session-settlements",
    tags=["admin operational paper-session settlements"],
    responses=ADMIN_ERROR_RESPONSES,
)


@router.post(
    "",
    response_model=OperationalPaperSessionSettlementResponse,
    status_code=status.HTTP_201_CREATED,
)
async def settle_operational_paper_session(
    payload: OperationalPaperSessionSettlementRequest,
    administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        OperationalPaperSessionSettlementService,
        Depends(get_operational_paper_session_settlement_service),
    ],
) -> OperationalPaperSessionSettlementResponse:
    """Settle one exact terminal session using backend-owned evidence."""

    settlement = await service.settle(
        payload.to_domain(),
        actor_id=administrator_id,
        idempotency_key=payload.idempotency_key,
    )
    return OperationalPaperSessionSettlementResponse.from_domain(settlement)
