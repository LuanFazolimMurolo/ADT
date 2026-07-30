"""System-setting request and response contracts."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import StringConstraints

from app.api.schemas.common import ApiSchema, JsonValue, NonBlankText

SettingKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    ),
]


class SettingPatchRequest(ApiSchema):
    """Only a setting's JSON value is mutable through the API."""

    value: JsonValue


class SettingResponse(ApiSchema):
    """Non-secret system setting visible to an administrator."""

    key: SettingKey
    value: JsonValue
    description: NonBlankText
    is_public: bool
    updated_by: UUID | None
    created_at: datetime
    updated_at: datetime


class SettingsListResponse(ApiSchema):
    """Administrative setting collection."""

    items: list[SettingResponse]
