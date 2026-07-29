"""Simulation lifecycle service."""

from decimal import Decimal
from uuid import UUID

from app.domain.errors import InvalidFinancialAmountError
from app.domain.models import SimulationDetails, SimulationStatus
from app.repositories.simulations import SimulationRepository
from app.services._validation import require_nonblank, validate_pagination


class SimulationService:
    """Coordinate simulation creation, queries, and terminal transitions."""

    def __init__(self, repository: SimulationRepository) -> None:
        self._repository = repository

    async def list(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[SimulationDetails], int]:
        """List simulation details newest first and return the total."""
        validate_pagination(limit, offset)
        return await self._repository.list(limit=limit, offset=offset)

    async def get(self, simulation_id: UUID) -> SimulationDetails:
        """Return one simulation and its calculated ledger totals."""
        return await self._repository.get(simulation_id)

    async def create(
        self,
        *,
        name: str,
        initial_capital: Decimal,
        currency: str,
        created_by: UUID,
    ) -> SimulationDetails:
        """Create an ACTIVE simulation and INITIAL_CAPITAL atomically."""
        if not initial_capital.is_finite() or initial_capital <= 0:
            raise InvalidFinancialAmountError()

        return await self._repository.create_with_initial_capital(
            name=require_nonblank(name, field_name="name"),
            initial_capital=initial_capital,
            currency=require_nonblank(currency, field_name="currency").upper(),
            created_by=created_by,
        )

    async def complete(self, simulation_id: UUID) -> SimulationDetails:
        """Complete an ACTIVE simulation exactly once."""
        return await self._repository.transition(
            simulation_id,
            target_status=SimulationStatus.COMPLETED,
        )

    async def cancel(self, simulation_id: UUID) -> SimulationDetails:
        """Cancel an ACTIVE simulation exactly once."""
        return await self._repository.transition(
            simulation_id,
            target_status=SimulationStatus.CANCELLED,
        )
