"""Administrator-only operational market-data collector endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

import app.operational_market_data_collectors as collectors
from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_operational_market_data_collector_service,
)
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.operational_market_data_collectors import (
    OperationalMarketDataCollectorCommandListResponse,
    OperationalMarketDataCollectorCommandRequest,
    OperationalMarketDataCollectorCommandResponse,
    OperationalMarketDataCollectorEpochResponse,
    OperationalMarketDataCollectorStartRequest,
)
from app.services.operational_market_data_collectors import (
    OperationalMarketDataCollectorService,
)

router = APIRouter(
    prefix="/api/v1/admin/operational-market-data-collectors",
    tags=["admin operational market-data collectors"],
    responses=ADMIN_ERROR_RESPONSES,
)


@router.post(
    "/start",
    response_model=OperationalMarketDataCollectorEpochResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_operational_market_data_collector(
    payload: OperationalMarketDataCollectorStartRequest,
    administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        OperationalMarketDataCollectorService,
        Depends(get_operational_market_data_collector_service),
    ],
) -> OperationalMarketDataCollectorEpochResponse:
    """Persist or replay START without launching physical collection."""

    epoch = await service.start(
        payload.specification.to_domain(),
        actor_id=administrator_id,
        idempotency_key=payload.idempotency_key,
        requested_at=datetime.now(UTC),
    )

    return OperationalMarketDataCollectorEpochResponse.from_domain(epoch)


@router.get(
    "/{epoch_id}",
    response_model=OperationalMarketDataCollectorEpochResponse,
)
async def get_operational_market_data_collector(
    epoch_id: UUID,
    response: Response,
    _administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        OperationalMarketDataCollectorService,
        Depends(get_operational_market_data_collector_service),
    ],
) -> OperationalMarketDataCollectorEpochResponse:
    """Return one exact persisted operational collector epoch."""

    response.headers["Cache-Control"] = "no-store"

    return OperationalMarketDataCollectorEpochResponse.from_domain(await service.get(epoch_id))


@router.get(
    "/{epoch_id}/commands",
    response_model=OperationalMarketDataCollectorCommandListResponse,
)
async def list_operational_market_data_collector_commands(
    epoch_id: UUID,
    response: Response,
    _administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        OperationalMarketDataCollectorService,
        Depends(get_operational_market_data_collector_service),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=100),
    ] = 20,
    offset: Annotated[
        int,
        Query(ge=0, le=1_000_000),
    ] = 0,
) -> OperationalMarketDataCollectorCommandListResponse:
    """Return bounded immutable collector-command history."""

    response.headers["Cache-Control"] = "no-store"

    items = await service.list_commands(
        epoch_id,
        limit=limit,
        offset=offset,
    )

    return OperationalMarketDataCollectorCommandListResponse.from_domain(
        items,
        limit=limit,
        offset=offset,
    )


async def _request_command(
    *,
    epoch_id: UUID,
    payload: OperationalMarketDataCollectorCommandRequest,
    administrator_id: UUID,
    service: OperationalMarketDataCollectorService,
    command_type: collectors.OperationalMarketDataCollectorCommandType,
) -> OperationalMarketDataCollectorCommandResponse:
    intent = collectors.OperationalMarketDataCollectorCommandIntent(
        epoch_id=epoch_id,
        epoch_checksum=payload.epoch_checksum,
        command_type=command_type,
        expected_record_version=payload.expected_record_version,
    )

    if command_type is collectors.OperationalMarketDataCollectorCommandType.PAUSE:
        command = await service.pause(
            intent,
            actor_id=administrator_id,
            idempotency_key=payload.idempotency_key,
            requested_at=datetime.now(UTC),
        )
    elif command_type is collectors.OperationalMarketDataCollectorCommandType.RESUME:
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

    return OperationalMarketDataCollectorCommandResponse.from_domain(command)


@router.post(
    "/{epoch_id}/pause",
    response_model=OperationalMarketDataCollectorCommandResponse,
)
async def pause_operational_market_data_collector(
    epoch_id: UUID,
    payload: OperationalMarketDataCollectorCommandRequest,
    administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        OperationalMarketDataCollectorService,
        Depends(get_operational_market_data_collector_service),
    ],
) -> OperationalMarketDataCollectorCommandResponse:
    """Persist PAUSE; the supervisor owns physical convergence."""

    return await _request_command(
        epoch_id=epoch_id,
        payload=payload,
        administrator_id=administrator_id,
        service=service,
        command_type=(collectors.OperationalMarketDataCollectorCommandType.PAUSE),
    )


@router.post(
    "/{epoch_id}/resume",
    response_model=OperationalMarketDataCollectorCommandResponse,
)
async def resume_operational_market_data_collector(
    epoch_id: UUID,
    payload: OperationalMarketDataCollectorCommandRequest,
    administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        OperationalMarketDataCollectorService,
        Depends(get_operational_market_data_collector_service),
    ],
) -> OperationalMarketDataCollectorCommandResponse:
    """Persist RESUME without waiting for physical convergence."""

    return await _request_command(
        epoch_id=epoch_id,
        payload=payload,
        administrator_id=administrator_id,
        service=service,
        command_type=(collectors.OperationalMarketDataCollectorCommandType.RESUME),
    )


@router.post(
    "/{epoch_id}/stop",
    response_model=OperationalMarketDataCollectorCommandResponse,
)
async def stop_operational_market_data_collector(
    epoch_id: UUID,
    payload: OperationalMarketDataCollectorCommandRequest,
    administrator_id: Annotated[
        UUID,
        Depends(require_administrator),
    ],
    service: Annotated[
        OperationalMarketDataCollectorService,
        Depends(get_operational_market_data_collector_service),
    ],
) -> OperationalMarketDataCollectorCommandResponse:
    """Persist STOP without waiting for physical convergence."""

    return await _request_command(
        epoch_id=epoch_id,
        payload=payload,
        administrator_id=administrator_id,
        service=service,
        command_type=(collectors.OperationalMarketDataCollectorCommandType.STOP),
    )
