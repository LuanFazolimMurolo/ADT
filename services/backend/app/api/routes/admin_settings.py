"""Administrative non-secret system-setting routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import get_settings_service
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.settings import (
    SettingKey,
    SettingPatchRequest,
    SettingResponse,
    SettingsListResponse,
)
from app.services import SettingsService

router = APIRouter(
    prefix="/api/v1/admin/settings",
    tags=["admin settings"],
    responses=ADMIN_ERROR_RESPONSES,
)


@router.get("", response_model=SettingsListResponse)
async def list_settings(
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[SettingsService, Depends(get_settings_service)],
) -> SettingsListResponse:
    """List every non-secret application setting."""
    settings = await service.list()
    return SettingsListResponse(
        items=[SettingResponse.model_validate(setting) for setting in settings]
    )


@router.patch("/{key}", response_model=SettingResponse)
async def update_setting(
    key: SettingKey,
    payload: SettingPatchRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[SettingsService, Depends(get_settings_service)],
) -> SettingResponse:
    """Change only an existing setting value and record the administrator UUID."""
    setting = await service.update_value(
        key,
        value=payload.value,
        updated_by=administrator_id,
    )
    return SettingResponse.model_validate(setting)
