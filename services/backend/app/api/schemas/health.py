"""Service and database health response contracts."""

from typing import Literal

from app.api.schemas.common import ApiSchema


class HealthResponse(ApiSchema):
    """A narrow health response with no infrastructure details."""

    status: Literal["healthy", "ready"]
