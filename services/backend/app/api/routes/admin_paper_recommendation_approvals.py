"""Administrator API for human PAPER recommendation approval evidence."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.paper_recommendation_approvals import (
    get_paper_recommendation_approval_service,
)
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.paper_recommendation_approvals import (
    PaperRecommendationApprovalCreateRequest,
    PaperRecommendationApprovalResponse,
    PaperRecommendationApprovalRevokeRequest,
)
from app.paper_recommendation_approvals import (
    InvalidPaperRecommendationApprovalError,
    PaperRecommendationApprovalStateTransitionError,
)
from app.services.ml_recommendations import MLRecommendationNotFoundError
from app.services.paper_recommendation_approvals import (
    PaperRecommendationApprovalIdempotencyConflictError,
    PaperRecommendationApprovalNotFoundError,
    PaperRecommendationApprovalRecordVersionConflictError,
    PaperRecommendationApprovalService,
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


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.post(
    "/recommendations/{recommendation_id}/paper-approvals",
    response_model=PaperRecommendationApprovalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def approve_paper_recommendation(
    recommendation_id: RecommendationId,
    payload: PaperRecommendationApprovalCreateRequest,
    response: Response,
    administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        PaperRecommendationApprovalService,
        Depends(get_paper_recommendation_approval_service),
    ],
) -> PaperRecommendationApprovalResponse:
    """Record human approval over exact immutable recommendation evidence."""

    _no_store(response)

    try:
        approval = await service.approve(
            recommendation_id,
            actor_id=administrator_id,
            idempotency_key=payload.idempotency_key,
        )
    except MLRecommendationNotFoundError:
        raise HTTPException(
            headers={"Cache-Control": "no-store"},
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "ml_recommendation_not_found",
                "message": "ML recommendation not found.",
            },
        ) from None
    except PaperRecommendationApprovalIdempotencyConflictError:
        raise HTTPException(
            headers={"Cache-Control": "no-store"},
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "paper_recommendation_approval_idempotency_conflict",
                "message": "Idempotency key conflicts with another approval intent.",
            },
        ) from None
    except InvalidPaperRecommendationApprovalError:
        raise HTTPException(
            headers={"Cache-Control": "no-store"},
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_paper_recommendation_approval",
                "message": "PAPER recommendation approval is invalid.",
            },
        ) from None

    return PaperRecommendationApprovalResponse.from_domain(approval)


@router.get(
    "/paper-recommendation-approvals/{approval_id}",
    response_model=PaperRecommendationApprovalResponse,
)
async def get_paper_recommendation_approval(
    approval_id: UUID,
    response: Response,
    _administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        PaperRecommendationApprovalService,
        Depends(get_paper_recommendation_approval_service),
    ],
) -> PaperRecommendationApprovalResponse:
    """Read one persisted approval evidence record."""

    _no_store(response)

    try:
        approval = await service.get(approval_id)
    except PaperRecommendationApprovalNotFoundError:
        raise HTTPException(
            headers={"Cache-Control": "no-store"},
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "paper_recommendation_approval_not_found",
                "message": "PAPER recommendation approval not found.",
            },
        ) from None

    return PaperRecommendationApprovalResponse.from_domain(approval)


@router.post(
    "/paper-recommendation-approvals/{approval_id}/revoke",
    response_model=PaperRecommendationApprovalResponse,
)
async def revoke_paper_recommendation_approval(
    approval_id: UUID,
    payload: PaperRecommendationApprovalRevokeRequest,
    response: Response,
    administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        PaperRecommendationApprovalService,
        Depends(get_paper_recommendation_approval_service),
    ],
) -> PaperRecommendationApprovalResponse:
    """Revoke approval evidence without triggering any PAPER operation."""

    _no_store(response)

    try:
        approval = await service.revoke(
            approval_id,
            expected_record_version=payload.expected_record_version,
            actor_id=administrator_id,
        )
    except PaperRecommendationApprovalNotFoundError:
        raise HTTPException(
            headers={"Cache-Control": "no-store"},
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "paper_recommendation_approval_not_found",
                "message": "PAPER recommendation approval not found.",
            },
        ) from None
    except PaperRecommendationApprovalRecordVersionConflictError:
        raise HTTPException(
            headers={"Cache-Control": "no-store"},
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "paper_recommendation_approval_record_version_conflict",
                "message": "PAPER recommendation approval version conflict.",
            },
        ) from None
    except PaperRecommendationApprovalStateTransitionError:
        raise HTTPException(
            headers={"Cache-Control": "no-store"},
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "paper_recommendation_approval_state_conflict",
                "message": "PAPER recommendation approval state transition conflict.",
            },
        ) from None

    return PaperRecommendationApprovalResponse.from_domain(approval)
