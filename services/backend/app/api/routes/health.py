from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies.resources import get_database
from app.api.schemas.health import HealthResponse
from app.database import Database
from app.domain.errors import PersistenceUnavailableError

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Get API health status."""
    return HealthResponse(status="healthy")


@router.get("/readiness", response_model=HealthResponse)
async def readiness() -> HealthResponse:
    """Get API readiness status."""
    return HealthResponse(status="ready")


@router.get("/database", response_model=HealthResponse)
async def database_health(
    database: Annotated[Database, Depends(get_database)],
) -> HealthResponse:
    """Verify PostgreSQL without returning connection information."""
    if not await database.health_check():
        raise PersistenceUnavailableError()
    return HealthResponse(status="healthy")
