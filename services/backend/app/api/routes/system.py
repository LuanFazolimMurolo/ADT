from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app import __version__
from app.core.config import Settings

router = APIRouter(prefix="/api/v1/system", tags=["system"])


class SystemStatus(BaseModel):
    """System status response."""

    status: str
    version: str
    environment: str
    timestamp: str


@router.get("/status")
async def get_status(request: Request) -> SystemStatus:
    """Get system status and configuration."""
    app_settings = cast(Settings, request.app.state.settings)
    return SystemStatus(
        status="operational",
        version=__version__,
        environment=app_settings.environment,
        timestamp=datetime.now(UTC).isoformat(),
    )
