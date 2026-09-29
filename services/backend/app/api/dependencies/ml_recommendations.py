"""Dependency provider for read-only ML recommendation evidence."""

from __future__ import annotations

from functools import lru_cache

from app.services.ml_recommendations import MLRecommendationReadService


@lru_cache(maxsize=1)
def get_ml_recommendation_read_service() -> MLRecommendationReadService:
    """Return the default explicit empty read source.

    Phase 8-17 introduces no persistence or automatic recommendation generation.
    Tests and future storage integrations may override this dependency explicitly.
    """

    return MLRecommendationReadService()
