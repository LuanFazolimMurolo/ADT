"""Administrative identity route."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies.auth import require_administrator
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.auth import AdminMeResponse

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    responses=ADMIN_ERROR_RESPONSES,
)


@router.get("/me", response_model=AdminMeResponse)
async def get_admin_me(
    administrator_id: Annotated[UUID, Depends(require_administrator)],
) -> AdminMeResponse:
    """Return the verified database-authorized administrator identity."""
    return AdminMeResponse(user_id=administrator_id)
