from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, Request

from app import __version__
from app.api.openapi import PUBLIC_ERROR_RESPONSES
from app.api.schemas.system import SystemStatus
from app.core.config import Settings

router = APIRouter(
    prefix="/api/v1/system",
    tags=["system"],
    responses=PUBLIC_ERROR_RESPONSES,
)


@router.get("/status", response_model=SystemStatus)
async def get_status(request: Request) -> SystemStatus:
    """Get system status and configuration."""
    app_settings = cast(Settings, request.app.state.settings)
    return SystemStatus(
        status="operational",
        version=__version__,
        environment=app_settings.environment,
        timestamp=datetime.now(UTC),
    )
