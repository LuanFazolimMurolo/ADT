"""Public system-status contract."""

from datetime import datetime
from typing import Literal

from app.api.schemas.common import ApiSchema
from app.core.config import Environment


class SystemStatus(ApiSchema):
    """Narrow operational metadata with no infrastructure details."""

    status: Literal["operational"]
    version: str
    environment: Environment
    timestamp: datetime
