from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__
from app.core.config import settings

router = APIRouter(prefix="/api/v1/system", tags=["system"])


class SystemStatus(BaseModel):
    """System status response."""

    status: str
    version: str
    environment: str
    timestamp: str


@router.get("/status")
async def get_status() -> SystemStatus:
    """Get system status and configuration."""
    return SystemStatus(
        status="operational",
        version=__version__,
        environment=settings.environment,
        timestamp=datetime.utcnow().isoformat(),
    )
