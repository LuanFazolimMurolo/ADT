"""Unauthenticated routes backed only by the secured public view."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies.public_official_paper import (
    get_public_official_paper_service,
)
from app.api.dependencies.resources import get_public_simulation_service
from app.api.openapi import PUBLIC_ERROR_RESPONSES
from app.api.schemas.public import PublicSimulationSummaryResponse
from app.api.schemas.public_official_paper import (
    PublicOfficialPaperSnapshotResponse,
)
from app.services import PublicSimulationService
from app.services.public_official_paper import PublicOfficialPaperService

router = APIRouter(
    prefix="/api/v1/public",
    tags=["public"],
    responses=PUBLIC_ERROR_RESPONSES,
)


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


@router.get(
    "/official-paper",
    response_model=PublicOfficialPaperSnapshotResponse,
)
async def get_public_official_paper(
    service: Annotated[
        PublicOfficialPaperService,
        Depends(get_public_official_paper_service),
    ],
) -> PublicOfficialPaperSnapshotResponse:
    """Return the bounded sanitized official PAPER operational snapshot."""

    snapshot = await service.get_snapshot()
    return PublicOfficialPaperSnapshotResponse.from_domain(snapshot)
