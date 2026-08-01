"""Remote-free tests for the administrative strategy-definition HTTP boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import get_strategy_definition_service
from app.main import create_app
from app.strategies.definitions import (
    StoredStrategyParameter,
    StrategyDefinition,
    StrategyDefinitionSpec,
    StrategyDefinitionState,
    strategy_parameter_checksum,
)
from app.strategies.domain import RawStrategyParameters, StrategyParameterKind
from app.strategies.errors import StrategyDefinitionNameConflictError

ADMIN_ID: Final = UUID("10000000-0000-4000-8000-000000000001")
DEFINITION_ID: Final = UUID("20000000-0000-4000-8000-000000000002")
NOW: Final = datetime(2026, 8, 1, 5, 0, tzinfo=UTC)
AUTH_HEADERS: Final = {"Authorization": "Bearer phase-3c-test-token"}


def _spec(
    *,
    display_name: str = "EMA demo",
    quantity: str = "1.25",
) -> StrategyDefinitionSpec:
    parameters = (
        StoredStrategyParameter("fast_period", StrategyParameterKind.INTEGER, 3),
        StoredStrategyParameter("quantity", StrategyParameterKind.DECIMAL, quantity),
        StoredStrategyParameter("slow_period", StrategyParameterKind.INTEGER, 5),
    )
    return StrategyDefinitionSpec(
        display_name=display_name,
        plugin_name="ema-cross-example",
        plugin_version="1",
        plugin_schema_version=1,
        lifecycle_version=1,
        parameters=parameters,
        parameters_checksum=strategy_parameter_checksum(parameters),
    )


def _definition() -> StrategyDefinition:
    return StrategyDefinition(
        id=DEFINITION_ID,
        spec=_spec(),
        state=StrategyDefinitionState.ACTIVE,
        revision=1,
        created_by=ADMIN_ID,
        updated_by=ADMIN_ID,
        created_at=NOW,
        updated_at=NOW,
    )


class FakeStrategyDefinitionService:
    """Record route calls while returning validated domain objects."""

    def __init__(self) -> None:
        self.definition = _definition()
        self.create_parameters: RawStrategyParameters | None = None
        self.replace_revision: int | None = None
        self.archive_revision: int | None = None
        self.include_archived: bool | None = None
        self.error: Exception | None = None

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        include_archived: bool = False,
    ) -> tuple[list[StrategyDefinition], int]:
        assert limit == 5
        assert offset == 5
        self.include_archived = include_archived
        return [self.definition], 1

    async def get(self, definition_id: UUID) -> StrategyDefinition:
        assert definition_id == DEFINITION_ID
        return self.definition

    async def create(
        self,
        *,
        display_name: str,
        plugin_name: str,
        plugin_version: str,
        parameters: RawStrategyParameters,
        actor_id: UUID,
    ) -> StrategyDefinition:
        self.create_parameters = parameters
        if self.error is not None:
            raise self.error
        assert display_name == "EMA demo"
        assert plugin_name == "ema-cross-example"
        assert plugin_version == "1"
        assert actor_id == ADMIN_ID
        return self.definition

    async def replace(
        self,
        definition_id: UUID,
        *,
        display_name: str,
        plugin_name: str,
        plugin_version: str,
        parameters: RawStrategyParameters,
        expected_revision: int,
        actor_id: UUID,
    ) -> StrategyDefinition:
        assert definition_id == DEFINITION_ID
        assert display_name == "EMA revised"
        assert plugin_name == "ema-cross-example"
        assert plugin_version == "1"
        assert parameters["quantity"] == Decimal("2.5")
        assert actor_id == ADMIN_ID
        self.replace_revision = expected_revision
        self.definition = replace(
            self.definition,
            spec=_spec(display_name=display_name, quantity="2.5"),
            revision=2,
        )
        return self.definition

    async def archive(
        self,
        definition_id: UUID,
        *,
        expected_revision: int,
        actor_id: UUID,
    ) -> StrategyDefinition:
        assert definition_id == DEFINITION_ID
        assert actor_id == ADMIN_ID
        self.archive_revision = expected_revision
        self.definition = replace(
            self.definition,
            state=StrategyDefinitionState.ARCHIVED,
            revision=2,
            archived_at=NOW,
        )
        return self.definition


@pytest.fixture
def api() -> tuple[FastAPI, FakeStrategyDefinitionService]:
    application = create_app()
    service = FakeStrategyDefinitionService()

    async def administrator_override() -> UUID:
        return ADMIN_ID

    def service_override() -> FakeStrategyDefinitionService:
        return service

    application.dependency_overrides[require_administrator] = administrator_override
    application.dependency_overrides[get_strategy_definition_service] = service_override
    return application, service


@pytest.fixture
async def client(
    api: tuple[FastAPI, FakeStrategyDefinitionService],
) -> AsyncIterator[AsyncClient]:
    application, _service = api
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as http_client:
        yield http_client


@pytest.mark.asyncio
async def test_create_converts_explicit_decimal_text_without_float(
    client: AsyncClient,
    api: tuple[FastAPI, FakeStrategyDefinitionService],
) -> None:
    response = await client.post(
        "/api/v1/admin/strategies",
        headers=AUTH_HEADERS,
        json={
            "display_name": "EMA demo",
            "plugin_name": "ema-cross-example",
            "plugin_version": "1",
            "parameters": {
                "fast_period": {"kind": "integer", "value": 3},
                "quantity": {"kind": "decimal", "value": "1.25"},
                "slow_period": {"kind": "integer", "value": 5},
            },
        },
    )

    assert response.status_code == 201
    assert response.json()["parameters"]["quantity"] == {
        "kind": "decimal",
        "value": "1.25",
    }
    service = api[1]
    assert service.create_parameters is not None
    assert service.create_parameters["quantity"] == Decimal("1.25")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("decimal", 1.25),
        ("integer", True),
        ("boolean", 1),
        ("string", {"nested": "forbidden"}),
    ],
)
async def test_parameter_kind_mismatch_is_rejected(
    client: AsyncClient,
    kind: str,
    value: object,
) -> None:
    response = await client.post(
        "/api/v1/admin/strategies",
        headers=AUTH_HEADERS,
        json={
            "display_name": "EMA demo",
            "plugin_name": "ema-cross-example",
            "plugin_version": "1",
            "parameters": {"quantity": {"kind": kind, "value": value}},
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_list_passes_pagination_and_archive_filter(
    client: AsyncClient,
    api: tuple[FastAPI, FakeStrategyDefinitionService],
) -> None:
    response = await client.get(
        "/api/v1/admin/strategies?page=2&page_size=5&include_archived=true",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["pagination"] == {
        "page": 2,
        "page_size": 5,
        "total": 1,
        "total_pages": 1,
    }
    assert api[1].include_archived is True


@pytest.mark.asyncio
async def test_get_replace_and_archive_use_revisioned_contract(
    client: AsyncClient,
    api: tuple[FastAPI, FakeStrategyDefinitionService],
) -> None:
    get_response = await client.get(
        f"/api/v1/admin/strategies/{DEFINITION_ID}",
        headers=AUTH_HEADERS,
    )
    replace_response = await client.patch(
        f"/api/v1/admin/strategies/{DEFINITION_ID}",
        headers=AUTH_HEADERS,
        json={
            "display_name": "EMA revised",
            "plugin_name": "ema-cross-example",
            "plugin_version": "1",
            "parameters": {
                "quantity": {"kind": "decimal", "value": "2.5"},
            },
            "expected_revision": 1,
        },
    )
    archive_response = await client.post(
        f"/api/v1/admin/strategies/{DEFINITION_ID}/archive",
        headers=AUTH_HEADERS,
        json={"expected_revision": 2},
    )

    assert get_response.status_code == 200
    assert replace_response.status_code == 200
    assert replace_response.json()["revision"] == 2
    assert archive_response.status_code == 200
    assert archive_response.json()["state"] == "ARCHIVED"
    assert api[1].replace_revision == 1
    assert api[1].archive_revision == 2


@pytest.mark.asyncio
async def test_domain_conflict_uses_safe_existing_error_contract(
    client: AsyncClient,
    api: tuple[FastAPI, FakeStrategyDefinitionService],
) -> None:
    api[1].error = StrategyDefinitionNameConflictError()

    response = await client.post(
        "/api/v1/admin/strategies",
        headers=AUTH_HEADERS,
        json={
            "display_name": "EMA demo",
            "plugin_name": "no-op",
            "plugin_version": "1",
            "parameters": {},
        },
    )

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "strategy_definition_name_conflict",
        "message": "Já existe uma definição de estratégia com esse nome.",
    }
