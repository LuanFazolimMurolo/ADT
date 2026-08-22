"""Remote-free contract tests for the operational-mandate administrator API."""

from __future__ import annotations

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
    get_operational_mandate_service,
)
from app.api.routes import admin_operational_mandates
from app.database import Database
from app.domain.errors import PersistenceError
from app.main import create_app
from app.market_data.domain import Exchange, MarketType, TradingPair
from app.operational_mandates import (
    OPERATIONAL_MANDATE_SPEC_SCHEMA_VERSION,
    OperationalMandate,
    OperationalMandateInstrument,
    OperationalMandateRevision,
    OperationalMandateSpecification,
    OperationalMandateState,
    operational_mandate_specification_checksum,
)
from app.operational_mandates.errors import (
    OperationalMandateNotFoundError,
    OperationalMandateRecordVersionConflictError,
)
from app.repositories.operational_mandates import PostgresOperationalMandateRepository
from app.services import OperationalMandateService

PREFIX: Final = "/api/v1/admin/operational-mandates"
ADMIN_ID: Final = UUID("10000000-0000-4000-8000-000000000001")
OTHER_ACTOR_ID: Final = UUID("10000000-0000-4000-8000-000000000002")
MANDATE_ID: Final = UUID("20000000-0000-4000-8000-000000000001")
NOW: Final = datetime(2026, 8, 22, 18, 0, tzinfo=UTC)
AUTH_HEADERS: Final = {"Authorization": "Bearer phase-7-06-gate-3-token"}
IDEMPOTENCY_KEY: Final = "gate-3-create-1"
OperationalMandateCurrent = tuple[OperationalMandate, OperationalMandateRevision]
OperationalMandatePage = tuple[list[OperationalMandateCurrent], int]
OperationalMandateRevisionPage = tuple[list[OperationalMandateRevision], int]


def _specification(*, name: str = "Core spot mandate") -> OperationalMandateSpecification:
    return OperationalMandateSpecification(
        schema_version=OPERATIONAL_MANDATE_SPEC_SCHEMA_VERSION,
        name=name,
        description="Canonical BTC and ETH spot authorization.",
        instruments=(
            OperationalMandateInstrument(
                exchange=Exchange.BINANCE,
                market_type=MarketType.SPOT,
                pair=TradingPair("ETH", "USDT"),
            ),
            OperationalMandateInstrument(
                exchange=Exchange.BINANCE,
                market_type=MarketType.SPOT,
                pair=TradingPair("BTC", "USDT"),
            ),
        ),
    )


def _revision(
    specification: OperationalMandateSpecification | None = None,
    *,
    revision: int = 1,
) -> OperationalMandateRevision:
    selected = specification or _specification()
    return OperationalMandateRevision(
        mandate_id=MANDATE_ID,
        revision=revision,
        specification=selected,
        specification_checksum=operational_mandate_specification_checksum(selected),
        created_by=ADMIN_ID,
        created_at=NOW,
    )


def _mandate(
    state: OperationalMandateState = OperationalMandateState.DRAFT,
) -> OperationalMandate:
    checksum = operational_mandate_specification_checksum(_specification())
    approved = state in {
        OperationalMandateState.APPROVED,
        OperationalMandateState.ARCHIVED,
    }
    archived = state is OperationalMandateState.ARCHIVED
    return OperationalMandate(
        mandate_id=MANDATE_ID,
        state=state,
        current_revision=1,
        record_version=1 + int(approved) + int(archived),
        approved_revision=1 if approved else None,
        approved_checksum=checksum if approved else None,
        created_by=ADMIN_ID,
        created_at=NOW,
        approved_by=ADMIN_ID if approved else None,
        approved_at=NOW + timedelta(minutes=1) if approved else None,
        archived_by=ADMIN_ID if archived else None,
        archived_at=NOW + timedelta(minutes=2) if archived else None,
        create_idempotency_key=IDEMPOTENCY_KEY,
        create_request_fingerprint="a" * 64,
    )


def _current() -> tuple[OperationalMandate, OperationalMandateRevision]:
    return _mandate(), _revision()


def _specification_payload(*, market_type: str = "spot") -> dict[str, object]:
    return {
        "schema_version": 1,
        "name": "  Core spot mandate  ",
        "description": "  Canonical BTC and ETH spot authorization.  ",
        "instruments": [
            {
                "exchange": "binance",
                "market_type": market_type,
                "base_asset": " btc ",
                "quote_asset": "usdt",
            },
            {
                "exchange": "binance",
                "market_type": market_type,
                "base_asset": "ETH",
                "quote_asset": "USDT",
            },
        ],
    }


class FakeJWTVerifier:
    """Record the opaque bearer token and return one durable subject UUID."""

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


class FakeOperationalMandateService:
    """Record the exact route-to-service contract without repository retesting."""

    def __init__(self) -> None:
        self.list_call: tuple[int, int, OperationalMandateState | None] | None = None
        self.get_calls: list[UUID] = []
        self.revision_list_call: tuple[UUID, int, int] | None = None
        self.get_revision_call: tuple[UUID, int] | None = None
        self.create_call: tuple[OperationalMandateSpecification, UUID, str] | None = None
        self.replace_call: tuple[UUID, OperationalMandateSpecification, int, int, UUID] | None = (
            None
        )
        self.approve_call: tuple[UUID, int, str, int, UUID] | None = None
        self.archive_call: tuple[UUID, int, UUID] | None = None
        self.error: Exception | None = None

    def _raise_configured_error(self) -> None:
        if self.error is not None:
            raise self.error

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: OperationalMandateState | None = None,
    ) -> OperationalMandatePage:
        self.list_call = (limit, offset, state)
        self._raise_configured_error()
        return [_current()], 9

    async def get(
        self,
        mandate_id: UUID,
    ) -> tuple[OperationalMandate, OperationalMandateRevision]:
        self.get_calls.append(mandate_id)
        self._raise_configured_error()
        return _current()

    async def list_revisions(
        self,
        mandate_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> OperationalMandateRevisionPage:
        self.revision_list_call = (mandate_id, limit, offset)
        self._raise_configured_error()
        return [_revision()], 3

    async def get_revision(
        self,
        mandate_id: UUID,
        revision: int,
    ) -> OperationalMandateRevision:
        self.get_revision_call = (mandate_id, revision)
        self._raise_configured_error()
        return _revision(revision=revision)

    async def create(
        self,
        specification: OperationalMandateSpecification,
        *,
        actor_id: UUID,
        idempotency_key: str,
    ) -> tuple[OperationalMandate, OperationalMandateRevision]:
        self.create_call = (specification, actor_id, idempotency_key)
        self._raise_configured_error()
        return _mandate(), _revision(specification)

    async def replace_draft(
        self,
        mandate_id: UUID,
        specification: OperationalMandateSpecification,
        *,
        expected_revision: int,
        expected_record_version: int,
        actor_id: UUID,
    ) -> tuple[OperationalMandate, OperationalMandateRevision]:
        self.replace_call = (
            mandate_id,
            specification,
            expected_revision,
            expected_record_version,
            actor_id,
        )
        self._raise_configured_error()
        return _current()

    async def approve(
        self,
        mandate_id: UUID,
        *,
        expected_revision: int,
        expected_checksum: str,
        expected_record_version: int,
        actor_id: UUID,
    ) -> OperationalMandate:
        self.approve_call = (
            mandate_id,
            expected_revision,
            expected_checksum,
            expected_record_version,
            actor_id,
        )
        self._raise_configured_error()
        return _mandate(OperationalMandateState.APPROVED)

    async def archive(
        self,
        mandate_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
    ) -> OperationalMandate:
        self.archive_call = (mandate_id, expected_record_version, actor_id)
        self._raise_configured_error()
        return _mandate(OperationalMandateState.ARCHIVED)


@pytest.fixture
def api() -> tuple[
    FastAPI,
    FakeOperationalMandateService,
    FakeJWTVerifier,
    FakeAdminService,
]:
    application = create_app()
    service = FakeOperationalMandateService()
    verifier = FakeJWTVerifier()
    admin_service = FakeAdminService()

    async def verifier_override() -> FakeJWTVerifier:
        return verifier

    async def admin_service_override() -> FakeAdminService:
        return admin_service

    async def mandate_service_override() -> FakeOperationalMandateService:
        return service

    application.dependency_overrides[get_jwt_verifier] = verifier_override
    application.dependency_overrides[get_admin_service] = admin_service_override
    application.dependency_overrides[get_operational_mandate_service] = mandate_service_override
    return application, service, verifier, admin_service


@pytest.fixture
async def client(
    api: tuple[
        FastAPI,
        FakeOperationalMandateService,
        FakeJWTVerifier,
        FakeAdminService,
    ],
) -> AsyncIterator[AsyncClient]:
    application, _service, _verifier, _admin_service = api
    async with AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://gate-3.test",
    ) as http_client:
        yield http_client


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected_before_service(
    client: AsyncClient,
    api: tuple[FastAPI, FakeOperationalMandateService, FakeJWTVerifier, FakeAdminService],
) -> None:
    response = await client.get(PREFIX)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert api[1].list_call is None
    assert api[2].tokens == []
    assert api[3].checked_users == []


@pytest.mark.asyncio
async def test_authenticated_non_admin_is_rejected_before_service(
    client: AsyncClient,
    api: tuple[FastAPI, FakeOperationalMandateService, FakeJWTVerifier, FakeAdminService],
) -> None:
    api[3].allowed = False

    response = await client.get(PREFIX, headers=AUTH_HEADERS)

    assert response.status_code == 403
    assert api[1].list_call is None
    assert api[2].tokens == ["phase-7-06-gate-3-token"]
    assert api[3].checked_users == [ADMIN_ID]


@pytest.mark.asyncio
async def test_list_forwards_bounds_filter_total_and_sets_no_store(
    client: AsyncClient,
    api: tuple[FastAPI, FakeOperationalMandateService, FakeJWTVerifier, FakeAdminService],
) -> None:
    response = await client.get(
        PREFIX,
        headers=AUTH_HEADERS,
        params={"limit": 7, "offset": 4, "state": "APPROVED"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert api[1].list_call == (7, 4, OperationalMandateState.APPROVED)
    assert api[3].checked_users == [ADMIN_ID]
    body = response.json()
    assert (body["limit"], body["offset"], body["total"]) == (7, 4, 9)
    assert body["items"][0]["mandate"]["mandate_id"] == str(MANDATE_ID)
    assert body["items"][0]["revision"]["specification"]["instruments"][0] == {
        "exchange": "binance",
        "market_type": "spot",
        "base_asset": "BTC",
        "quote_asset": "USDT",
    }
    assert body["items"][0]["mandate"]["approved_revision"] is None
    assert body["items"][0]["mandate"]["approved_by"] is None
    assert body["items"][0]["mandate"]["approved_at"] is None
    assert body["items"][0]["mandate"]["archived_by"] is None
    assert body["items"][0]["mandate"]["archived_at"] is None
    assert "create_idempotency_key" not in response.text
    assert "create_request_fingerprint" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"state": "UNKNOWN"},
    ],
)
async def test_list_rejects_invalid_bounds_before_service(
    client: AsyncClient,
    api: tuple[FastAPI, FakeOperationalMandateService, FakeJWTVerifier, FakeAdminService],
    params: dict[str, str | int],
) -> None:
    response = await client.get(PREFIX, headers=AUTH_HEADERS, params=params)

    assert response.status_code == 422
    assert api[1].list_call is None


@pytest.mark.asyncio
async def test_get_history_and_exact_revision_are_bounded_no_store_reads(
    client: AsyncClient,
    api: tuple[FastAPI, FakeOperationalMandateService, FakeJWTVerifier, FakeAdminService],
) -> None:
    current_response = await client.get(f"{PREFIX}/{MANDATE_ID}", headers=AUTH_HEADERS)
    history_response = await client.get(
        f"{PREFIX}/{MANDATE_ID}/revisions",
        headers=AUTH_HEADERS,
        params={"limit": 6, "offset": 2},
    )
    revision_response = await client.get(
        f"{PREFIX}/{MANDATE_ID}/revisions/1",
        headers=AUTH_HEADERS,
    )

    assert current_response.status_code == 200
    assert current_response.headers["cache-control"] == "no-store"
    assert current_response.json()["revision"]["revision"] == 1
    assert api[1].get_calls == [MANDATE_ID]

    assert history_response.status_code == 200
    assert history_response.headers["cache-control"] == "no-store"
    assert history_response.json()["total"] == 3
    assert api[1].revision_list_call == (MANDATE_ID, 6, 2)

    assert revision_response.status_code == 200
    assert revision_response.headers["cache-control"] == "no-store"
    assert revision_response.json()["revision"] == 1
    assert api[1].get_revision_call == (MANDATE_ID, 1)


@pytest.mark.asyncio
async def test_missing_mandate_uses_existing_safe_not_found_mapping(
    client: AsyncClient,
    api: tuple[FastAPI, FakeOperationalMandateService, FakeJWTVerifier, FakeAdminService],
) -> None:
    api[1].error = OperationalMandateNotFoundError()

    response = await client.get(f"{PREFIX}/{MANDATE_ID}", headers=AUTH_HEADERS)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "operational_mandate_not_found"


@pytest.mark.asyncio
async def test_create_preserves_specification_key_and_authenticated_actor(
    client: AsyncClient,
    api: tuple[FastAPI, FakeOperationalMandateService, FakeJWTVerifier, FakeAdminService],
) -> None:
    response = await client.post(
        PREFIX,
        headers=AUTH_HEADERS,
        json={
            "specification": _specification_payload(),
            "idempotency_key": IDEMPOTENCY_KEY,
        },
    )

    assert response.status_code == 201
    assert api[1].create_call is not None
    specification, actor_id, idempotency_key = api[1].create_call
    assert specification == _specification()
    assert actor_id == ADMIN_ID
    assert actor_id != OTHER_ACTOR_ID
    assert idempotency_key == IDEMPOTENCY_KEY
    assert response.json()["revision"]["specification"]["name"] == "Core spot mandate"


@pytest.mark.asyncio
async def test_request_cannot_spoof_mutation_actor(
    client: AsyncClient,
    api: tuple[FastAPI, FakeOperationalMandateService, FakeJWTVerifier, FakeAdminService],
) -> None:
    response = await client.post(
        PREFIX,
        headers=AUTH_HEADERS,
        json={
            "specification": _specification_payload(),
            "idempotency_key": IDEMPOTENCY_KEY,
            "actor_id": str(OTHER_ACTOR_ID),
        },
    )

    assert response.status_code == 422
    assert api[1].create_call is None


@pytest.mark.asyncio
async def test_replace_preserves_both_tokens_actor_and_semantic_noop_result(
    client: AsyncClient,
    api: tuple[FastAPI, FakeOperationalMandateService, FakeJWTVerifier, FakeAdminService],
) -> None:
    response = await client.patch(
        f"{PREFIX}/{MANDATE_ID}",
        headers=AUTH_HEADERS,
        json={
            "specification": _specification_payload(),
            "expected_revision": 1,
            "expected_record_version": 1,
        },
    )

    assert response.status_code == 200
    assert api[1].replace_call == (MANDATE_ID, _specification(), 1, 1, ADMIN_ID)
    assert response.json()["mandate"]["current_revision"] == 1
    assert response.json()["mandate"]["record_version"] == 1


@pytest.mark.asyncio
async def test_approve_and_archive_preserve_exact_guards_and_actor(
    client: AsyncClient,
    api: tuple[FastAPI, FakeOperationalMandateService, FakeJWTVerifier, FakeAdminService],
) -> None:
    checksum = operational_mandate_specification_checksum(_specification())
    approve_response = await client.post(
        f"{PREFIX}/{MANDATE_ID}/approve",
        headers=AUTH_HEADERS,
        json={
            "expected_revision": 1,
            "expected_checksum": checksum,
            "expected_record_version": 1,
        },
    )
    archive_response = await client.post(
        f"{PREFIX}/{MANDATE_ID}/archive",
        headers=AUTH_HEADERS,
        json={"expected_record_version": 2},
    )

    assert approve_response.status_code == 200
    assert approve_response.json()["state"] == "APPROVED"
    assert api[1].approve_call == (MANDATE_ID, 1, checksum, 1, ADMIN_ID)
    assert archive_response.status_code == 200
    assert archive_response.json()["state"] == "ARCHIVED"
    assert api[1].archive_call == (MANDATE_ID, 2, ADMIN_ID)


@pytest.mark.asyncio
async def test_conflict_and_persistence_failures_use_global_safe_mapping(
    client: AsyncClient,
    api: tuple[FastAPI, FakeOperationalMandateService, FakeJWTVerifier, FakeAdminService],
) -> None:
    api[1].error = OperationalMandateRecordVersionConflictError()
    conflict = await client.post(
        f"{PREFIX}/{MANDATE_ID}/archive",
        headers=AUTH_HEADERS,
        json={"expected_record_version": 1},
    )

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == ("operational_mandate_record_version_conflict")

    api[1].error = PersistenceError()
    persistence = await client.get(f"{PREFIX}/{MANDATE_ID}", headers=AUTH_HEADERS)

    assert persistence.status_code == 500
    assert persistence.json()["error"] == {
        "code": "persistence_error",
        "message": "Não foi possível persistir os dados.",
    }
    assert "traceback" not in persistence.text.lower()


@pytest.mark.asyncio
async def test_unsupported_capability_and_strict_tokens_fail_before_service(
    client: AsyncClient,
    api: tuple[FastAPI, FakeOperationalMandateService, FakeJWTVerifier, FakeAdminService],
) -> None:
    unsupported = await client.post(
        PREFIX,
        headers=AUTH_HEADERS,
        json={
            "specification": _specification_payload(market_type="futures"),
            "idempotency_key": IDEMPOTENCY_KEY,
        },
    )
    coerced_token = await client.post(
        f"{PREFIX}/{MANDATE_ID}/archive",
        headers=AUTH_HEADERS,
        json={"expected_record_version": "1"},
    )
    coerced_schema_versions = [
        await client.post(
            PREFIX,
            headers=AUTH_HEADERS,
            json={
                "specification": {
                    **_specification_payload(),
                    "schema_version": schema_version,
                },
                "idempotency_key": IDEMPOTENCY_KEY,
            },
        )
        for schema_version in (True, 1.0, "1")
    ]

    assert unsupported.status_code == 400
    assert unsupported.json()["error"]["code"] == ("operational_mandate_unsupported_capability")
    assert coerced_token.status_code == 422
    assert [response.status_code for response in coerced_schema_versions] == [422, 422, 422]
    assert api[1].create_call is None
    assert api[1].archive_call is None


def test_router_and_openapi_expose_only_the_selected_protected_contract(
    api: tuple[FastAPI, FakeOperationalMandateService, FakeJWTVerifier, FakeAdminService],
) -> None:
    expected_methods = {
        PREFIX: {"GET", "POST"},
        f"{PREFIX}/{{mandate_id}}": {"GET", "PATCH"},
        f"{PREFIX}/{{mandate_id}}/revisions": {"GET"},
        f"{PREFIX}/{{mandate_id}}/revisions/{{revision}}": {"GET"},
        f"{PREFIX}/{{mandate_id}}/approve": {"POST"},
        f"{PREFIX}/{{mandate_id}}/archive": {"POST"},
    }
    inventory: dict[str, set[str]] = {}
    for route in admin_operational_mandates.router.routes:
        route_path = getattr(route, "path", None)
        route_methods = getattr(route, "methods", None)
        assert isinstance(route_path, str)
        assert route_methods is not None
        inventory.setdefault(route_path, set()).update(str(item) for item in route_methods)
    assert inventory == expected_methods

    schema = api[0].openapi()
    paths = cast(dict[str, dict[str, object]], schema["paths"])
    mandate_paths = {path: item for path, item in paths.items() if path.startswith(PREFIX)}
    assert set(mandate_paths) == set(expected_methods)

    operation_ids: set[str] = set()
    for path, methods in expected_methods.items():
        path_item = mandate_paths[path]
        for method in methods:
            operation = cast(dict[str, object], path_item[method.lower()])
            assert operation.get("security")
            operation_id = operation.get("operationId")
            assert isinstance(operation_id, str)
            operation_ids.add(operation_id)
            responses = cast(dict[str, object], operation["responses"])
            assert {"400", "401", "403", "404", "409", "422", "500", "503"}.issubset(responses)
    assert len(operation_ids) == 8

    collection_get = cast(dict[str, object], mandate_paths[PREFIX]["get"])
    parameters = {
        cast(dict[str, object], item)["name"]: cast(dict[str, object], item)
        for item in cast(list[object], collection_get["parameters"])
    }
    limit_schema = cast(dict[str, object], parameters["limit"]["schema"])
    offset_schema = cast(dict[str, object], parameters["offset"]["schema"])
    assert limit_schema["minimum"] == 1
    assert limit_schema["maximum"] == 100
    assert offset_schema["minimum"] == 0

    components = cast(dict[str, object], schema["components"])
    schemas = cast(dict[str, dict[str, object]], components["schemas"])
    aggregate_properties = cast(
        dict[str, object],
        schemas["OperationalMandateResponse"]["properties"],
    )
    assert "create_idempotency_key" not in aggregate_properties
    assert "create_request_fingerprint" not in aggregate_properties
    instrument_properties = cast(
        dict[str, object],
        schemas["OperationalMandateInstrumentRequest"]["properties"],
    )
    assert set(instrument_properties) == {
        "exchange",
        "market_type",
        "base_asset",
        "quote_asset",
    }
    assert "native_symbol" not in instrument_properties
    assert "active" not in instrument_properties
    assert "precision" not in instrument_properties


def test_dependency_builds_service_over_application_database_and_utc_clock() -> None:
    database = Database("postgresql://adt_test@127.0.0.1:1/adt_test")

    service = get_operational_mandate_service(database)

    assert isinstance(service, OperationalMandateService)
    assert isinstance(service._repository, PostgresOperationalMandateRepository)
    assert service._repository._database is database
    observed_at = service._clock()
    assert observed_at.tzinfo is UTC
