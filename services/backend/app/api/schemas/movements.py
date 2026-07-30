"""Capital-ledger request and response contracts."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import StringConstraints, model_validator

from app.api.schemas.common import (
    ApiSchema,
    FinancialDecimal,
    JsonObject,
    NonBlankText,
    NonZeroFinancialDecimalStringInput,
)
from app.api.schemas.pagination import PaginatedResponse


class MovementCreateType(StrEnum):
    """Only movement kinds that an administrator may submit through the API."""

    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    ADJUSTMENT = "ADJUSTMENT"


class CapitalMovementType(StrEnum):
    """Complete set of historical movement kinds stored in PostgreSQL."""

    INITIAL_CAPITAL = "INITIAL_CAPITAL"
    ADMIN_DEPOSIT = "ADMIN_DEPOSIT"
    ADMIN_WITHDRAWAL = "ADMIN_WITHDRAWAL"
    TRADE_PROFIT = "TRADE_PROFIT"
    TRADE_LOSS = "TRADE_LOSS"
    FEE = "FEE"
    ADJUSTMENT = "ADJUSTMENT"


MovementReason = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]


class MovementCreateRequest(ApiSchema):
    """Append-only administrative ledger entry."""

    type: MovementCreateType
    amount: NonZeroFinancialDecimalStringInput
    reason: MovementReason
    metadata: JsonObject | None = None

    @model_validator(mode="after")
    def validate_amount_sign(self) -> Self:
        """Reject sign/type mismatches at the HTTP boundary.

        PostgreSQL remains authoritative and rechecks the mapped ledger type.
        """

        if self.type is MovementCreateType.DEPOSIT and self.amount <= 0:
            raise ValueError("DEPOSIT amount must be greater than zero.")
        if self.type is MovementCreateType.WITHDRAWAL and self.amount >= 0:
            raise ValueError("WITHDRAWAL amount must be less than zero.")
        return self


class CapitalMovementResponse(ApiSchema):
    """Immutable movement returned from the simulation ledger."""

    id: UUID
    simulation_id: UUID
    type: CapitalMovementType
    amount: FinancialDecimal
    reason: NonBlankText
    reference_id: UUID | None
    created_by: UUID | None
    created_at: datetime
    metadata: JsonObject | None = None


class MovementListResponse(PaginatedResponse[CapitalMovementResponse]):
    """Chronological, paginated capital-movement history."""
