"""Administrator-only read API for immutable ML recommendation evidence."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.ml_recommendations import (
    get_ml_recommendation_read_service,
)
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.ml_recommendations import (
    MLRecommendationPageResponse,
    MLRecommendationResponse,
)
from app.services.ml_recommendations import (
    ML_RECOMMENDATION_DEFAULT_LIMIT,
    ML_RECOMMENDATION_MAX_LIMIT,
    MLRecommendationNotFoundError,
    MLRecommendationReadService,
)

router = APIRouter(
    prefix="/api/v1/admin/ml",
    tags=["admin ml"],
    responses=ADMIN_ERROR_RESPONSES,
)

RecommendationId = Annotated[
    str,
    Path(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    ),
]


@router.get(
    "/recommendations",
    response_model=MLRecommendationPageResponse,
)
def list_ml_recommendations(
    response: Response,
    _administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        MLRecommendationReadService,
        Depends(get_ml_recommendation_read_service),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=ML_RECOMMENDATION_MAX_LIMIT),
    ] = ML_RECOMMENDATION_DEFAULT_LIMIT,
) -> MLRecommendationPageResponse:
    """List bounded immutable recommendation evidence."""

    response.headers["Cache-Control"] = "no-store"

    return MLRecommendationPageResponse.from_domain(service.list(limit=limit))


@router.get(
    "/recommendations/{recommendation_id}",
    response_model=MLRecommendationResponse,
)
def get_ml_recommendation(
    response: Response,
    _administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        MLRecommendationReadService,
        Depends(get_ml_recommendation_read_service),
    ],
    recommendation_id: RecommendationId,
) -> MLRecommendationResponse:
    """Read one immutable recommendation evidence document."""

    response.headers["Cache-Control"] = "no-store"

    try:
        recommendation = service.get(recommendation_id)
    except MLRecommendationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "ml_recommendation_not_found",
                "message": "ML recommendation not found.",
            },
        ) from None

    return MLRecommendationResponse.from_domain(recommendation)
