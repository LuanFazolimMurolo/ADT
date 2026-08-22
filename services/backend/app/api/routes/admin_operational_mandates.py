"""Administrator-only operational-mandate endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import get_operational_mandate_service
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.operational_mandates import (
    OperationalMandateApproveRequest,
    OperationalMandateArchiveRequest,
    OperationalMandateCreateRequest,
    OperationalMandateCurrentResponse,
    OperationalMandateListResponse,
    OperationalMandateReplaceRequest,
    OperationalMandateResponse,
    OperationalMandateRevisionListResponse,
    OperationalMandateRevisionResponse,
)
from app.operational_mandates import OperationalMandateState
from app.services import OperationalMandateService

router = APIRouter(
    prefix="/api/v1/admin/operational-mandates",
    tags=["admin operational mandates"],
    responses=ADMIN_ERROR_RESPONSES,
)


@router.get("", response_model=OperationalMandateListResponse)
async def list_operational_mandates(
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalMandateService,
        Depends(get_operational_mandate_service),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    state: Annotated[OperationalMandateState | None, Query()] = None,
) -> OperationalMandateListResponse:
    """List a bounded current-mandate page and independent filtered total."""

    response.headers["Cache-Control"] = "no-store"
    items, total = await service.list(limit=limit, offset=offset, state=state)
    return OperationalMandateListResponse.from_domain(
        items,
        limit=limit,
        offset=offset,
        total=total,
    )


@router.post(
    "",
    response_model=OperationalMandateCurrentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_operational_mandate(
    payload: OperationalMandateCreateRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalMandateService,
        Depends(get_operational_mandate_service),
    ],
) -> OperationalMandateCurrentResponse:
    """Create or replay one administrator-scoped draft intent."""

    current = await service.create(
        payload.specification.to_domain(),
        actor_id=administrator_id,
        idempotency_key=payload.idempotency_key,
    )
    return OperationalMandateCurrentResponse.from_domain(current)


@router.get(
    "/{mandate_id}",
    response_model=OperationalMandateCurrentResponse,
)
async def get_operational_mandate(
    mandate_id: UUID,
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalMandateService,
        Depends(get_operational_mandate_service),
    ],
) -> OperationalMandateCurrentResponse:
    """Return one aggregate with its exact current immutable revision."""

    response.headers["Cache-Control"] = "no-store"
    return OperationalMandateCurrentResponse.from_domain(await service.get(mandate_id))


@router.patch(
    "/{mandate_id}",
    response_model=OperationalMandateCurrentResponse,
)
async def replace_operational_mandate_draft(
    mandate_id: UUID,
    payload: OperationalMandateReplaceRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalMandateService,
        Depends(get_operational_mandate_service),
    ],
) -> OperationalMandateCurrentResponse:
    """Replace one draft while preserving both client concurrency tokens."""

    current = await service.replace_draft(
        mandate_id,
        payload.specification.to_domain(),
        expected_revision=payload.expected_revision,
        expected_record_version=payload.expected_record_version,
        actor_id=administrator_id,
    )
    return OperationalMandateCurrentResponse.from_domain(current)


@router.get(
    "/{mandate_id}/revisions",
    response_model=OperationalMandateRevisionListResponse,
)
async def list_operational_mandate_revisions(
    mandate_id: UUID,
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalMandateService,
        Depends(get_operational_mandate_service),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> OperationalMandateRevisionListResponse:
    """List bounded immutable history in repository-defined newest-first order."""

    response.headers["Cache-Control"] = "no-store"
    items, total = await service.list_revisions(
        mandate_id,
        limit=limit,
        offset=offset,
    )
    return OperationalMandateRevisionListResponse.from_domain(
        items,
        limit=limit,
        offset=offset,
        total=total,
    )


@router.get(
    "/{mandate_id}/revisions/{revision}",
    response_model=OperationalMandateRevisionResponse,
)
async def get_operational_mandate_revision(
    mandate_id: UUID,
    revision: Annotated[int, Path(ge=1)],
    response: Response,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalMandateService,
        Depends(get_operational_mandate_service),
    ],
) -> OperationalMandateRevisionResponse:
    """Return one exact immutable mandate revision."""

    response.headers["Cache-Control"] = "no-store"
    result = await service.get_revision(mandate_id, revision)
    return OperationalMandateRevisionResponse.from_domain(result)


@router.post(
    "/{mandate_id}/approve",
    response_model=OperationalMandateResponse,
)
async def approve_operational_mandate(
    mandate_id: UUID,
    payload: OperationalMandateApproveRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalMandateService,
        Depends(get_operational_mandate_service),
    ],
) -> OperationalMandateResponse:
    """Approve one exact revision, checksum, and aggregate version."""

    mandate = await service.approve(
        mandate_id,
        expected_revision=payload.expected_revision,
        expected_checksum=payload.expected_checksum,
        expected_record_version=payload.expected_record_version,
        actor_id=administrator_id,
    )
    return OperationalMandateResponse.from_domain(mandate)


@router.post(
    "/{mandate_id}/archive",
    response_model=OperationalMandateResponse,
)
async def archive_operational_mandate(
    mandate_id: UUID,
    payload: OperationalMandateArchiveRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        OperationalMandateService,
        Depends(get_operational_mandate_service),
    ],
) -> OperationalMandateResponse:
    """Archive one mandate without pre-reading or reinterpreting lifecycle state."""

    mandate = await service.archive(
        mandate_id,
        expected_record_version=payload.expected_record_version,
        actor_id=administrator_id,
    )
    return OperationalMandateResponse.from_domain(mandate)
