"""Administrative paper-simulation and append-only ledger routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_capital_movement_service,
    get_simulation_service,
)
from app.api.schemas.movements import (
    CapitalMovementResponse,
    MovementCreateRequest,
    MovementListResponse,
)
from app.api.schemas.pagination import PageMeta, PageParams
from app.api.schemas.simulations import (
    SimulationCreateRequest,
    SimulationDetailResponse,
    SimulationListItem,
    SimulationListResponse,
)
from app.domain.models import AdministrativeMovementType
from app.services import CapitalMovementService, SimulationService

router = APIRouter(prefix="/api/v1/admin/simulations", tags=["admin simulations"])


@router.get("", response_model=SimulationListResponse)
async def list_simulations(
    pagination: Annotated[PageParams, Depends()],
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[SimulationService, Depends(get_simulation_service)],
) -> SimulationListResponse:
    """List simulations newest first using bounded page pagination."""
    simulations, total = await service.list(
        limit=pagination.page_size,
        offset=pagination.offset,
    )
    return SimulationListResponse(
        items=[SimulationListItem.from_domain(item) for item in simulations],
        pagination=PageMeta.from_total(
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
        ),
    )


@router.post(
    "",
    response_model=SimulationDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_simulation(
    payload: SimulationCreateRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[SimulationService, Depends(get_simulation_service)],
) -> SimulationDetailResponse:
    """Create the ACTIVE simulation and INITIAL_CAPITAL in one transaction."""
    simulation = await service.create(
        name=payload.name,
        initial_capital=payload.initial_capital,
        currency=payload.currency,
        created_by=administrator_id,
    )
    return SimulationDetailResponse.from_domain(simulation)


@router.get("/{simulation_id}", response_model=SimulationDetailResponse)
async def get_simulation(
    simulation_id: UUID,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[SimulationService, Depends(get_simulation_service)],
) -> SimulationDetailResponse:
    """Return one simulation with authoritative ledger totals."""
    simulation = await service.get(simulation_id)
    return SimulationDetailResponse.from_domain(simulation)


@router.post("/{simulation_id}/complete", response_model=SimulationDetailResponse)
async def complete_simulation(
    simulation_id: UUID,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[SimulationService, Depends(get_simulation_service)],
) -> SimulationDetailResponse:
    """Transition an ACTIVE simulation to COMPLETED exactly once."""
    simulation = await service.complete(simulation_id)
    return SimulationDetailResponse.from_domain(simulation)


@router.post("/{simulation_id}/cancel", response_model=SimulationDetailResponse)
async def cancel_simulation(
    simulation_id: UUID,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[SimulationService, Depends(get_simulation_service)],
) -> SimulationDetailResponse:
    """Transition an ACTIVE simulation to CANCELLED exactly once."""
    simulation = await service.cancel(simulation_id)
    return SimulationDetailResponse.from_domain(simulation)


@router.get(
    "/{simulation_id}/movements",
    response_model=MovementListResponse,
)
async def list_movements(
    simulation_id: UUID,
    pagination: Annotated[PageParams, Depends()],
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        CapitalMovementService,
        Depends(get_capital_movement_service),
    ],
) -> MovementListResponse:
    """List immutable movements in stable chronological order."""
    movements, total = await service.list(
        simulation_id,
        limit=pagination.page_size,
        offset=pagination.offset,
    )
    return MovementListResponse(
        items=[CapitalMovementResponse.model_validate(movement) for movement in movements],
        pagination=PageMeta.from_total(
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
        ),
    )


@router.post(
    "/{simulation_id}/movements",
    response_model=CapitalMovementResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_movement(
    simulation_id: UUID,
    payload: MovementCreateRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        CapitalMovementService,
        Depends(get_capital_movement_service),
    ],
) -> CapitalMovementResponse:
    """Append one allowed administrative movement; never rewrite the ledger."""
    movement = await service.create(
        simulation_id=simulation_id,
        movement_type=AdministrativeMovementType(payload.type.value),
        amount=payload.amount,
        reason=payload.reason,
        created_by=administrator_id,
        metadata=payload.metadata,
    )
    return CapitalMovementResponse.model_validate(movement)
