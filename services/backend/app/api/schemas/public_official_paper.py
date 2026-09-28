"""Sanitized HTTP contract for the public official PAPER projection."""

from datetime import datetime
from typing import Literal

from app.api.schemas.common import ApiSchema
from app.services.public_official_paper import (
    PUBLIC_OFFICIAL_PAPER_LIMIT,
    PublicOfficialPaperItem,
    PublicOfficialPaperSnapshot,
)

PublicOfficialPaperAvailabilityValue = Literal[
    "RUNNING",
    "CONTROLLED_INACTIVE",
    "CONVERGING",
    "DEGRADED",
    "FAILED",
]


class PublicOfficialPaperItemResponse(ApiSchema):
    """One minimized current official PAPER operational projection."""

    session_id: str
    desired_state: str
    observed_state: str
    availability: PublicOfficialPaperAvailabilityValue
    ownership_fresh: bool | None
    heartbeat_at: datetime | None
    lease_expires_at: datetime | None
    failure_code: str | None

    @classmethod
    def from_domain(
        cls,
        value: PublicOfficialPaperItem,
    ) -> "PublicOfficialPaperItemResponse":
        return cls(
            session_id=value.session_id,
            desired_state=value.desired_state,
            observed_state=value.observed_state,
            availability=value.availability.value,
            ownership_fresh=value.ownership_fresh,
            heartbeat_at=value.heartbeat_at,
            lease_expires_at=value.lease_expires_at,
            failure_code=value.failure_code,
        )


class PublicOfficialPaperSnapshotResponse(ApiSchema):
    """Fixed-size snapshot; never an unbounded operational history."""

    observed_at: datetime
    limit: Literal[20] = PUBLIC_OFFICIAL_PAPER_LIMIT
    count: int
    items: list[PublicOfficialPaperItemResponse]

    @classmethod
    def from_domain(
        cls,
        value: PublicOfficialPaperSnapshot,
    ) -> "PublicOfficialPaperSnapshotResponse":
        items = [PublicOfficialPaperItemResponse.from_domain(item) for item in value.items]

        return cls(
            observed_at=value.observed_at,
            limit=PUBLIC_OFFICIAL_PAPER_LIMIT,
            count=len(items),
            items=items,
        )
