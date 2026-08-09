"""Authenticated application identity route."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies.auth import get_authenticated_user
from app.api.dependencies.resources import get_admin_service
from app.api.openapi import AUTHENTICATED_ERROR_RESPONSES
from app.api.schemas.auth import AppMeResponse
from app.services import AdminService

router = APIRouter(
    prefix="/api/v1/app",
    tags=["app"],
    responses=AUTHENTICATED_ERROR_RESPONSES,
)


@router.get("/me", response_model=AppMeResponse)
async def get_app_me(
    user_id: Annotated[UUID, Depends(get_authenticated_user)],
    admin_service: Annotated[AdminService, Depends(get_admin_service)],
) -> AppMeResponse:
    """Return authenticated identity and backend-authoritative admin membership."""
    return AppMeResponse(
        user_id=user_id,
        is_admin=await admin_service.is_admin(user_id),
    )
