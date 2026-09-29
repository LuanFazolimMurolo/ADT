"""Application service for human PAPER recommendation approval evidence.

This layer records a human decision over exact immutable ML recommendation
evidence. It deliberately has no dependency on PAPER materialization,
activation, run orchestration, trading execution, or LIVE authority.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.ml.recommendations import MLStrategyRecommendation
from app.paper_recommendation_approvals import (
    PaperRecommendationApproval,
    PaperRecommendationApprovalIntent,
)


class PaperRecommendationApprovalServiceError(ValueError):
    """Base application error for recommendation approvals."""


class PaperRecommendationApprovalNotFoundError(PaperRecommendationApprovalServiceError):
    """Raised when an approval does not exist."""


class PaperRecommendationApprovalIdempotencyConflictError(PaperRecommendationApprovalServiceError):
    """Raised when an idempotency key is reused for another intent."""


class PaperRecommendationApprovalRecordVersionConflictError(
    PaperRecommendationApprovalServiceError
):
    """Raised when revoke uses a stale record version."""


class MLRecommendationApprovalSource(Protocol):
    def get(self, recommendation_id: str) -> MLStrategyRecommendation:
        """Return one immutable recommendation by identity."""


class PaperRecommendationApprovalRepository(Protocol):
    async def get(
        self,
        approval_id: UUID,
    ) -> PaperRecommendationApproval | None:
        """Read one approval."""

    async def create(
        self,
        intent: PaperRecommendationApprovalIntent,
        *,
        approval_id: UUID,
        actor_id: UUID,
        idempotency_key: str,
        now: datetime,
    ) -> PaperRecommendationApproval:
        """Persist or replay the exact approval intent."""

    async def revoke(
        self,
        approval_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
    ) -> PaperRecommendationApproval:
        """Revoke an approval using optimistic concurrency."""


class PaperRecommendationApprovalService:
    """Bind human approval exclusively to server-read recommendation evidence."""

    def __init__(
        self,
        *,
        recommendation_source: MLRecommendationApprovalSource,
        repository: PaperRecommendationApprovalRepository,
        clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._recommendation_source = recommendation_source
        self._repository = repository
        self._clock = clock
        self._uuid_factory = uuid_factory

    async def get(
        self,
        approval_id: UUID,
    ) -> PaperRecommendationApproval:
        approval = await self._repository.get(approval_id)
        if approval is None:
            raise PaperRecommendationApprovalNotFoundError()
        return approval

    async def approve(
        self,
        recommendation_id: str,
        *,
        actor_id: UUID,
        idempotency_key: str,
    ) -> PaperRecommendationApproval:
        recommendation = self._recommendation_source.get(recommendation_id)

        intent = PaperRecommendationApprovalIntent(
            recommendation_identity=recommendation.identity,
            recommendation_checksum=recommendation.checksum,
            candidate_identity=recommendation.candidate.identity,
            candidate_checksum=recommendation.candidate.checksum,
            configuration_checksum=recommendation.candidate.configuration_checksum,
            parameters_checksum=recommendation.candidate.parameters_checksum,
        )

        return await self._repository.create(
            intent,
            approval_id=self._uuid_factory(),
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            now=self._clock(),
        )

    async def revoke(
        self,
        approval_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
    ) -> PaperRecommendationApproval:
        return await self._repository.revoke(
            approval_id,
            expected_record_version=expected_record_version,
            actor_id=actor_id,
            now=self._clock(),
        )
