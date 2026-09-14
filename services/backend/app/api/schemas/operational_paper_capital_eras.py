"""Administrator HTTP contracts for official operational paper-capital eras."""

from __future__ import annotations

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import Field

from app.api.schemas.common import ApiSchema, FinancialDecimal
from app.operational_paper_capital_eras import (
    MAX_OPERATIONAL_PAPER_CAPITAL_ERA_IDEMPOTENCY_KEY_LENGTH,
    OperationalPaperCapitalEra,
    OperationalPaperCapitalEraDesignationIntent,
)

_IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"


class OperationalPaperCapitalEraDesignationRequest(ApiSchema):
    """Designate one simulation as an official paper-capital era."""

    simulation_id: UUID
    idempotency_key: str = Field(
        strict=True,
        min_length=1,
        max_length=MAX_OPERATIONAL_PAPER_CAPITAL_ERA_IDEMPOTENCY_KEY_LENGTH,
        pattern=_IDEMPOTENCY_KEY_PATTERN,
    )

    def to_domain(self) -> OperationalPaperCapitalEraDesignationIntent:
        return OperationalPaperCapitalEraDesignationIntent(
            simulation_id=self.simulation_id,
        )


class OperationalPaperCapitalEraResponse(ApiSchema):
    """Auditable official-era identity without replay internals."""

    era_id: UUID
    schema_version: int
    designation_contract_version: int
    simulation_id: UUID
    currency: str
    initial_capital: FinancialDecimal
    simulation_started_at: datetime
    era_checksum: str
    designated_by: UUID
    designated_at: datetime

    @classmethod
    def from_domain(
        cls,
        era: OperationalPaperCapitalEra,
    ) -> Self:
        return cls(
            era_id=era.era_id,
            schema_version=era.schema_version,
            designation_contract_version=era.designation_contract_version,
            simulation_id=era.simulation_id,
            currency=era.currency,
            initial_capital=era.initial_capital,
            simulation_started_at=era.simulation_started_at,
            era_checksum=era.era_checksum,
            designated_by=era.designated_by,
            designated_at=era.designated_at,
        )
