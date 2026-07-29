"""System-setting request and response contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import JsonValue

from app.api.schemas.common import ApiSchema, NonBlankText


class SettingPatchRequest(ApiSchema):
    """Only a setting's JSON value is mutable through the API."""

    value: JsonValue


class SettingResponse(ApiSchema):
    """Non-secret system setting visible to an administrator."""

    key: NonBlankText
    value: JsonValue
    description: NonBlankText
    is_public: bool
    updated_by: UUID | None
    created_at: datetime
    updated_at: datetime


class SettingsListResponse(ApiSchema):
    """Administrative setting collection."""

    items: list[SettingResponse]
