"""Administrator-only operational paper-session run-control endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

import app.operational_paper_session_runs as runs
from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_operational_paper_session_run_service,
)
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.operational_paper_session_runs import (
    OperationalPaperSessionRunCommandListResponse,
    OperationalPaperSessionRunCommandRequest,
    OperationalPaperSessionRunCommandResponse,
    OperationalPaperSessionRunEpochResponse,
    OperationalPaperSessionRunStartRequest,
)
from app.services.operational_paper_session_runs import (
    OperationalPaperSessionRunService,
)

router = APIRouter(
    prefix="/api/v1/admin/operational-paper-session-runs",
    tags=["admin operational paper-session runs"],
    responses=ADMIN_ERROR_RESPONSES,
)


@router.post(
    "/start",
    response_model=OperationalPaperSessionRunEpochResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_operational_paper_session_run(
    payload: OperationalPaperSessionRunStartRequest,
    administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        OperationalPaperSessionRunService,
        Depends(get_operational_paper_session_run_service),
    ],
) -> OperationalPaperSessionRunEpochResponse:
    """Persist or replay START without launching physical execution."""

    epoch = await service.start(
        runs.OperationalPaperSessionRunEpochStartIntent(
            activation_id=payload.activation_id,
            activation_checksum=payload.activation_checksum,
        ),
        actor_id=administrator_id,
        idempotency_key=payload.idempotency_key,
        requested_at=datetime.now(UTC),
    )
    return OperationalPaperSessionRunEpochResponse.from_domain(epoch)


@router.get(
    "/{epoch_id}",
    response_model=OperationalPaperSessionRunEpochResponse,
)
async def get_operational_paper_session_run(
    epoch_id: UUID,
    response: Response,
    _administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        OperationalPaperSessionRunService,
        Depends(get_operational_paper_session_run_service),
    ],
) -> OperationalPaperSessionRunEpochResponse:
    """Return one exact persisted run epoch."""

    response.headers["Cache-Control"] = "no-store"
    return OperationalPaperSessionRunEpochResponse.from_domain(await service.get(epoch_id))


@router.get(
    "/{epoch_id}/commands",
    response_model=OperationalPaperSessionRunCommandListResponse,
)
async def list_operational_paper_session_run_commands(
    epoch_id: UUID,
    response: Response,
    _administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        OperationalPaperSessionRunService,
        Depends(get_operational_paper_session_run_service),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[
        int,
        Query(ge=0, le=1_000_000),
    ] = 0,
) -> OperationalPaperSessionRunCommandListResponse:
    """Return bounded immutable command history."""

    response.headers["Cache-Control"] = "no-store"

    items = await service.list_commands(
        epoch_id,
        limit=limit,
        offset=offset,
    )

    return OperationalPaperSessionRunCommandListResponse.from_domain(
        items,
        limit=limit,
        offset=offset,
    )


async def _request_command(
    *,
    epoch_id: UUID,
    payload: OperationalPaperSessionRunCommandRequest,
    administrator_id: UUID,
    service: OperationalPaperSessionRunService,
    command_type: runs.OperationalPaperSessionRunCommandType,
) -> OperationalPaperSessionRunCommandResponse:
    intent = runs.OperationalPaperSessionRunEpochCommandIntent(
        epoch_id=epoch_id,
        epoch_checksum=payload.epoch_checksum,
        command_type=command_type,
        expected_record_version=payload.expected_record_version,
    )

    if command_type is runs.OperationalPaperSessionRunCommandType.PAUSE:
        command = await service.pause(
            intent,
            actor_id=administrator_id,
            idempotency_key=payload.idempotency_key,
            requested_at=datetime.now(UTC),
        )
    elif command_type is runs.OperationalPaperSessionRunCommandType.RESUME:
        command = await service.resume(
            intent,
            actor_id=administrator_id,
            idempotency_key=payload.idempotency_key,
            requested_at=datetime.now(UTC),
        )
    else:
        command = await service.stop(
            intent,
            actor_id=administrator_id,
            idempotency_key=payload.idempotency_key,
            requested_at=datetime.now(UTC),
        )

    return OperationalPaperSessionRunCommandResponse.from_domain(command)


@router.post(
    "/{epoch_id}/pause",
    response_model=OperationalPaperSessionRunCommandResponse,
)
async def pause_operational_paper_session_run(
    epoch_id: UUID,
    payload: OperationalPaperSessionRunCommandRequest,
    administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        OperationalPaperSessionRunService,
        Depends(get_operational_paper_session_run_service),
    ],
) -> OperationalPaperSessionRunCommandResponse:
    """Persist PAUSE; the supervisor owns physical convergence."""

    return await _request_command(
        epoch_id=epoch_id,
        payload=payload,
        administrator_id=administrator_id,
        service=service,
        command_type=(runs.OperationalPaperSessionRunCommandType.PAUSE),
    )


@router.post(
    "/{epoch_id}/resume",
    response_model=OperationalPaperSessionRunCommandResponse,
)
async def resume_operational_paper_session_run(
    epoch_id: UUID,
    payload: OperationalPaperSessionRunCommandRequest,
    administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        OperationalPaperSessionRunService,
        Depends(get_operational_paper_session_run_service),
    ],
) -> OperationalPaperSessionRunCommandResponse:
    """Persist RESUME after application-layer eligibility checks."""

    return await _request_command(
        epoch_id=epoch_id,
        payload=payload,
        administrator_id=administrator_id,
        service=service,
        command_type=(runs.OperationalPaperSessionRunCommandType.RESUME),
    )


@router.post(
    "/{epoch_id}/stop",
    response_model=OperationalPaperSessionRunCommandResponse,
)
async def stop_operational_paper_session_run(
    epoch_id: UUID,
    payload: OperationalPaperSessionRunCommandRequest,
    administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        OperationalPaperSessionRunService,
        Depends(get_operational_paper_session_run_service),
    ],
) -> OperationalPaperSessionRunCommandResponse:
    """Persist STOP without waiting for physical convergence."""

    return await _request_command(
        epoch_id=epoch_id,
        payload=payload,
        administrator_id=administrator_id,
        service=service,
        command_type=(runs.OperationalPaperSessionRunCommandType.STOP),
    )
