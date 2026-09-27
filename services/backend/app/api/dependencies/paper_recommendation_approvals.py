"""Dependency composition for PAPER recommendation approval evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends

from app.api.dependencies.ml_recommendations import (
    get_ml_recommendation_read_service,
)
from app.api.dependencies.resources import get_database
from app.database.pool import Database
from app.repositories.paper_recommendation_approvals import (
    PostgresPaperRecommendationApprovalRepository,
)
from app.services.ml_recommendations import MLRecommendationReadService
from app.services.paper_recommendation_approvals import (
    PaperRecommendationApprovalService,
)


def _approval_clock() -> datetime:
    return datetime.now(UTC)


def get_paper_recommendation_approval_service(
    database: Annotated[Database, Depends(get_database)],
    recommendation_source: Annotated[
        MLRecommendationReadService,
        Depends(get_ml_recommendation_read_service),
    ],
) -> PaperRecommendationApprovalService:
    return PaperRecommendationApprovalService(
        recommendation_source=recommendation_source,
        repository=PostgresPaperRecommendationApprovalRepository(database),
        clock=_approval_clock,
    )
