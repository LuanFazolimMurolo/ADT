"""Remote-free contracts for the administrative activation HTTP boundary."""

from __future__ import annotations

import ast
import inspect
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import resources
from app.api.dependencies.resources import (
    get_admin_service,
    get_jwt_verifier,
    get_operational_paper_session_activation_service,
)
from app.api.openapi import ADMIN_ERROR_RESPONSES
from app.api.routes import admin_operational_paper_session_activations
from app.api.schemas.operational_paper_session_activations import (
    OperationalPaperSessionActivationAuthorizeRequest,
    OperationalPaperSessionActivationRevokeRequest,
)
from app.database import Database
from app.domain.errors import DomainError, PersistenceError, PersistenceUnavailableError
from app.main import create_app
from app.operational_paper_session_activations import (
    InvalidOperationalPaperSessionActivationSpecificationError,
    OperationalPaperSessionActivation,
    OperationalPaperSessionActivationBoundsExceededError,
    OperationalPaperSessionActivationCurrentGrantConflictError,
    OperationalPaperSessionActivationIdempotencyConflictError,
    OperationalPaperSessionActivationNotFoundError,
    OperationalPaperSessionActivationRecordVersionConflictError,
    OperationalPaperSessionActivationState,
    OperationalPaperSessionActivationStateTransitionConflictError,
    revoke_operational_paper_session_activation,
)
from app.operational_paper_session_materializations import (
    OperationalPaperSessionMaterializationConfigIdentityConflictError,
    OperationalPaperSessionMaterializationNotFoundError,
    OperationalPaperSessionMaterializationStateTransitionConflictError,
)
from app.paper_trading.errors import PaperSessionCorruptError
from app.paper_trading.repository import PaperTradingRepository
from app.repositories.operational_paper_session_activations import (
    PostgresOperationalPaperSessionActivationRepository,
)
from app.repositories.operational_paper_session_materializations import (
    PostgresOperationalPaperSessionMaterializationRepository,
)
from app.repositories.operational_paper_session_profiles import (
    PostgresOperationalPaperSessionProfileRepository,
)
from app.services import OperationalPaperSessionActivationService
from app.strategies.errors import StrategyDefinitionCompatibilityError
from app.strategies.registry import StrategyPluginRegistry
from tests.test_operational_paper_session_activations_domain import (
    ACTIVATION_ID,
    ACTOR_ID,
    MATERIALIZATION_ID,
    NOW,
    OTHER_ACTOR_ID,
    _aggregate,
)

PREFIX = "/api/v1/admin/operational-paper-session-activations"
AUTH_HEADERS = {"Authorization": "Bearer activation-api-test-token"}
IDEMPOTENCY_KEY = "Activation:HTTP_1.test-key"
AUTHORITATIVE_EXTRAS = (
    "actor_id",
    "materialization_checksum",
    "create_intent_fingerprint",
    "activation_checksum",
    "authorization_binding",
    "profile_binding",
    "mandate_binding",
    "simulation_id",
    "session_id",
    "config_checksum",
    "authorized_by",
    "authorized_at",
    "state",
    "record_version",
)


def _payload() -> dict[str, object]:
    return {"materialization_id": str(MATERIALIZATION_ID), "idempotency_key": IDEMPOTENCY_KEY}


def _revoked() -> OperationalPaperSessionActivation:
    return revoke_operational_paper_session_activation(
        _aggregate(), revoked_by=OTHER_ACTOR_ID, revoked_at=NOW + timedelta(minutes=1)
    )


def _expected_response(activation: OperationalPaperSessionActivation) -> dict[str, object]:
    """Independent expected wire projection, including exact audit serialization."""
    return {
        "activation_id": str(activation.activation_id),
        "schema_version": activation.schema_version,
        "activation_contract_version": activation.activation_contract_version,
        "state": activation.state.value,
        "record_version": activation.record_version,
        "materialization_id": str(activation.materialization_id),
        "materialization_checksum": activation.materialization_checksum,
        "authorization_binding": {
            "authorization_id": str(activation.authorization_binding.authorization_id),
            "authorization_checksum": activation.authorization_binding.authorization_checksum,
        },
        "profile_binding": {
            "profile_id": str(activation.profile_binding.profile_id),
            "approved_revision": activation.profile_binding.approved_revision,
            "specification_checksum": activation.profile_binding.specification_checksum,
        },
        "mandate_binding": {
            "mandate_id": str(activation.mandate_binding.mandate_id),
            "approved_revision": activation.mandate_binding.approved_revision,
            "specification_checksum": activation.mandate_binding.specification_checksum,
        },
        "simulation_id": str(activation.simulation_id),
        "session_id": activation.session_id,
        "config_checksum": activation.config_checksum,
        "activation_checksum": activation.activation_checksum,
        "authorized_by": str(activation.authorized_by),
        "authorized_at": activation.authorized_at.isoformat().replace("+00:00", "Z"),
        "revoked_by": None if activation.revoked_by is None else str(activation.revoked_by),
        "revoked_at": (
            None
            if activation.revoked_at is None
            else activation.revoked_at.isoformat().replace("+00:00", "Z")
        ),
    }


class FakeJWTVerifier:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    async def verify(self, token: str) -> UUID:
        self.tokens.append(token)
        return ACTOR_ID


class FakeAdminService:
    def __init__(self) -> None:
        self.allowed = True
        self.checked_users: list[UUID] = []

    async def is_admin(self, user_id: UUID) -> bool:
        self.checked_users.append(user_id)
        return self.allowed


class FakeActivationService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.result = _aggregate()
        self.error: Exception | None = None

    def _record(self, *args: object) -> None:
        self.calls.append(args)
        if self.error is not None:
            raise self.error

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: OperationalPaperSessionActivationState | None = None,
        materialization_id: UUID | None = None,
    ) -> tuple[list[OperationalPaperSessionActivation], int]:
        self._record("list", limit, offset, state, materialization_id)
        return [self.result], 17

    async def get(self, activation_id: UUID) -> OperationalPaperSessionActivation:
        self._record("get", activation_id)
        return self.result

    async def authorize(
        self,
        materialization_id: UUID,
        *,
        actor_id: UUID,
        idempotency_key: str,
    ) -> OperationalPaperSessionActivation:
        self._record("authorize", materialization_id, actor_id, idempotency_key)
        return self.result

    async def revoke(
        self,
        activation_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
    ) -> OperationalPaperSessionActivation:
        self._record("revoke", activation_id, expected_record_version, actor_id)
        return _revoked()


ApiFixture = tuple[FastAPI, FakeActivationService, FakeJWTVerifier, FakeAdminService]


@pytest.fixture
def api() -> ApiFixture:
    application = create_app()
    service = FakeActivationService()
    verifier = FakeJWTVerifier()
    administrator = FakeAdminService()

    async def verifier_override() -> FakeJWTVerifier:
        return verifier

    async def administrator_override() -> FakeAdminService:
        return administrator

    async def service_override() -> FakeActivationService:
        return service

    application.dependency_overrides[get_jwt_verifier] = verifier_override
    application.dependency_overrides[get_admin_service] = administrator_override
    application.dependency_overrides[get_operational_paper_session_activation_service] = (
        service_override
    )
    return application, service, verifier, administrator


@pytest.fixture
async def client(api: ApiFixture) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=api[0], raise_app_exceptions=False),
        base_url="http://activation.test",
    ) as http_client:
        yield http_client


OPERATIONS = (
    ("GET", PREFIX, None),
    ("GET", f"{PREFIX}/{ACTIVATION_ID}", None),
    ("POST", PREFIX, _payload()),
    ("POST", f"{PREFIX}/{ACTIVATION_ID}/revoke", {"expected_record_version": 1}),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "payload"), OPERATIONS)
@pytest.mark.parametrize("authenticated", (False, True))
async def test_all_operations_require_real_administrator_chain(
    client: AsyncClient,
    api: ApiFixture,
    method: str,
    path: str,
    payload: dict[str, object] | None,
    authenticated: bool,
) -> None:
    api[3].allowed = False
    response = await client.request(
        method, path, json=payload, headers=AUTH_HEADERS if authenticated else {}
    )
    assert response.status_code == (403 if authenticated else 401)
    assert api[1].calls == []
    assert api[2].tokens == (["activation-api-test-token"] if authenticated else [])
    assert api[3].checked_users == ([ACTOR_ID] if authenticated else [])
    if not authenticated:
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_list_defaults_independent_total_exact_response_and_no_store(
    client: AsyncClient,
    api: ApiFixture,
) -> None:
    response = await client.get(PREFIX, headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert api[1].calls == [("list", 20, 0, None, None)]
    assert response.json() == {
        "items": [_expected_response(_aggregate())],
        "limit": 20,
        "offset": 0,
        "total": 17,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "expected"),
    (
        ({"state": "REVOKED"}, (20, 0, OperationalPaperSessionActivationState.REVOKED, None)),
        ({"materialization_id": str(MATERIALIZATION_ID)}, (20, 0, None, MATERIALIZATION_ID)),
        (
            {
                "state": "AUTHORIZED",
                "materialization_id": str(MATERIALIZATION_ID),
                "limit": 1,
                "offset": 0,
            },
            (1, 0, OperationalPaperSessionActivationState.AUTHORIZED, MATERIALIZATION_ID),
        ),
        (
            {"state": "REVOKED", "limit": 100, "offset": 1_000_000},
            (100, 1_000_000, OperationalPaperSessionActivationState.REVOKED, None),
        ),
    ),
)
async def test_list_filters_and_exact_pagination_edges(
    client: AsyncClient,
    api: ApiFixture,
    params: dict[str, str | int],
    expected: tuple[object, ...],
) -> None:
    response = await client.get(PREFIX, headers=AUTH_HEADERS, params=params)
    assert response.status_code == 200
    assert api[1].calls == [("list", *expected)]
    assert (response.json()["limit"], response.json()["offset"]) == expected[:2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    (
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"offset": 1_000_001},
        {"state": "RUNNING"},
        {"materialization_id": "not-a-uuid"},
    ),
)
async def test_invalid_list_queries_fail_before_service(
    client: AsyncClient,
    api: ApiFixture,
    params: dict[str, str | int],
) -> None:
    response = await client.get(PREFIX, headers=AUTH_HEADERS, params=params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert api[1].calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked", (False, True))
async def test_get_exact_uuid_and_full_authorized_or_revoked_response(
    client: AsyncClient,
    api: ApiFixture,
    revoked: bool,
) -> None:
    api[1].result = _revoked() if revoked else _aggregate()
    response = await client.get(f"{PREFIX}/{ACTIVATION_ID}", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert api[1].calls == [("get", ACTIVATION_ID)]
    assert response.json() == _expected_response(api[1].result)


@pytest.mark.asyncio
async def test_authorize_forwards_exact_inputs_and_authenticated_actor_once(
    client: AsyncClient,
    api: ApiFixture,
) -> None:
    response = await client.post(PREFIX, headers=AUTH_HEADERS, json=_payload())
    assert response.status_code == 201
    assert api[1].calls == [("authorize", MATERIALIZATION_ID, ACTOR_ID, IDEMPOTENCY_KEY)]
    assert api[3].checked_users == [ACTOR_ID]
    assert response.json() == _expected_response(_aggregate())


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked", (False, True))
async def test_authorize_historical_replays_keep_201_without_state_interpretation(
    client: AsyncClient,
    api: ApiFixture,
    revoked: bool,
) -> None:
    api[1].result = _revoked() if revoked else _aggregate()
    for _ in range(2):
        response = await client.post(PREFIX, headers=AUTH_HEADERS, json=_payload())
        assert response.status_code == 201
        assert response.json() == _expected_response(api[1].result)
    assert api[1].calls == [("authorize", MATERIALIZATION_ID, ACTOR_ID, IDEMPOTENCY_KEY)] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("field", AUTHORITATIVE_EXTRAS)
async def test_authorize_rejects_each_client_authority_override(
    client: AsyncClient,
    api: ApiFixture,
    field: str,
) -> None:
    response = await client.post(
        PREFIX, headers=AUTH_HEADERS, json={**_payload(), field: "client-controlled"}
    )
    assert response.status_code == 422
    assert api[1].calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    (
        None,
        {},
        {"materialization_id": str(MATERIALIZATION_ID)},
        {"idempotency_key": IDEMPOTENCY_KEY},
        {"materialization_id": "not-a-uuid", "idempotency_key": IDEMPOTENCY_KEY},
    ),
)
async def test_authorize_rejects_missing_body_fields_and_malformed_uuid(
    client: AsyncClient,
    api: ApiFixture,
    payload: dict[str, object] | None,
) -> None:
    response = await client.post(PREFIX, headers=AUTH_HEADERS, json=payload)
    assert response.status_code == 422
    assert api[1].calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ("", "a" * 129, "_bad", "a b", "a/b", True, 12, None))
async def test_authorize_rejects_invalid_idempotency_keys_before_service(
    client: AsyncClient,
    api: ApiFixture,
    key: object,
) -> None:
    response = await client.post(
        PREFIX, headers=AUTH_HEADERS, json={**_payload(), "idempotency_key": key}
    )
    assert response.status_code == 422
    assert api[1].calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ("a", "Z" * 128))
async def test_authorize_accepts_exact_idempotency_length_edges(
    client: AsyncClient,
    api: ApiFixture,
    key: str,
) -> None:
    response = await client.post(
        PREFIX, headers=AUTH_HEADERS, json={**_payload(), "idempotency_key": key}
    )
    assert response.status_code == 201
    assert api[1].calls == [("authorize", MATERIALIZATION_ID, ACTOR_ID, key)]


@pytest.mark.asyncio
async def test_revoke_delegates_exactly_once_without_any_preread(
    client: AsyncClient,
    api: ApiFixture,
) -> None:
    response = await client.post(
        f"{PREFIX}/{ACTIVATION_ID}/revoke",
        headers=AUTH_HEADERS,
        json={"expected_record_version": 7},
    )
    assert response.status_code == 200
    assert api[1].calls == [("revoke", ACTIVATION_ID, 7, ACTOR_ID)]
    assert response.json() == _expected_response(_revoked())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    (
        None,
        {},
        {"expected_record_version": 0},
        {"expected_record_version": -1},
        {"expected_record_version": True},
        {"expected_record_version": "7"},
        {"expected_record_version": 7.0},
        {"expected_record_version": 7, "actor_id": str(OTHER_ACTOR_ID)},
        {"expected_record_version": 7, "reason": "reason"},
    ),
)
async def test_revoke_rejects_invalid_body_before_service(
    client: AsyncClient,
    api: ApiFixture,
    payload: dict[str, object] | None,
) -> None:
    response = await client.post(
        f"{PREFIX}/{ACTIVATION_ID}/revoke", headers=AUTH_HEADERS, json=payload
    )
    assert response.status_code == 422
    assert api[1].calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "suffix", "payload"),
    (
        ("GET", "", None),
        ("POST", "/revoke", {"expected_record_version": 1}),
    ),
)
async def test_malformed_activation_path_uuid_fails_before_service(
    client: AsyncClient,
    api: ApiFixture,
    method: str,
    suffix: str,
    payload: dict[str, object] | None,
) -> None:
    response = await client.request(
        method, f"{PREFIX}/not-a-uuid{suffix}", headers=AUTH_HEADERS, json=payload
    )
    assert response.status_code == 422
    assert api[1].calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "error", "http_status", "code"),
    (
        (
            "get",
            OperationalPaperSessionActivationNotFoundError(),
            404,
            "operational_paper_session_activation_not_found",
        ),
        (
            "revoke",
            OperationalPaperSessionActivationRecordVersionConflictError(),
            409,
            "operational_paper_session_activation_record_version_conflict",
        ),
        (
            "authorize",
            OperationalPaperSessionActivationIdempotencyConflictError(),
            409,
            "operational_paper_session_activation_idempotency_conflict",
        ),
        (
            "authorize",
            OperationalPaperSessionActivationCurrentGrantConflictError(),
            409,
            "operational_paper_session_activation_current_grant_conflict",
        ),
        (
            "revoke",
            OperationalPaperSessionActivationStateTransitionConflictError(),
            409,
            "operational_paper_session_activation_state_transition_conflict",
        ),
        (
            "authorize",
            OperationalPaperSessionMaterializationNotFoundError(),
            404,
            "operational_paper_session_materialization_not_found",
        ),
        (
            "authorize",
            OperationalPaperSessionMaterializationStateTransitionConflictError(),
            409,
            "operational_paper_session_materialization_state_transition_conflict",
        ),
        (
            "authorize",
            OperationalPaperSessionMaterializationConfigIdentityConflictError(),
            409,
            "operational_paper_session_materialization_config_identity_conflict",
        ),
        (
            "authorize",
            StrategyDefinitionCompatibilityError(),
            409,
            "strategy_definition_incompatible",
        ),
        ("authorize", PaperSessionCorruptError(), 500, "paper_session_corrupt"),
        ("authorize", PersistenceError(), 500, "persistence_error"),
        ("authorize", PersistenceUnavailableError(), 503, "database_unavailable"),
        (
            "authorize",
            InvalidOperationalPaperSessionActivationSpecificationError(),
            400,
            "operational_paper_session_activation_invalid_specification",
        ),
        (
            "authorize",
            OperationalPaperSessionActivationBoundsExceededError(),
            400,
            "operational_paper_session_activation_bounds_exceeded",
        ),
    ),
)
async def test_domain_errors_preserve_global_status_and_stable_code(
    client: AsyncClient,
    api: ApiFixture,
    operation: str,
    error: DomainError,
    http_status: int,
    code: str,
) -> None:
    api[1].error = error
    if operation == "get":
        response = await client.get(f"{PREFIX}/{ACTIVATION_ID}", headers=AUTH_HEADERS)
    elif operation == "revoke":
        response = await client.post(
            f"{PREFIX}/{ACTIVATION_ID}/revoke",
            headers=AUTH_HEADERS,
            json={"expected_record_version": 1},
        )
    else:
        response = await client.post(PREFIX, headers=AUTH_HEADERS, json=_payload())
    assert response.status_code == http_status
    assert response.json()["error"]["code"] == code
    assert len(api[1].calls) == 1
    assert api[1].calls[0][0] == operation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    (PersistenceError, PersistenceUnavailableError, PaperSessionCorruptError, RuntimeError),
)
async def test_safe_failures_do_not_expose_internal_cause(
    client: AsyncClient,
    api: ApiFixture,
    error_type: type[Exception],
) -> None:
    sentinel = (
        "SELECT secret FROM private; /private/data/config.json password=fake-secret Traceback"
    )
    if issubclass(error_type, DomainError):
        error = error_type()
        error.__cause__ = RuntimeError(sentinel)
    else:
        error = error_type(sentinel)
    api[1].error = error
    response = await client.post(PREFIX, headers=AUTH_HEADERS, json=_payload())
    assert response.status_code == (503 if error_type is PersistenceUnavailableError else 500)
    expected_code = error.code if isinstance(error, DomainError) else "internal_error"
    expected_message = (
        error.message if isinstance(error, DomainError) else "An internal server error occurred."
    )
    assert response.json() == {"error": {"code": expected_code, "message": expected_message}}
    for fragment in ("SELECT", "/private/", "fake-secret", "Traceback", error_type.__name__):
        assert fragment not in response.text


def test_provider_reuses_database_paper_repository_and_never_builds_strategy() -> None:
    database = Database("postgresql://adt_test@127.0.0.1:1/adt_test")
    paper = cast(PaperTradingRepository, object())
    builtin_identities = StrategyPluginRegistry.builtins().identities
    with (
        patch.object(resources, "Database", side_effect=AssertionError("new database")),
        patch.object(
            resources, "PaperTradingRepository", side_effect=AssertionError("new paper repository")
        ),
        patch.object(
            StrategyPluginRegistry, "build", side_effect=AssertionError("strategy build")
        ) as build,
    ):
        service = get_operational_paper_session_activation_service(database, paper)
    assert isinstance(service, OperationalPaperSessionActivationService)
    for repository, expected_type in (
        (service._repository, PostgresOperationalPaperSessionActivationRepository),
        (
            service._materialization_repository,
            PostgresOperationalPaperSessionMaterializationRepository,
        ),
        (service._profile_repository, PostgresOperationalPaperSessionProfileRepository),
    ):
        assert isinstance(repository, expected_type)
        assert repository._database is database
    assert service._paper_repository is paper
    assert service._registry.identities == builtin_identities
    before = datetime.now(UTC)
    now = service._clock()
    assert before <= now <= datetime.now(UTC)
    assert now.tzinfo is UTC
    build.assert_not_called()


def test_route_has_only_application_service_authority() -> None:
    tree = ast.parse(inspect.getsource(admin_operational_paper_session_activations))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imports == {
        "__future__",
        "typing",
        "uuid",
        "fastapi",
        "app.api.dependencies.auth",
        "app.api.dependencies.resources",
        "app.api.openapi",
        "app.api.schemas.operational_paper_session_activations",
        "app.operational_paper_session_activations",
        "app.services",
    }
    assert not any(isinstance(node, (ast.Import, ast.Try)) for node in ast.walk(tree))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    service_calls = [
        node.func.attr
        for node in calls
        if isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "service"
    ]
    assert sorted(service_calls) == ["authorize", "get", "list", "revoke"]
    for node in calls:
        if isinstance(node.func, ast.Name):
            assert node.func.id in {"APIRouter", "Depends", "Query"}
        else:
            assert isinstance(node.func, ast.Attribute)
            assert node.func.attr in {"get", "post", "list", "authorize", "revoke", "from_domain"}


def test_exact_router_openapi_requests_responses_and_security() -> None:
    application = create_app()
    schema = application.openapi()
    paths = {path: item for path, item in schema["paths"].items() if path.startswith(PREFIX)}
    expected = {
        PREFIX: {"get", "post"},
        f"{PREFIX}/{{activation_id}}": {"get"},
        f"{PREFIX}/{{activation_id}}/revoke": {"post"},
    }
    assert set(paths) == set(expected)
    for path, methods in expected.items():
        assert set(paths[path]) == methods
        for method in methods:
            operation = paths[path][method]
            assert operation["security"] == [{"HTTPBearer": []}]
            assert operation["tags"] == ["admin operational paper-session activations"]
            success = "201" if path == PREFIX and method == "post" else "200"
            assert set(operation["responses"]) == {
                success,
                *(str(code) for code in ADMIN_ERROR_RESPONSES),
            }
            response_name = (
                "OperationalPaperSessionActivationListResponse"
                if path == PREFIX and method == "get"
                else "OperationalPaperSessionActivationResponse"
            )
            assert operation["responses"][success]["content"]["application/json"]["schema"] == {
                "$ref": f"#/components/schemas/{response_name}"
            }
            if "{activation_id}" in path:
                parameter = next(p for p in operation["parameters"] if p["name"] == "activation_id")
                assert parameter["in"] == "path"
                assert parameter["required"] is True
                assert parameter["schema"]["format"] == "uuid"
    assert (
        sum(
            getattr(route, "original_router", None)
            is admin_operational_paper_session_activations.router
            for route in application.routes
        )
        == 1
    )
    schemas = schema["components"]["schemas"]
    for model, fields, path in (
        (
            OperationalPaperSessionActivationAuthorizeRequest,
            {"materialization_id", "idempotency_key"},
            PREFIX,
        ),
        (
            OperationalPaperSessionActivationRevokeRequest,
            {"expected_record_version"},
            f"{PREFIX}/{{activation_id}}/revoke",
        ),
    ):
        contract = schemas[model.__name__]
        assert set(model.model_fields) == fields
        assert set(contract["properties"]) == set(contract["required"]) == fields
        assert contract["additionalProperties"] is False
        assert paths[path]["post"]["requestBody"]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{model.__name__}"
        }
    authorize = schemas["OperationalPaperSessionActivationAuthorizeRequest"]["properties"]
    assert authorize["materialization_id"]["format"] == "uuid"
    key = authorize["idempotency_key"]
    assert (key["minLength"], key["maxLength"], key["pattern"]) == (
        1,
        128,
        r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    assert (
        schemas["OperationalPaperSessionActivationRevokeRequest"]["properties"][
            "expected_record_version"
        ]["minimum"]
        == 1
    )
    query = {p["name"]: p["schema"] for p in paths[PREFIX]["get"]["parameters"]}
    assert set(query) == {"limit", "offset", "state", "materialization_id"}
    for name, low, high, default in (("limit", 1, 100, 20), ("offset", 0, 1_000_000, 0)):
        assert (query[name]["minimum"], query[name]["maximum"], query[name]["default"]) == (
            low,
            high,
            default,
        )
    assert {"type": "string", "format": "uuid"} in query["materialization_id"]["anyOf"]
    assert {"$ref": "#/components/schemas/OperationalPaperSessionActivationState"} in query[
        "state"
    ]["anyOf"]
    assert schemas["OperationalPaperSessionActivationState"]["enum"] == ["AUTHORIZED", "REVOKED"]
    response = schemas["OperationalPaperSessionActivationResponse"]
    assert set(response["properties"]) == set(_expected_response(_aggregate()))
    for field, binding in (
        ("authorization_binding", "Authorization"),
        ("profile_binding", "Profile"),
        ("mandate_binding", "Mandate"),
    ):
        assert response["properties"][field] == {
            "$ref": (
                f"#/components/schemas/OperationalPaperSessionMaterialization{binding}BindingResponse"
            )
        }
