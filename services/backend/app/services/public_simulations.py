"""Public simulation summary service."""

from app.domain.models import PublicSimulationSummary
from app.repositories.public_simulations import PublicSimulationRepository


class PublicSimulationService:
    """Expose only the safe public active-simulation projection."""

    def __init__(self, repository: PublicSimulationRepository) -> None:
        self._repository = repository

    async def get_active(self) -> PublicSimulationSummary | None:
        """Return the public active simulation, if one exists."""
        return await self._repository.get_active()
