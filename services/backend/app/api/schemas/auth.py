"""Administrative identity response contracts."""

from uuid import UUID

from app.api.schemas.common import ApiSchema


class AdminMeResponse(ApiSchema):
    """Authenticated administrator identity without email or token data."""

    user_id: UUID
    is_admin: bool = True
