"""Read-only backend service tests for Phase 8-17 ML recommendations."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from app.ml.recommendations import MLStrategyRecommendation
from app.services.ml_recommendations import (
    ML_RECOMMENDATION_DEFAULT_LIMIT,
    ML_RECOMMENDATION_MAX_LIMIT,
    MLRecommendationNotFoundError,
    MLRecommendationReadLimitError,
    MLRecommendationReadService,
)
from tests.test_ml_recommendations import _recommendation


def test_empty_source_returns_empty_bounded_page() -> None:
    service = MLRecommendationReadService()

    page = service.list()

    assert page.count == 0
    assert page.items == ()
    assert ML_RECOMMENDATION_DEFAULT_LIMIT <= ML_RECOMMENDATION_MAX_LIMIT


def test_service_reads_exact_immutable_recommendation() -> None:
    recommendation = _recommendation()
    service = MLRecommendationReadService((recommendation,))

    result = service.get(recommendation.identity)

    assert result is recommendation
    assert result.identity == recommendation.identity
    assert result.checksum == recommendation.checksum


def test_list_is_deterministic_and_newest_first() -> None:
    first = _recommendation()
    second = replace(
        first,
        recommendation_time=(first.recommendation_time + timedelta(minutes=1)),
    )

    service = MLRecommendationReadService(
        (
            first,
            second,
        )
    )

    page = service.list(limit=2)

    assert page.items == (
        second,
        first,
    )

    repeated = service.list(limit=2)

    assert repeated == page


def test_list_respects_explicit_bound() -> None:
    first = _recommendation()
    second = replace(
        first,
        recommendation_time=(first.recommendation_time + timedelta(minutes=1)),
    )

    service = MLRecommendationReadService(
        (
            first,
            second,
        )
    )

    page = service.list(limit=1)

    assert page.count == 1
    assert page.items == (second,)


@pytest.mark.parametrize(
    "limit",
    (
        0,
        -1,
        ML_RECOMMENDATION_MAX_LIMIT + 1,
        True,
    ),
)
def test_invalid_read_limit_is_rejected(limit: int) -> None:
    service = MLRecommendationReadService()

    with pytest.raises(MLRecommendationReadLimitError):
        service.list(limit=limit)


def test_missing_identity_is_rejected() -> None:
    service = MLRecommendationReadService((_recommendation(),))

    with pytest.raises(MLRecommendationNotFoundError):
        service.get("f" * 64)


@pytest.mark.parametrize(
    "recommendation_id",
    (
        "",
        "x" * 129,
    ),
)
def test_invalid_identity_is_fail_closed(
    recommendation_id: str,
) -> None:
    service = MLRecommendationReadService()

    with pytest.raises(MLRecommendationNotFoundError):
        service.get(recommendation_id)


def test_duplicate_recommendation_identity_is_rejected() -> None:
    recommendation = _recommendation()

    with pytest.raises(ValueError):
        MLRecommendationReadService(
            (
                recommendation,
                recommendation,
            )
        )


def test_service_accepts_only_recommendation_contracts() -> None:
    with pytest.raises(TypeError):
        MLRecommendationReadService(
            ("not-a-recommendation",)  # type: ignore[arg-type]
        )


def test_service_exposes_no_mutating_authority_surface() -> None:
    service = MLRecommendationReadService((_recommendation(),))

    forbidden = (
        "approve",
        "activate",
        "promote",
        "deploy",
        "execute",
        "order",
        "start",
        "stop",
        "revoke",
        "paper",
        "live",
    )

    public_names = tuple(name.lower() for name in dir(service) if not name.startswith("_"))

    for token in forbidden:
        assert not any(token in name for name in public_names)


def test_page_contains_domain_recommendations_only() -> None:
    recommendation = _recommendation()

    page = MLRecommendationReadService((recommendation,)).list()

    assert page.count == 1
    assert all(type(item) is MLStrategyRecommendation for item in page.items)
