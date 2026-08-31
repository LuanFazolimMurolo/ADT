"""Remote-free contract tests for the operational paper-capital authorization admin API."""

from __future__ import annotations

import ast
import inspect
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final, cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.resources import (
    get_admin_service,
    get_jwt_verifier,
    get_operational_paper_capital_authorization_service,
)
from app.api.routes import admin_operational_paper_capital_authorizations
from app.database import Database
from app.domain.errors import (
    PersistenceError,
    PersistenceUnavailableError,
    SimulationNotFoundError,
    SimulationTerminalError,
)
from app.main import create_app
from app.operational_paper_capital_authorizations import (
    OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_SCHEMA_VERSION,
    InvalidOperationalPaperCapitalAuthorizationSpecificationError,
    OperationalPaperCapitalAuthorization,
    OperationalPaperCapitalAuthorizationActiveProfileConflictError,
    OperationalPaperCapitalAuthorizationBoundsExceededError,
    OperationalPaperCapitalAuthorizationCreateIntent,
    OperationalPaperCapitalAuthorizationCurrencyMismatchError,
    OperationalPaperCapitalAuthorizationIdempotencyConflictError,
    OperationalPaperCapitalAuthorizationInsufficientAvailableCapitalError,
    OperationalPaperCapitalAuthorizationNotFoundError,
    OperationalPaperCapitalAuthorizationProfileBinding,
    OperationalPaperCapitalAuthorizationProfileStateConflictError,
    OperationalPaperCapitalAuthorizationRecordVersionConflictError,
    OperationalPaperCapitalAuthorizationState,
    OperationalPaperCapitalAuthorizationStateTransitionConflictError,
    OperationalPaperCapitalReservationConflictError,
    build_operational_paper_capital_authorization_specification,
    operational_paper_capital_authorization_create_intent_fingerprint,
    operational_paper_capital_authorization_specification_checksum,
)
from app.repositories.operational_paper_capital_authorizations import (
    PostgresOperationalPaperCapitalAuthorizationRepository,
)
from app.services import OperationalPaperCapitalAuthorizationService

PREFIX: Final = "/api/v1/admin/operational-paper-capital-authorizations"
ADMIN_ID: Final = UUID("10000000-0000-4000-8000-000000000001")
OTHER_ACTOR_ID: Final = UUID("10000000-0000-4000-8000-000000000002")
AUTHORIZATION_ID: Final = UUID("20000000-0000-4000-8000-000000000001")
PROFILE_ID: Final = UUID("30000000-0000-4000-8000-000000000001")
SIMULATION_ID: Final = UUID("40000000-0000-4000-8000-000000000001")
NOW: Final = datetime(2026, 8, 28, 18, 0, tzinfo=UTC)
AUTH_HEADERS: Final = {"Authorization": "Bearer phase-7-08-gate-3-token"}
IDEMPOTENCY_KEY: Final = "gate-3-capital-auth-create-1"
PROFILE_CHECKSUM: Final = "a" * 64
AUTHORIZED_CAPITAL_TEXT: Final = "100.12345678"


def _binding() -> OperationalPaperCapitalAuthorizationProfileBinding:
    return OperationalPaperCapitalAuthorizationProfileBinding(
        profile_id=PROFILE_ID,
        approved_revision=3,
        specification_checksum=PROFILE_CHECKSUM,
    )


def _intent() -> OperationalPaperCapitalAuthorizationCreateIntent:
    return OperationalPaperCapitalAuthorizationCreateIntent(
        profile_binding=_binding(),
        simulation_id=SIMULATION_ID,
        quote_asset="USDT",
        authorized_capital=Decimal(AUTHORIZED_CAPITAL_TEXT),
    )


def _authorization(
    state: OperationalPaperCapitalAuthorizationState = (
        OperationalPaperCapitalAuthorizationState.AUTHORIZED
    ),
) -> OperationalPaperCapitalAuthorization:
    intent = _intent()
    specification = build_operational_paper_capital_authorization_specification(intent)
    revoked = state is OperationalPaperCapitalAuthorizationState.REVOKED
    return OperationalPaperCapitalAuthorization(
        authorization_id=AUTHORIZATION_ID,
        schema_version=OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_SCHEMA_VERSION,
        state=state,
        record_version=2 if revoked else 1,
        profile_binding=intent.profile_binding,
        simulation_id=intent.simulation_id,
        quote_asset=intent.quote_asset,
        authorized_capital=intent.authorized_capital,
        authorization_checksum=(
            operational_paper_capital_authorization_specification_checksum(specification)
        ),
        created_by=ADMIN_ID,
        created_at=NOW,
        revoked_by=OTHER_ACTOR_ID if revoked else None,
        revoked_at=NOW + timedelta(minutes=1) if revoked else None,
        create_idempotency_key=IDEMPOTENCY_KEY,
        create_intent_fingerprint=(
            operational_paper_capital_authorization_create_intent_fingerprint(intent)
        ),
    )


def _create_payload(
    *,
    authorized_capital: object = AUTHORIZED_CAPITAL_TEXT,
    quote_asset: object = " usdt ",
    approved_revision: object = 3,
) -> dict[str, object]:
    return {
        "intent": {
            "profile_binding": {
                "profile_id": str(PROFILE_ID),
                "approved_revision": approved_revision,
                "specification_checksum": PROFILE_CHECKSUM,
            },
            "simulation_id": str(SIMULATION_ID),
            "quote_asset": quote_asset,
            "authorized_capital": authorized_capital,
        },
        "idempotency_key": IDEMPOTENCY_KEY,
    }


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


class FakeOperationalPaperCapitalAuthorizationService:
    """Record route-to-service calls without retesting repository behavior."""

    def __init__(self) -> None:
        self.method_calls: list[str] = []
        self.list_calls: list[
            tuple[int, int, OperationalPaperCapitalAuthorizationState | None]
        ] = []
        self.get_calls: list[UUID] = []
        self.create_calls: list[
            tuple[OperationalPaperCapitalAuthorizationCreateIntent, UUID, str]
        ] = []
        self.revoke_calls: list[tuple[UUID, int, UUID]] = []
        self.error: Exception | None = None

    def _raise_configured_error(self) -> None:
        if self.error is not None:
            raise self.error

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: OperationalPaperCapitalAuthorizationState | None = None,
    ) -> tuple[list[OperationalPaperCapitalAuthorization], int]:
        self.method_calls.append("list")
        self.list_calls.append((limit, offset, state))
        self._raise_configured_error()
        return [_authorization()], 7

    async def get(
        self,
        authorization_id: UUID,
    ) -> OperationalPaperCapitalAuthorization:
        self.method_calls.append("get")
        self.get_calls.append(authorization_id)
        self._raise_configured_error()
        return _authorization()

    async def create(
        self,
        intent: OperationalPaperCapitalAuthorizationCreateIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
    ) -> OperationalPaperCapitalAuthorization:
        self.method_calls.append("create")
        self.create_calls.append((intent, actor_id, idempotency_key))
        self._raise_configured_error()
        return _authorization()

    async def revoke(
        self,
        authorization_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
    ) -> OperationalPaperCapitalAuthorization:
        self.method_calls.append("revoke")
        self.revoke_calls.append((authorization_id, expected_record_version, actor_id))
        self._raise_configured_error()
        return _authorization(OperationalPaperCapitalAuthorizationState.REVOKED)


ApiFixture = tuple[
    FastAPI,
    FakeOperationalPaperCapitalAuthorizationService,
    FakeJWTVerifier,
    FakeAdminService,
]


@pytest.fixture
def api() -> ApiFixture:
    application = create_app()
    service = FakeOperationalPaperCapitalAuthorizationService()
    verifier = FakeJWTVerifier()
    admin_service = FakeAdminService()

    async def verifier_override() -> FakeJWTVerifier:
        return verifier

    async def admin_service_override() -> FakeAdminService:
        return admin_service

    async def authorization_service_override() -> FakeOperationalPaperCapitalAuthorizationService:
        return service

    application.dependency_overrides[get_jwt_verifier] = verifier_override
    application.dependency_overrides[get_admin_service] = admin_service_override
    application.dependency_overrides[get_operational_paper_capital_authorization_service] = (
        authorization_service_override
    )
    return application, service, verifier, admin_service


@pytest.fixture
async def client(api: ApiFixture) -> AsyncIterator[AsyncClient]:
    application, _service, _verifier, _admin_service = api
    async with AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://gate-3.test",
    ) as http_client:
        yield http_client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", PREFIX, None),
        ("POST", PREFIX, _create_payload()),
        ("GET", f"{PREFIX}/{AUTHORIZATION_ID}", None),
        (
            "POST",
            f"{PREFIX}/{AUTHORIZATION_ID}/revoke",
            {"expected_record_version": 1},
        ),
    ],
)
async def test_all_four_operations_require_authentication(
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
        ("GET", f"{PREFIX}/{AUTHORIZATION_ID}", None),
        (
            "POST",
            f"{PREFIX}/{AUTHORIZATION_ID}/revoke",
            {"expected_record_version": 1},
        ),
    ],
)
async def test_all_four_operations_require_administrator_membership(
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
    assert api[2].tokens == ["phase-7-08-gate-3-token"]
    assert api[3].checked_users == [ADMIN_ID]


@pytest.mark.asyncio
async def test_list_defaults_serialize_exact_decimal_hide_replay_internals_and_no_store(
    client: AsyncClient,
    api: ApiFixture,
) -> None:
    response = await client.get(PREFIX, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert api[1].list_calls == [(20, 0, None)]
    body = response.json()
    assert (body["limit"], body["offset"], body["total"]) == (20, 0, 7)
    item = body["items"][0]
    assert item["authorization_id"] == str(AUTHORIZATION_ID)
    assert item["state"] == "AUTHORIZED"
    assert item["authorized_capital"] == AUTHORIZED_CAPITAL_TEXT
    assert isinstance(item["authorized_capital"], str)
    assert item["profile_binding"] == {
        "profile_id": str(PROFILE_ID),
        "approved_revision": 3,
        "specification_checksum": PROFILE_CHECKSUM,
    }
    assert "create_idempotency_key" not in item
    assert "create_intent_fingerprint" not in item
    assert "idempotency_key" not in item
    assert set(item) == {
        "authorization_id",
        "schema_version",
        "state",
        "record_version",
        "profile_binding",
        "simulation_id",
        "quote_asset",
        "authorized_capital",
        "authorization_checksum",
        "created_by",
        "created_at",
        "revoked_by",
        "revoked_at",
    }


@pytest.mark.asyncio
async def test_list_forwards_explicit_pagination_and_state(
    client: AsyncClient,
    api: ApiFixture,
) -> None:
    response = await client.get(
        PREFIX,
        headers=AUTH_HEADERS,
        params={"limit": 7, "offset": 4, "state": "REVOKED"},
    )

    assert response.status_code == 200
    assert api[1].list_calls == [(7, 4, OperationalPaperCapitalAuthorizationState.REVOKED)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limit", "offset", "state"),
    [
        (1, 0, "AUTHORIZED"),
        (100, 1_000_000, "REVOKED"),
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
    assert api[1].list_calls == [(limit, offset, OperationalPaperCapitalAuthorizationState(state))]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"offset": 1_000_001},
        {"state": "DRAFT"},
    ],
)
async def test_list_rejects_values_outside_transport_contract_before_service(
    client: AsyncClient,
    api: ApiFixture,
    params: dict[str, object],
) -> None:
    response = await client.get(PREFIX, headers=AUTH_HEADERS, params=params)

    assert response.status_code == 422
    assert api[1].method_calls == []
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_get_forwards_uuid_serializes_response_and_sets_no_store(
    client: AsyncClient,
    api: ApiFixture,
) -> None:
    response = await client.get(
        f"{PREFIX}/{AUTHORIZATION_ID}",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert api[1].get_calls == [AUTHORIZATION_ID]
    assert response.json()["authorization_checksum"] == (_authorization().authorization_checksum)

    malformed = await client.get(f"{PREFIX}/not-a-uuid", headers=AUTH_HEADERS)
    assert malformed.status_code == 422
    assert api[1].get_calls == [AUTHORIZATION_ID]


@pytest.mark.asyncio
async def test_create_builds_domain_intent_uses_authenticated_actor_and_returns_201(
    client: AsyncClient,
    api: ApiFixture,
) -> None:
    response = await client.post(
        PREFIX,
        headers=AUTH_HEADERS,
        json=_create_payload(),
    )

    assert response.status_code == 201
    assert len(api[1].create_calls) == 1
    intent, actor_id, idempotency_key = api[1].create_calls[0]
    assert intent.profile_binding == _binding()
    assert intent.simulation_id == SIMULATION_ID
    assert intent.quote_asset == "USDT"
    assert intent.authorized_capital == Decimal(AUTHORIZED_CAPITAL_TEXT)
    assert actor_id == ADMIN_ID
    assert idempotency_key == IDEMPOTENCY_KEY
    body = response.json()
    assert body["authorized_capital"] == AUTHORIZED_CAPITAL_TEXT
    assert "create_idempotency_key" not in body
    assert "create_intent_fingerprint" not in body


@pytest.mark.asyncio
async def test_create_replay_keeps_201_contract(
    client: AsyncClient,
    api: ApiFixture,
) -> None:
    first = await client.post(PREFIX, headers=AUTH_HEADERS, json=_create_payload())
    second = await client.post(PREFIX, headers=AUTH_HEADERS, json=_create_payload())

    assert first.status_code == second.status_code == 201
    assert len(api[1].create_calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("authorized_capital", ["100.00000000", "0.00000001"])
async def test_create_preserves_legal_exact_decimal_quantums(
    client: AsyncClient,
    api: ApiFixture,
    authorized_capital: str,
) -> None:
    response = await client.post(
        PREFIX,
        headers=AUTH_HEADERS,
        json=_create_payload(authorized_capital=authorized_capital),
    )

    assert response.status_code == 201
    assert api[1].create_calls[0][0].authorized_capital == Decimal(authorized_capital)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authorized_capital",
    [
        100.125,
        "0",
        "-1",
        "1.000000001",
        "1000000000000",
        "NaN",
        "Infinity",
        "-Infinity",
        "1e2",
    ],
)
async def test_create_requires_positive_bounded_base10_decimal_string(
    client: AsyncClient,
    api: ApiFixture,
    authorized_capital: object,
) -> None:
    response = await client.post(
        PREFIX,
        headers=AUTH_HEADERS,
        json=_create_payload(authorized_capital=authorized_capital),
    )

    assert response.status_code == 422
    assert api[1].method_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (
            _create_payload(approved_revision=2**63),
            "operational_paper_capital_authorization_bounds_exceeded",
        ),
        (
            _create_payload(quote_asset="bad asset"),
            "operational_paper_capital_authorization_invalid_specification",
        ),
    ],
)
async def test_create_preserves_domain_validation_boundary_as_safe_400(
    client: AsyncClient,
    api: ApiFixture,
    payload: dict[str, object],
    expected_code: str,
) -> None:
    response = await client.post(PREFIX, headers=AUTH_HEADERS, json=payload)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == expected_code
    assert api[1].method_calls == []


@pytest.mark.asyncio
async def test_create_rejects_extra_fields_and_invalid_idempotency_key_before_service(
    client: AsyncClient,
    api: ApiFixture,
) -> None:
    with_extra = _create_payload()
    with_extra["actor_id"] = str(OTHER_ACTOR_ID)
    extra = await client.post(PREFIX, headers=AUTH_HEADERS, json=with_extra)

    invalid_key = _create_payload()
    invalid_key["idempotency_key"] = "contains spaces"
    invalid = await client.post(PREFIX, headers=AUTH_HEADERS, json=invalid_key)

    empty_key = _create_payload()
    empty_key["idempotency_key"] = ""
    empty = await client.post(PREFIX, headers=AUTH_HEADERS, json=empty_key)

    long_key = _create_payload()
    long_key["idempotency_key"] = "a" * 129
    too_long = await client.post(PREFIX, headers=AUTH_HEADERS, json=long_key)

    persistence_internal = _create_payload()
    persistence_internal["create_intent_fingerprint"] = "b" * 64
    internal = await client.post(
        PREFIX,
        headers=AUTH_HEADERS,
        json=persistence_internal,
    )

    assert {response.status_code for response in (extra, invalid, empty, too_long, internal)} == {
        422
    }
    assert api[1].method_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["created_by", "revoked_by", "created_at", "revoked_at"])
async def test_create_and_revoke_reject_spoofed_audit_authority_fields(
    client: AsyncClient,
    api: ApiFixture,
    field: str,
) -> None:
    create_payload = _create_payload()
    create_payload[field] = "client-controlled"
    create_response = await client.post(PREFIX, headers=AUTH_HEADERS, json=create_payload)
    revoke_response = await client.post(
        f"{PREFIX}/{AUTHORIZATION_ID}/revoke",
        headers=AUTH_HEADERS,
        json={"expected_record_version": 1, field: "client-controlled"},
    )

    assert create_response.status_code == revoke_response.status_code == 422
    assert api[1].method_calls == []


@pytest.mark.asyncio
async def test_create_rejects_malformed_transport_binding_and_types_before_service(
    client: AsyncClient,
    api: ApiFixture,
) -> None:
    payloads: list[dict[str, object]] = []

    for revision in (0, True):
        payloads.append(_create_payload(approved_revision=revision))

    malformed_checksum = _create_payload()
    malformed_checksum["intent"]["profile_binding"]["specification_checksum"] = "A" * 64  # type: ignore[index]
    payloads.append(malformed_checksum)

    malformed_profile_id = _create_payload()
    malformed_profile_id["intent"]["profile_binding"]["profile_id"] = "not-a-uuid"  # type: ignore[index]
    payloads.append(malformed_profile_id)

    malformed_simulation_id = _create_payload()
    malformed_simulation_id["intent"]["simulation_id"] = "not-a-uuid"  # type: ignore[index]
    payloads.append(malformed_simulation_id)

    payloads.append(_create_payload(quote_asset=123))

    responses = [
        await client.post(PREFIX, headers=AUTH_HEADERS, json=payload) for payload in payloads
    ]

    assert {response.status_code for response in responses} == {422}
    assert api[1].method_calls == []


@pytest.mark.asyncio
async def test_revoke_forwards_only_concurrency_token_and_authenticated_actor(
    client: AsyncClient,
    api: ApiFixture,
) -> None:
    response = await client.post(
        f"{PREFIX}/{AUTHORIZATION_ID}/revoke",
        headers=AUTH_HEADERS,
        json={"expected_record_version": 7},
    )

    assert response.status_code == 200
    assert api[1].revoke_calls == [(AUTHORIZATION_ID, 7, ADMIN_ID)]
    body = response.json()
    assert body["state"] == "REVOKED"
    assert body["record_version"] == 2
    assert body["revoked_by"] == str(OTHER_ACTOR_ID)
    assert body["revoked_at"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"expected_record_version": True},
        {"expected_record_version": "7"},
        {"expected_record_version": 0},
        {"expected_record_version": -1},
        {},
        {"expected_record_version": 7, "actor_id": str(OTHER_ACTOR_ID)},
    ],
)
async def test_revoke_rejects_invalid_or_extra_client_authority_fields(
    client: AsyncClient,
    api: ApiFixture,
    payload: dict[str, object],
) -> None:
    response = await client.post(
        f"{PREFIX}/{AUTHORIZATION_ID}/revoke",
        headers=AUTH_HEADERS,
        json=payload,
    )

    assert response.status_code == 422
    assert api[1].method_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload", "error", "status_code", "code"),
    [
        (
            "POST",
            PREFIX,
            _create_payload(quote_asset="bad asset"),
            InvalidOperationalPaperCapitalAuthorizationSpecificationError(),
            400,
            "operational_paper_capital_authorization_invalid_specification",
        ),
        (
            "POST",
            PREFIX,
            _create_payload(),
            OperationalPaperCapitalAuthorizationBoundsExceededError(),
            400,
            "operational_paper_capital_authorization_bounds_exceeded",
        ),
        (
            "GET",
            f"{PREFIX}/{AUTHORIZATION_ID}",
            None,
            OperationalPaperCapitalAuthorizationNotFoundError(),
            404,
            "operational_paper_capital_authorization_not_found",
        ),
        (
            "POST",
            f"{PREFIX}/{AUTHORIZATION_ID}/revoke",
            {"expected_record_version": 7},
            OperationalPaperCapitalAuthorizationRecordVersionConflictError(),
            409,
            "operational_paper_capital_authorization_record_version_conflict",
        ),
        (
            "POST",
            f"{PREFIX}/{AUTHORIZATION_ID}/revoke",
            {"expected_record_version": 7},
            OperationalPaperCapitalAuthorizationStateTransitionConflictError(),
            409,
            "operational_paper_capital_authorization_state_transition_conflict",
        ),
        (
            "POST",
            PREFIX,
            _create_payload(),
            OperationalPaperCapitalAuthorizationIdempotencyConflictError(),
            409,
            "operational_paper_capital_authorization_idempotency_conflict",
        ),
        (
            "POST",
            PREFIX,
            _create_payload(),
            OperationalPaperCapitalAuthorizationProfileStateConflictError(),
            409,
            "operational_paper_capital_authorization_profile_state_conflict",
        ),
        (
            "POST",
            PREFIX,
            _create_payload(),
            OperationalPaperCapitalAuthorizationActiveProfileConflictError(),
            409,
            "operational_paper_capital_authorization_active_profile_conflict",
        ),
        (
            "POST",
            PREFIX,
            _create_payload(),
            OperationalPaperCapitalAuthorizationCurrencyMismatchError(),
            409,
            "operational_paper_capital_authorization_currency_mismatch",
        ),
        (
            "POST",
            PREFIX,
            _create_payload(),
            OperationalPaperCapitalAuthorizationInsufficientAvailableCapitalError(),
            409,
            "operational_paper_capital_authorization_insufficient_available_capital",
        ),
        (
            "POST",
            PREFIX,
            _create_payload(),
            OperationalPaperCapitalReservationConflictError(),
            409,
            "operational_paper_capital_reservation_conflict",
        ),
        (
            "POST",
            PREFIX,
            _create_payload(),
            SimulationNotFoundError(),
            404,
            "simulation_not_found",
        ),
        (
            "POST",
            PREFIX,
            _create_payload(),
            SimulationTerminalError(),
            409,
            "simulation_terminal",
        ),
        (
            "GET",
            PREFIX,
            None,
            PersistenceError(),
            500,
            "persistence_error",
        ),
        (
            "GET",
            PREFIX,
            None,
            PersistenceUnavailableError(),
            503,
            "database_unavailable",
        ),
    ],
)
async def test_domain_and_persistence_errors_use_global_safe_http_contract(
    client: AsyncClient,
    api: ApiFixture,
    method: str,
    path: str,
    payload: dict[str, object] | None,
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    error.__cause__ = RuntimeError("PRIVATE_DATABASE_CAUSE")
    api[1].error = error

    response = await client.request(
        method,
        path,
        headers=AUTH_HEADERS,
        json=payload,
    )

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
    assert "PRIVATE_DATABASE_CAUSE" not in response.text
    assert "Traceback" not in response.text


@pytest.mark.asyncio
async def test_validation_error_envelope_is_stable_and_does_not_echo_raw_input(
    client: AsyncClient,
    api: ApiFixture,
) -> None:
    raw_value = "PRIVATE_INVALID_CHECKSUM_VALUE"
    payload = _create_payload()
    payload["intent"]["profile_binding"]["specification_checksum"] = raw_value  # type: ignore[index]

    response = await client.post(PREFIX, headers=AUTH_HEADERS, json=payload)

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "Request validation failed."
    assert isinstance(body["error"]["details"], list)
    assert all(set(detail) <= {"code", "message", "field"} for detail in body["error"]["details"])
    assert raw_value not in response.text
    assert api[1].method_calls == []


def test_route_module_has_no_persistence_or_calculation_authority() -> None:
    source = inspect.getsource(admin_operational_paper_capital_authorizations)
    tree = ast.parse(source)
    revoke_tree = ast.parse(
        inspect.getsource(
            admin_operational_paper_capital_authorizations.revoke_operational_paper_capital_authorization
        )
    )
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert {
        "PostgresOperationalPaperCapitalAuthorizationRepository",
        "Database",
        "psycopg",
        "sql",
    }.isdisjoint(imported_names)
    assert {
        "execute",
        "transaction",
        "lock",
        "retry",
        "checksum",
        "fingerprint",
    }.isdisjoint(called_attributes)
    assert "from app.services import OperationalPaperCapitalAuthorizationService" in source
    assert "PostgresOperationalPaperCapitalAuthorizationRepository" not in source
    assert "SELECT " not in source.upper()
    assert "INSERT " not in source.upper()
    assert "UPDATE " not in source.upper()
    assert "DELETE " not in source.upper()
    assert [
        node.func.attr
        for node in ast.walk(revoke_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "service"
    ] == ["revoke"]


def test_dependency_factory_builds_concrete_service_repository_and_utc_clock() -> None:
    database = cast(Database, object())

    service = get_operational_paper_capital_authorization_service(database=database)

    assert isinstance(service, OperationalPaperCapitalAuthorizationService)
    assert isinstance(
        service._repository,
        PostgresOperationalPaperCapitalAuthorizationRepository,
    )
    assert service._repository._database is database
    now = service._clock()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_router_and_openapi_expose_exactly_four_protected_operations() -> None:
    application = create_app()
    schema = application.openapi()
    paths = cast(dict[str, dict[str, object]], schema["paths"])

    authorization_paths = {path: item for path, item in paths.items() if path.startswith(PREFIX)}
    expected = {
        PREFIX: {"get", "post"},
        f"{PREFIX}/{{authorization_id}}": {"get"},
        f"{PREFIX}/{{authorization_id}}/revoke": {"post"},
    }

    assert set(authorization_paths) == set(expected)
    operation_count = 0
    for path, methods in expected.items():
        path_item = authorization_paths[path]
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
            responses = cast(dict[str, object], operation["responses"])
            assert {"400", "401", "403", "404", "409", "422", "500", "503"} <= set(responses)
    assert operation_count == 4

    components = cast(dict[str, object], schema["components"])
    schemas = cast(dict[str, dict[str, object]], components["schemas"])
    intent_schema = schemas["OperationalPaperCapitalAuthorizationIntentRequest"]
    properties = cast(dict[str, dict[str, object]], intent_schema["properties"])
    assert properties["authorized_capital"]["type"] == "string"
    assert "create_intent_fingerprint" not in properties

    assert (
        sum(
            getattr(route, "original_router", None)
            is admin_operational_paper_capital_authorizations.router
            for route in application.routes
        )
        == 1
    )
