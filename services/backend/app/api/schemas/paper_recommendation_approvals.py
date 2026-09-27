"""HTTP contracts for human PAPER recommendation approval evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.paper_recommendation_approvals import PaperRecommendationApproval


class PaperRecommendationApprovalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


class PaperRecommendationApprovalRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_record_version: int = Field(ge=1)


class PaperRecommendationApprovalResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    approval_id: UUID
    state: Literal["APPROVED", "REVOKED"]
    record_version: int

    recommendation_identity: str
    recommendation_checksum: str

    candidate_identity: str
    candidate_checksum: str
    configuration_checksum: str
    parameters_checksum: str

    approval_checksum: str

    approved_by: UUID
    approved_at: datetime
    create_idempotency_key: str
    create_intent_fingerprint: str

    revoked_by: UUID | None
    revoked_at: datetime | None

    @classmethod
    def from_domain(
        cls,
        value: PaperRecommendationApproval,
    ) -> PaperRecommendationApprovalResponse:
        return cls(
            approval_id=value.approval_id,
            state=value.state.value,
            record_version=value.record_version,
            recommendation_identity=value.recommendation_identity,
            recommendation_checksum=value.recommendation_checksum,
            candidate_identity=value.candidate_identity,
            candidate_checksum=value.candidate_checksum,
            configuration_checksum=value.configuration_checksum,
            parameters_checksum=value.parameters_checksum,
            approval_checksum=value.approval_checksum,
            approved_by=value.approved_by,
            approved_at=value.approved_at,
            create_idempotency_key=value.create_idempotency_key,
            create_intent_fingerprint=value.create_intent_fingerprint,
            revoked_by=value.revoked_by,
            revoked_at=value.revoked_at,
        )
