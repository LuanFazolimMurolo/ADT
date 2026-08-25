"""Administrator-only operational paper-session profile endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import get_operational_paper_session_profile_service
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.operational_paper_session_profiles import (
    OperationalPaperSessionProfileApproveRequest,
    OperationalPaperSessionProfileArchiveRequest,
    OperationalPaperSessionProfileCreateRequest,
    OperationalPaperSessionProfileCurrentResponse,
    OperationalPaperSessionProfileListResponse,
    OperationalPaperSessionProfileReplaceRequest,
    OperationalPaperSessionProfileResponse,
    OperationalPaperSessionProfileRevisionListResponse,
    OperationalPaperSessionProfileRevisionResponse,
)
from app.operational_paper_session_profiles import OperationalPaperSessionProfileState
from app.services import OperationalPaperSessionProfileService

router = APIRouter(
    prefix="/api/v1/admin/operational-paper-session-profiles",
    tags=["admin operational paper-session profiles"],
    responses=ADMIN_ERROR_RESPONSES,
)


@router.get("", response_model=OperationalPaperSessionProfileListResponse)
async def list_operational_paper_session_profiles(
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperSessionProfileService,
        Depends(get_operational_paper_session_profile_service),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    state: Annotated[OperationalPaperSessionProfileState | None, Query()] = None,
) -> OperationalPaperSessionProfileListResponse:
    """List a bounded current-profile page and independent filtered total."""

    response.headers["Cache-Control"] = "no-store"
    items, total = await service.list(limit=limit, offset=offset, state=state)
    return OperationalPaperSessionProfileListResponse.from_domain(
        items,
        limit=limit,
        offset=offset,
        total=total,
    )


@router.post(
    "",
    response_model=OperationalPaperSessionProfileCurrentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_operational_paper_session_profile(
    payload: OperationalPaperSessionProfileCreateRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperSessionProfileService,
        Depends(get_operational_paper_session_profile_service),
    ],
) -> OperationalPaperSessionProfileCurrentResponse:
    """Create or replay one administrator-scoped profile draft intent."""

    current = await service.create(
        payload.intent.to_domain(),
        actor_id=administrator_id,
        idempotency_key=payload.idempotency_key,
    )
    return OperationalPaperSessionProfileCurrentResponse.from_domain(current)


@router.get(
    "/{profile_id}",
    response_model=OperationalPaperSessionProfileCurrentResponse,
)
async def get_operational_paper_session_profile(
    profile_id: UUID,
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperSessionProfileService,
        Depends(get_operational_paper_session_profile_service),
    ],
) -> OperationalPaperSessionProfileCurrentResponse:
    """Return one profile with its exact current immutable revision."""

    response.headers["Cache-Control"] = "no-store"
    return OperationalPaperSessionProfileCurrentResponse.from_domain(await service.get(profile_id))


@router.patch(
    "/{profile_id}",
    response_model=OperationalPaperSessionProfileCurrentResponse,
)
async def replace_operational_paper_session_profile_draft(
    profile_id: UUID,
    payload: OperationalPaperSessionProfileReplaceRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperSessionProfileService,
        Depends(get_operational_paper_session_profile_service),
    ],
) -> OperationalPaperSessionProfileCurrentResponse:
    """Replace one draft while preserving both client concurrency tokens."""

    current = await service.replace_draft(
        profile_id,
        payload.intent.to_domain(),
        expected_revision=payload.expected_revision,
        expected_record_version=payload.expected_record_version,
        actor_id=administrator_id,
    )
    return OperationalPaperSessionProfileCurrentResponse.from_domain(current)


@router.get(
    "/{profile_id}/revisions",
    response_model=OperationalPaperSessionProfileRevisionListResponse,
)
async def list_operational_paper_session_profile_revisions(
    profile_id: UUID,
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperSessionProfileService,
        Depends(get_operational_paper_session_profile_service),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> OperationalPaperSessionProfileRevisionListResponse:
    """List bounded immutable history in repository-defined newest-first order."""

    response.headers["Cache-Control"] = "no-store"
    items, total = await service.list_revisions(
        profile_id,
        limit=limit,
        offset=offset,
    )
    return OperationalPaperSessionProfileRevisionListResponse.from_domain(
        items,
        limit=limit,
        offset=offset,
        total=total,
    )


@router.get(
    "/{profile_id}/revisions/{revision}",
    response_model=OperationalPaperSessionProfileRevisionResponse,
)
async def get_operational_paper_session_profile_revision(
    profile_id: UUID,
    revision: Annotated[int, Path(ge=1)],
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperSessionProfileService,
        Depends(get_operational_paper_session_profile_service),
    ],
) -> OperationalPaperSessionProfileRevisionResponse:
    """Return one exact immutable profile revision."""

    response.headers["Cache-Control"] = "no-store"
    result = await service.get_revision(profile_id, revision)
    return OperationalPaperSessionProfileRevisionResponse.from_domain(result)


@router.post(
    "/{profile_id}/approve",
    response_model=OperationalPaperSessionProfileResponse,
)
async def approve_operational_paper_session_profile(
    profile_id: UUID,
    payload: OperationalPaperSessionProfileApproveRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperSessionProfileService,
        Depends(get_operational_paper_session_profile_service),
    ],
) -> OperationalPaperSessionProfileResponse:
    """Approve one exact revision, checksum, and aggregate version."""

    profile = await service.approve(
        profile_id,
        expected_revision=payload.expected_revision,
        expected_checksum=payload.expected_checksum,
        expected_record_version=payload.expected_record_version,
        actor_id=administrator_id,
    )
    return OperationalPaperSessionProfileResponse.from_domain(profile)


@router.post(
    "/{profile_id}/archive",
    response_model=OperationalPaperSessionProfileResponse,
)
async def archive_operational_paper_session_profile(
    profile_id: UUID,
    payload: OperationalPaperSessionProfileArchiveRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalPaperSessionProfileService,
        Depends(get_operational_paper_session_profile_service),
    ],
) -> OperationalPaperSessionProfileResponse:
    """Archive one profile without pre-reading or interpreting lifecycle state."""

    profile = await service.archive(
        profile_id,
        expected_record_version=payload.expected_record_version,
        actor_id=administrator_id,
    )
    return OperationalPaperSessionProfileResponse.from_domain(profile)
