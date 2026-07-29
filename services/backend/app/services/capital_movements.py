"""Administrative capital movement service."""

from decimal import Decimal
from uuid import UUID

from app.domain.errors import InvalidFinancialAmountError
from app.domain.models import (
    AdministrativeMovementType,
    CapitalMovement,
    JsonObject,
    LedgerMovementType,
)
from app.repositories.capital_movements import CapitalMovementRepository
from app.services._validation import require_finite_nonzero, require_nonblank, validate_pagination

_LEDGER_TYPE_BY_ADMIN_TYPE = {
    AdministrativeMovementType.DEPOSIT: LedgerMovementType.ADMIN_DEPOSIT,
    AdministrativeMovementType.WITHDRAWAL: LedgerMovementType.ADMIN_WITHDRAWAL,
    AdministrativeMovementType.ADJUSTMENT: LedgerMovementType.ADJUSTMENT,
}


class CapitalMovementService:
    """Validate API movement shape and append it to the authoritative ledger."""

    def __init__(self, repository: CapitalMovementRepository) -> None:
        self._repository = repository

    async def list(
        self,
        simulation_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[CapitalMovement], int]:
        """List immutable movements in chronological order and return the total."""
        validate_pagination(limit, offset)
        return await self._repository.list(simulation_id, limit=limit, offset=offset)

    async def create(
        self,
        *,
        simulation_id: UUID,
        movement_type: AdministrativeMovementType,
        amount: Decimal,
        reason: str,
        created_by: UUID,
        metadata: JsonObject | None = None,
    ) -> CapitalMovement:
        """Map an admin movement name while preserving its required sign."""
        require_finite_nonzero(amount)
        if movement_type is AdministrativeMovementType.DEPOSIT and amount <= 0:
            raise InvalidFinancialAmountError(message="Depósitos exigem um valor positivo.")
        if movement_type is AdministrativeMovementType.WITHDRAWAL and amount >= 0:
            raise InvalidFinancialAmountError(message="Retiradas exigem um valor negativo.")

        return await self._repository.create(
            simulation_id=simulation_id,
            movement_type=_LEDGER_TYPE_BY_ADMIN_TYPE[movement_type],
            amount=amount,
            reason=require_nonblank(reason, field_name="reason"),
            created_by=created_by,
            metadata=metadata,
        )
