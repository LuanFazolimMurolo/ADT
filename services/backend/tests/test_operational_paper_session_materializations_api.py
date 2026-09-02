"""Remote-free contract tests for the paper-session materialization admin API."""

from __future__ import annotations

import ast
import inspect
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final, cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.resources import (
    get_admin_service,
    get_jwt_verifier,
    get_operational_paper_session_materialization_service,
)
from app.api.routes import admin_operational_paper_session_materializations
from app.api.schemas.operational_paper_session_materializations import (
    OperationalPaperSessionMaterializationCreateRequest,
)
from app.main import create_app
from app.operational_paper_session_materializations import (
    OperationalPaperSessionMaterialization,
    OperationalPaperSessionMaterializationConfigIdentityConflictError,
    OperationalPaperSessionMaterializationNotFoundError,
    OperationalPaperSessionMaterializationState,
    materialize_operational_paper_session_materialization,
    prepare_operational_paper_session_materialization,
)
from tests.test_operational_paper_session_materializations_domain import (
    _plan as _domain_plan,
)

PREFIX: Final = "/api/v1/admin/operational-paper-session-materializations"
ADMIN_ID: Final = UUID("10000000-0000-4000-8000-000000000001")
MATERIALIZED_BY: Final = UUID("10000000-0000-4000-8000-000000000002")
MATERIALIZATION_ID: Final = UUID("20000000-0000-4000-8000-000000000001")
AUTHORIZATION_ID: Final = UUID("40000000-0000-4000-8000-000000000004")
PREPARED_AT: Final = datetime(2026, 9, 2, 18, 0, tzinfo=UTC)
AUTH_HEADERS: Final = {"Authorization": "Bearer phase-7-09-gate-a5-token"}


def _materialization(
    state: OperationalPaperSessionMaterializationState = (
        OperationalPaperSessionMaterializationState.MATERIALIZED
    ),
) -> OperationalPaperSessionMaterialization:
    prepared = prepare_operational_paper_session_materialization(
        materialization_id=MATERIALIZATION_ID,
        plan=_domain_plan(),
        prepared_by=ADMIN_ID,
        prepared_at=PREPARED_AT,
    )
    if state is OperationalPaperSessionMaterializationState.PREPARED:
        return prepared
    return materialize_operational_paper_session_materialization(
        prepared,
        materialized_by=MATERIALIZED_BY,
        materialized_at=PREPARED_AT + timedelta(minutes=1),
    )


def _create_payload() -> dict[str, object]:
    return {"authorization_id": str(AUTHORIZATION_ID)}


class FakeJWTVerifier:
    """Record opaque bearer tokens and return one durable administrator UUID."""

    def __init__(self) -> None:
        self.tokens: list[str] = []

    async def verify(self, token: str) -> UUID:
        self.tokens.append(token)
        return ADMIN_ID


class FakeAdminService:
    """Record database-backed administrator membership decisions."""

    def __init__(self) -> None:
        self.allowed = True
        self.checked_users: list[UUID] = []

    async def is_admin(self, user_id: UUID) -> bool:
        self.checked_users.append(user_id)
        return self.allowed


class FakeOperationalPaperSessionMaterializationService:
    """Record route-to-service calls without retesting repository behavior."""

    def __init__(self) -> None:
        self.method_calls: list[str] = []
        self.list_calls: list[
            tuple[int, int, OperationalPaperSessionMaterializationState | None]
        ] = []
        self.get_calls: list[UUID] = []
        self.materialize_authorization_calls: list[tuple[UUID, UUID]] = []
        self.error: Exception | None = None

    def _raise_configured_error(self) -> None:
        if self.error is not None:
            raise self.error

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: OperationalPaperSessionMaterializationState | None = None,
    ) -> tuple[list[OperationalPaperSessionMaterialization], int]:
        self.method_calls.append("list")
        self.list_calls.append((limit, offset, state))
        self._raise_configured_error()
        return [_materialization()], 7

    async def get(
        self,
        materialization_id: UUID,
    ) -> OperationalPaperSessionMaterialization:
        self.method_calls.append("get")
        self.get_calls.append(materialization_id)
        self._raise_configured_error()
        return _materialization()

    async def materialize_authorization(
        self,
        authorization_id: UUID,
        *,
        actor_id: UUID,
    ) -> OperationalPaperSessionMaterialization:
        self.method_calls.append("materialize_authorization")
        self.materialize_authorization_calls.append((authorization_id, actor_id))
        self._raise_configured_error()
        return _materialization()


ApiFixture = tuple[
    FastAPI,
    FakeOperationalPaperSessionMaterializationService,
    FakeJWTVerifier,
    FakeAdminService,
]


@pytest.fixture
def api() -> ApiFixture:
    application = create_app()
    service = FakeOperationalPaperSessionMaterializationService()
    verifier = FakeJWTVerifier()
    admin_service = FakeAdminService()

    async def verifier_override() -> FakeJWTVerifier:
        return verifier

    async def admin_service_override() -> FakeAdminService:
        return admin_service

    async def materialization_service_override() -> (
        FakeOperationalPaperSessionMaterializationService
    ):
        return service

    application.dependency_overrides[get_jwt_verifier] = verifier_override
    application.dependency_overrides[get_admin_service] = admin_service_override
    application.dependency_overrides[get_operational_paper_session_materialization_service] = (
        materialization_service_override
    )
    return application, service, verifier, admin_service


@pytest.fixture
async def client(api: ApiFixture) -> AsyncIterator[AsyncClient]:
    application, _service, _verifier, _admin_service = api
    async with AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://gate-a5.test",
    ) as http_client:
        yield http_client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", PREFIX, None),
        ("POST", PREFIX, _create_payload()),
        ("GET", f"{PREFIX}/{MATERIALIZATION_ID}", None),
    ],
)
async def test_all_three_operations_require_authentication(
    client: AsyncClient,
    api: ApiFixture,
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    response = await client.request(method, path, json=payload)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert api[1].method_calls == []
    assert api[2].tokens == []
    assert api[3].checked_users == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", PREFIX, None),
        ("POST", PREFIX, _create_payload()),
        ("GET", f"{PREFIX}/{MATERIALIZATION_ID}", None),
    ],
)
async def test_all_three_operations_require_administrator_membership(
    client: AsyncClient,
    api: ApiFixture,
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    api[3].allowed = False

    response = await client.request(method, path, headers=AUTH_HEADERS, json=payload)

    assert response.status_code == 403
    assert api[1].method_calls == []
    assert api[2].tokens == ["phase-7-09-gate-a5-token"]
    assert api[3].checked_users == [ADMIN_ID]


@pytest.mark.asyncio
async def test_list_defaults_serialize_complete_provenance_and_no_store(
    client: AsyncClient,
    api: ApiFixture,
) -> None:
    response = await client.get(PREFIX, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert api[1].list_calls == [(20, 0, None)]
    body = response.json()
    assert (body["limit"], body["offset"], body["total"]) == (20, 0, 7)
    expected = _materialization()
    item = body["items"][0]
    assert item == {
        "materialization_id": str(expected.materialization_id),
        "schema_version": expected.schema_version,
        "materialization_contract_version": expected.materialization_contract_version,
        "state": "MATERIALIZED",
        "record_version": expected.record_version,
        "authorization_binding": {
            "authorization_id": str(expected.authorization_binding.authorization_id),
            "authorization_checksum": expected.authorization_binding.authorization_checksum,
        },
        "profile_binding": {
            "profile_id": str(expected.profile_binding.profile_id),
            "approved_revision": expected.profile_binding.approved_revision,
            "specification_checksum": expected.profile_binding.specification_checksum,
        },
        "mandate_binding": {
            "mandate_id": str(expected.mandate_binding.mandate_id),
            "approved_revision": expected.mandate_binding.approved_revision,
            "specification_checksum": expected.mandate_binding.specification_checksum,
        },
        "simulation_id": str(expected.simulation_id),
        "config_checksum": expected.config_checksum,
        "session_id": expected.session_id,
        "materialization_checksum": expected.materialization_checksum,
        "prepared_by": str(ADMIN_ID),
        "prepared_at": "2026-09-02T18:00:00Z",
        "materialized_by": str(MATERIALIZED_BY),
        "materialized_at": "2026-09-02T18:01:00Z",
    }
    assert {"config", "config_bytes", "path", "filesystem_path"}.isdisjoint(item)


@pytest.mark.asyncio
async def test_list_forwards_explicit_pagination_and_state(
    client: AsyncClient,
    api: ApiFixture,
) -> None:
    response = await client.get(
        PREFIX,
        headers=AUTH_HEADERS,
        params={"limit": 7, "offset": 4, "state": "MATERIALIZED"},
    )

    assert response.status_code == 200
    assert api[1].list_calls == [(7, 4, OperationalPaperSessionMaterializationState.MATERIALIZED)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limit", "offset", "state"),
    [
        (1, 0, "PREPARED"),
        (100, 1_000_000, "MATERIALIZED"),
    ],
)
async def test_list_accepts_exact_pagination_edges_and_both_states(
    client: AsyncClient,
    api: ApiFixture,
    limit: int,
    offset: int,
    state: str,
) -> None:
    response = await client.get(
        PREFIX,
        headers=AUTH_HEADERS,
        params={"limit": limit, "offset": offset, "state": state},
    )

    assert response.status_code == 200
    assert api[1].list_calls == [
        (limit, offset, OperationalPaperSessionMaterializationState(state))
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"offset": 1_000_001},
        {"state": "ARCHIVED"},
    ],
)
async def test_list_rejects_invalid_transport_values_before_service(
    client: AsyncClient,
    api: ApiFixture,
    params: dict[str, str | int],
) -> None:
    response = await client.get(PREFIX, headers=AUTH_HEADERS, params=params)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert api[1].method_calls == []


@pytest.mark.asyncio
async def test_get_forwards_uuid_serializes_response_and_sets_no_store(
    client: AsyncClient,
    api: ApiFixture,
) -> None:
    response = await client.get(
        f"{PREFIX}/{MATERIALIZATION_ID}",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert api[1].get_calls == [MATERIALIZATION_ID]
    assert response.json()["materialization_id"] == str(MATERIALIZATION_ID)

    malformed = await client.get(f"{PREFIX}/not-a-uuid", headers=AUTH_HEADERS)
    assert malformed.status_code == 422
    assert api[1].get_calls == [MATERIALIZATION_ID]


@pytest.mark.asyncio
async def test_post_uses_only_authorization_and_authenticated_actor_and_returns_201(
    client: AsyncClient,
    api: ApiFixture,
) -> None:
    response = await client.post(PREFIX, headers=AUTH_HEADERS, json=_create_payload())

    assert response.status_code == 201
    assert api[1].method_calls == ["materialize_authorization"]
    assert api[1].materialize_authorization_calls == [(AUTHORIZATION_ID, ADMIN_ID)]
    assert response.json()["materialization_id"] == str(MATERIALIZATION_ID)
    assert response.json()["state"] == "MATERIALIZED"


@pytest.mark.asyncio
async def test_post_replay_keeps_201_contract(
    client: AsyncClient,
    api: ApiFixture,
) -> None:
    first = await client.post(PREFIX, headers=AUTH_HEADERS, json=_create_payload())
    second = await client.post(PREFIX, headers=AUTH_HEADERS, json=_create_payload())

    assert first.status_code == second.status_code == 201
    assert api[1].materialize_authorization_calls == [
        (AUTHORIZATION_ID, ADMIN_ID),
        (AUTHORIZATION_ID, ADMIN_ID),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "actor_id",
        "plan",
        "profile_binding",
        "mandate_binding",
        "simulation_id",
        "authorized_capital",
        "quote_asset",
        "config",
        "config_checksum",
        "session_id",
        "materialization_checksum",
        "state",
        "prepared_by",
        "prepared_at",
        "materialized_by",
        "materialized_at",
        "record_version",
    ],
)
async def test_post_rejects_client_supplied_authority_fields_before_service(
    client: AsyncClient,
    api: ApiFixture,
    field: str,
) -> None:
    payload = _create_payload()
    payload[field] = "client-controlled"

    response = await client.post(PREFIX, headers=AUTH_HEADERS, json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert api[1].method_calls == []


def test_create_request_surface_is_exact_and_has_no_plan() -> None:
    assert set(OperationalPaperSessionMaterializationCreateRequest.model_fields) == {
        "authorization_id"
    }
    assert "plan" not in OperationalPaperSessionMaterializationCreateRequest.model_fields


@pytest.mark.asyncio
async def test_domain_errors_use_global_safe_http_contract(
    client: AsyncClient,
    api: ApiFixture,
) -> None:
    api[1].error = OperationalPaperSessionMaterializationNotFoundError()
    not_found = await client.get(
        f"{PREFIX}/{MATERIALIZATION_ID}",
        headers=AUTH_HEADERS,
    )

    api[1].error = OperationalPaperSessionMaterializationConfigIdentityConflictError()
    conflict = await client.post(PREFIX, headers=AUTH_HEADERS, json=_create_payload())

    assert not_found.status_code == 404
    assert not_found.json()["error"]["code"] == (
        "operational_paper_session_materialization_not_found"
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == (
        "operational_paper_session_materialization_config_identity_conflict"
    )
    assert api[1].method_calls == ["get", "materialize_authorization"]


def test_route_module_has_no_plan_persistence_or_runtime_authority() -> None:
    source = inspect.getsource(admin_operational_paper_session_materializations)
    tree = ast.parse(source)
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    forbidden = {
        "PaperTradingRepository",
        "PaperSessionConfig",
        "OperationalPaperSessionMaterializationPlan",
        "build_operational_paper_session_materialization_plan",
        "PostgresOperationalPaperSessionMaterializationRepository",
    }

    assert forbidden.isdisjoint(imported_names)
    assert all(name not in source for name in forbidden)
    assert ".materialize(" not in source
    assert source.count(".materialize_authorization(") == 1


def test_router_and_openapi_expose_exactly_three_protected_operations() -> None:
    application = create_app()
    schema = application.openapi()
    paths = cast(dict[str, dict[str, object]], schema["paths"])
    materialization_paths = {path: item for path, item in paths.items() if path.startswith(PREFIX)}
    expected = {
        PREFIX: {"get", "post"},
        f"{PREFIX}/{{materialization_id}}": {"get"},
    }

    assert set(materialization_paths) == set(expected)
    operation_count = 0
    for path, methods in expected.items():
        path_item = materialization_paths[path]
        actual_methods = {
            method.lower()
            for method in path_item
            if method.lower() in {"get", "post", "put", "patch", "delete"}
        }
        assert actual_methods == methods
        for method in methods:
            operation_count += 1
            operation = cast(dict[str, object], path_item[method])
            assert operation["security"] == [{"HTTPBearer": []}]
    assert operation_count == 3
    assert all(
        token not in path.lower()
        for path in materialization_paths
        for token in ("start", "stop", "pause", "resume", "run", "run_once", "worker", "activate")
    )

    components = cast(dict[str, object], schema["components"])
    schemas = cast(dict[str, dict[str, object]], components["schemas"])
    request_schema = schemas["OperationalPaperSessionMaterializationCreateRequest"]
    properties = cast(dict[str, object], request_schema["properties"])
    assert set(properties) == {"authorization_id"}
    assert "plan" not in properties
    assert (
        sum(
            getattr(route, "original_router", None)
            is admin_operational_paper_session_materializations.router
            for route in application.routes
        )
        == 1
    )
