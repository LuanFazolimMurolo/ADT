"""Deterministic in-memory repository for PAPER recommendation approvals.

This implementation proves repository semantics without establishing any
production persistence or operational PAPER authority.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.paper_recommendation_approvals import (
    PaperRecommendationApproval,
    PaperRecommendationApprovalIntent,
    approve_paper_recommendation,
    paper_recommendation_approval_intent_fingerprint,
    revoke_paper_recommendation_approval,
)
from app.services.paper_recommendation_approvals import (
    PaperRecommendationApprovalIdempotencyConflictError,
    PaperRecommendationApprovalNotFoundError,
    PaperRecommendationApprovalRecordVersionConflictError,
)


class InMemoryPaperRecommendationApprovalRepository:
    """Reference implementation for idempotency and optimistic concurrency."""

    def __init__(self) -> None:
        self._approvals: dict[UUID, PaperRecommendationApproval] = {}
        self._idempotency: dict[
            tuple[UUID, str],
            UUID,
        ] = {}

    async def get(
        self,
        approval_id: UUID,
    ) -> PaperRecommendationApproval | None:
        return self._approvals.get(approval_id)

    async def create(
        self,
        intent: PaperRecommendationApprovalIntent,
        *,
        approval_id: UUID,
        actor_id: UUID,
        idempotency_key: str,
        now: datetime,
    ) -> PaperRecommendationApproval:
        fingerprint = paper_recommendation_approval_intent_fingerprint(intent)
        idempotency_identity = (actor_id, idempotency_key)

        existing_id = self._idempotency.get(idempotency_identity)
        if existing_id is not None:
            existing = self._approvals[existing_id]

            if existing.create_intent_fingerprint != fingerprint:
                raise PaperRecommendationApprovalIdempotencyConflictError()

            return existing

        approval = approve_paper_recommendation(
            intent,
            approval_id=approval_id,
            actor_id=actor_id,
            approved_at=now,
            idempotency_key=idempotency_key,
        )

        self._approvals[approval.approval_id] = approval
        self._idempotency[idempotency_identity] = approval.approval_id

        return approval

    async def revoke(
        self,
        approval_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
    ) -> PaperRecommendationApproval:
        current = self._approvals.get(approval_id)

        if current is None:
            raise PaperRecommendationApprovalNotFoundError()

        if (
            type(expected_record_version) is not int
            or current.record_version != expected_record_version
        ):
            raise PaperRecommendationApprovalRecordVersionConflictError()

        revoked = revoke_paper_recommendation_approval(
            current,
            actor_id=actor_id,
            revoked_at=now,
        )

        self._approvals[approval_id] = revoked
        return revoked
