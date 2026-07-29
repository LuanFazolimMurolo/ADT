"""Unauthenticated routes backed only by the secured public view."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies.resources import get_public_simulation_service
from app.api.schemas.public import PublicSimulationSummaryResponse
from app.services import PublicSimulationService

router = APIRouter(prefix="/api/v1/public", tags=["public"])


@router.get(
    "/simulation",
    response_model=PublicSimulationSummaryResponse | None,
)
async def get_public_simulation(
    service: Annotated[
        PublicSimulationService,
        Depends(get_public_simulation_service),
    ],
) -> PublicSimulationSummaryResponse | None:
    """Return the UUID-free active simulation summary, when one exists."""
    summary = await service.get_active()
    if summary is None:
        return None
    return PublicSimulationSummaryResponse.model_validate(summary)
