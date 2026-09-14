"""Administrator HTTP contracts for operational paper-session settlement."""

from __future__ import annotations

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import Field

from app.api.schemas.common import ApiSchema
from app.operational_paper_session_settlements import (
    MAX_OPERATIONAL_PAPER_SESSION_SETTLEMENT_IDEMPOTENCY_KEY_LENGTH,
    OperationalPaperSessionSettlement,
    OperationalPaperSessionSettlementIntent,
)

_IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class OperationalPaperSessionSettlementRequest(ApiSchema):
    """Request settlement by immutable run identity only."""

    epoch_id: UUID
    epoch_checksum: str = Field(
        strict=True,
        min_length=64,
        max_length=64,
        pattern=_SHA256_PATTERN,
    )
    idempotency_key: str = Field(
        strict=True,
        min_length=1,
        max_length=MAX_OPERATIONAL_PAPER_SESSION_SETTLEMENT_IDEMPOTENCY_KEY_LENGTH,
        pattern=_IDEMPOTENCY_KEY_PATTERN,
    )

    def to_domain(self) -> OperationalPaperSessionSettlementIntent:
        return OperationalPaperSessionSettlementIntent(
            epoch_id=self.epoch_id,
            epoch_checksum=self.epoch_checksum,
        )


class OperationalPaperSessionSettlementResponse(ApiSchema):
    """Immutable settlement identity returned after backend verification."""

    settlement_id: UUID
    settlement_checksum: str
    settled_by: UUID
    settled_at: datetime

    @classmethod
    def from_domain(
        cls,
        settlement: OperationalPaperSessionSettlement,
    ) -> Self:
        return cls(
            settlement_id=settlement.settlement_id,
            settlement_checksum=settlement.settlement_checksum,
            settled_by=settlement.settled_by,
            settled_at=settlement.settled_at,
        )
