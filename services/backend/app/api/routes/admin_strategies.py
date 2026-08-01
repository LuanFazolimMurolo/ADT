"""Administrative CRUD routes for versioned strategy definitions."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import get_strategy_definition_service
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.schemas.pagination import PageMeta, PageParams
from app.api.schemas.strategies import (
    StrategyDefinitionArchiveRequest,
    StrategyDefinitionCreateRequest,
    StrategyDefinitionListResponse,
    StrategyDefinitionReplaceRequest,
    StrategyDefinitionResponse,
)
from app.strategies import StrategyDefinitionService

router = APIRouter(
    prefix="/api/v1/admin/strategies",
    tags=["admin strategies"],
    responses=ADMIN_ERROR_RESPONSES,
)


@router.get("", response_model=StrategyDefinitionListResponse)
async def list_strategy_definitions(
    pagination: Annotated[PageParams, Depends()],
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        StrategyDefinitionService,
        Depends(get_strategy_definition_service),
    ],
    include_archived: Annotated[bool, Query()] = False,
) -> StrategyDefinitionListResponse:
    """List active definitions or include archived history explicitly."""

    definitions, total = await service.list(
        limit=pagination.page_size,
        offset=pagination.offset,
        include_archived=include_archived,
    )
    return StrategyDefinitionListResponse(
        items=[StrategyDefinitionResponse.from_domain(item) for item in definitions],
        pagination=PageMeta.from_total(
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
        ),
    )


@router.post(
    "",
    response_model=StrategyDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_strategy_definition(
    payload: StrategyDefinitionCreateRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        StrategyDefinitionService,
        Depends(get_strategy_definition_service),
    ],
) -> StrategyDefinitionResponse:
    """Create an active definition from one explicitly registered plugin."""

    definition = await service.create(
        display_name=payload.display_name,
        plugin_name=payload.plugin_name,
        plugin_version=payload.plugin_version,
        parameters=payload.raw_parameters(),
        actor_id=administrator_id,
    )
    return StrategyDefinitionResponse.from_domain(definition)


@router.get("/{definition_id}", response_model=StrategyDefinitionResponse)
async def get_strategy_definition(
    definition_id: UUID,
    _administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        StrategyDefinitionService,
        Depends(get_strategy_definition_service),
    ],
) -> StrategyDefinitionResponse:
    """Return one definition after plugin and checksum revalidation."""

    definition = await service.get(definition_id)
    return StrategyDefinitionResponse.from_domain(definition)


@router.patch("/{definition_id}", response_model=StrategyDefinitionResponse)
async def replace_strategy_definition(
    definition_id: UUID,
    payload: StrategyDefinitionReplaceRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        StrategyDefinitionService,
        Depends(get_strategy_definition_service),
    ],
) -> StrategyDefinitionResponse:
    """Replace mutable fields only when the supplied revision is current."""

    definition = await service.replace(
        definition_id,
        display_name=payload.display_name,
        plugin_name=payload.plugin_name,
        plugin_version=payload.plugin_version,
        parameters=payload.raw_parameters(),
        expected_revision=payload.expected_revision,
        actor_id=administrator_id,
    )
    return StrategyDefinitionResponse.from_domain(definition)


@router.post("/{definition_id}/archive", response_model=StrategyDefinitionResponse)
async def archive_strategy_definition(
    definition_id: UUID,
    payload: StrategyDefinitionArchiveRequest,
    administrator_id: Annotated[UUID, Depends(require_administrator)],
    service: Annotated[
        StrategyDefinitionService,
        Depends(get_strategy_definition_service),
    ],
) -> StrategyDefinitionResponse:
    """Perform the irreversible active-to-archived transition."""

    definition = await service.archive(
        definition_id,
        expected_revision=payload.expected_revision,
        actor_id=administrator_id,
    )
    return StrategyDefinitionResponse.from_domain(definition)
