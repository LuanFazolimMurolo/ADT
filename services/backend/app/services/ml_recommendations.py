"""Read-only application service for ML recommendation evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.ml.recommendations import MLStrategyRecommendation

ML_RECOMMENDATION_DEFAULT_LIMIT: Final = 50
ML_RECOMMENDATION_MAX_LIMIT: Final = 100


class MLRecommendationReadError(ValueError):
    """Base error for bounded recommendation reads."""


class MLRecommendationNotFoundError(MLRecommendationReadError):
    """Requested recommendation does not exist."""


class MLRecommendationReadLimitError(MLRecommendationReadError):
    """Requested recommendation page exceeds the read contract."""


@dataclass(frozen=True, slots=True)
class MLRecommendationPage:
    """Bounded deterministic recommendation page."""

    items: tuple[MLStrategyRecommendation, ...]

    @property
    def count(self) -> int:
        return len(self.items)


class MLRecommendationReadService:
    """Read immutable recommendation evidence through a bounded read-only surface."""

    def __init__(
        self,
        recommendations: tuple[MLStrategyRecommendation, ...] = (),
    ) -> None:
        identities: set[str] = set()

        for recommendation in recommendations:
            if type(recommendation) is not MLStrategyRecommendation:
                raise TypeError("recommendations must contain MLStrategyRecommendation")

            if recommendation.identity in identities:
                raise ValueError("duplicate recommendation identity")

            identities.add(recommendation.identity)

        self._recommendations = tuple(
            sorted(
                recommendations,
                key=lambda recommendation: (
                    recommendation.recommendation_time,
                    recommendation.identity,
                ),
                reverse=True,
            )
        )

    def list(
        self,
        *,
        limit: int = ML_RECOMMENDATION_DEFAULT_LIMIT,
    ) -> MLRecommendationPage:
        """Return a deterministic bounded recommendation page."""

        _require_limit(limit)

        return MLRecommendationPage(
            items=self._recommendations[:limit],
        )

    def get(
        self,
        recommendation_id: str,
    ) -> MLStrategyRecommendation:
        """Read one recommendation by its immutable identity."""

        if (
            not isinstance(recommendation_id, str)
            or not recommendation_id
            or len(recommendation_id) > 128
        ):
            raise MLRecommendationNotFoundError()

        for recommendation in self._recommendations:
            if recommendation.identity == recommendation_id:
                return recommendation

        raise MLRecommendationNotFoundError()


def _require_limit(limit: int) -> None:
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= ML_RECOMMENDATION_MAX_LIMIT
    ):
        raise MLRecommendationReadLimitError()
