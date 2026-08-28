"""Application-service tests for operational paper-capital authorizations."""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest

from app import services as services_package
from app.domain.errors import PersistenceError, PersistenceUnavailableError
from app.operational_paper_capital_authorizations import (
    OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_SCHEMA_VERSION,
    OperationalPaperCapitalAuthorization,
    OperationalPaperCapitalAuthorizationCreateIntent,
    OperationalPaperCapitalAuthorizationIdempotencyConflictError,
    OperationalPaperCapitalAuthorizationInsufficientAvailableCapitalError,
    OperationalPaperCapitalAuthorizationNotFoundError,
    OperationalPaperCapitalAuthorizationProfileBinding,
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
from app.services import (
    OperationalPaperCapitalAuthorizationService as ExportedAuthorizationService,
)
from app.services import operational_paper_capital_authorizations as service_module
from app.services.operational_paper_capital_authorizations import (
    OperationalPaperCapitalAuthorizationPage,
    OperationalPaperCapitalAuthorizationService,
)

AUTHORIZATION_ID = UUID("10000000-0000-4000-8000-000000000001")
PROFILE_ID = UUID("20000000-0000-4000-8000-000000000002")
SIMULATION_ID = UUID("30000000-0000-4000-8000-000000000003")
ACTOR_ID = UUID("40000000-0000-4000-8000-000000000004")
NOW = datetime(2026, 8, 28, 18, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=1)
IDEMPOTENCY_KEY = "gate-2e1-create"

PROFILE_BINDING = OperationalPaperCapitalAuthorizationProfileBinding(
    profile_id=PROFILE_ID,
    approved_revision=3,
    specification_checksum="a" * 64,
)
INTENT = OperationalPaperCapitalAuthorizationCreateIntent(
    profile_binding=PROFILE_BINDING,
    simulation_id=SIMULATION_ID,
    quote_asset="USDT",
    authorized_capital=Decimal("100.00000000"),
)
SPECIFICATION = build_operational_paper_capital_authorization_specification(INTENT)
AUTHORIZATION = OperationalPaperCapitalAuthorization(
    authorization_id=AUTHORIZATION_ID,
    schema_version=OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_SCHEMA_VERSION,
    state=OperationalPaperCapitalAuthorizationState.AUTHORIZED,
    record_version=1,
    profile_binding=PROFILE_BINDING,
    simulation_id=SIMULATION_ID,
    quote_asset="USDT",
    authorized_capital=Decimal("100.00000000"),
    authorization_checksum=operational_paper_capital_authorization_specification_checksum(
        SPECIFICATION
    ),
    created_by=ACTOR_ID,
    created_at=NOW,
    revoked_by=None,
    revoked_at=None,
    create_idempotency_key=IDEMPOTENCY_KEY,
    create_intent_fingerprint=operational_paper_capital_authorization_create_intent_fingerprint(
        INTENT
    ),
)
PAGE: OperationalPaperCapitalAuthorizationPage = ([AUTHORIZATION], 1)
EXISTING_SERVICE_EXPORTS = {
    "AdminService",
    "CapitalMovementService",
    "MarketOperationService",
    "OperationalMandateService",
    "OperationalPaperSessionProfileService",
    "PublicSimulationService",
    "SettingsService",
    "SimulationService",
    "WorkerRuntimeObservabilityService",
}


class RecordingClock:
    def __init__(self, value: datetime = LATER) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return self.value


class RecordingRepository(PostgresOperationalPaperCapitalAuthorizationRepository):
    def __init__(self) -> None:
        self.page_result = PAGE
        self.get_result: OperationalPaperCapitalAuthorization | None = AUTHORIZATION
        self.create_result = AUTHORIZATION
        self.revoke_result = AUTHORIZATION
        self.failures: dict[str, Exception] = {}
        self.method_calls: list[str] = []
        self.list_calls: list[
            tuple[int, int, OperationalPaperCapitalAuthorizationState | None]
        ] = []
        self.get_calls: list[UUID] = []
        self.create_calls: list[
            tuple[OperationalPaperCapitalAuthorizationCreateIntent, UUID, str, datetime]
        ] = []
        self.revoke_calls: list[tuple[UUID, int, UUID, datetime]] = []

    def _raise_failure(self, method: str) -> None:
        failure = self.failures.get(method)
        if failure is not None:
            raise failure

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: OperationalPaperCapitalAuthorizationState | None = None,
    ) -> OperationalPaperCapitalAuthorizationPage:
        self.method_calls.append("list")
        self.list_calls.append((limit, offset, state))
        self._raise_failure("list")
        return self.page_result

    async def get(
        self,
        authorization_id: UUID,
    ) -> OperationalPaperCapitalAuthorization | None:
        self.method_calls.append("get")
        self.get_calls.append(authorization_id)
        self._raise_failure("get")
        return self.get_result

    async def create(
        self,
        intent: OperationalPaperCapitalAuthorizationCreateIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        now: datetime,
    ) -> OperationalPaperCapitalAuthorization:
        self.method_calls.append("create")
        self.create_calls.append((intent, actor_id, idempotency_key, now))
        self._raise_failure("create")
        return self.create_result

    async def revoke(
        self,
        authorization_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
    ) -> OperationalPaperCapitalAuthorization:
        self.method_calls.append("revoke")
        self.revoke_calls.append((authorization_id, expected_record_version, actor_id, now))
        self._raise_failure("revoke")
        return self.revoke_result


def _service() -> tuple[
    OperationalPaperCapitalAuthorizationService,
    RecordingRepository,
    RecordingClock,
]:
    repository = RecordingRepository()
    clock = RecordingClock()
    return (
        OperationalPaperCapitalAuthorizationService(
            repository=repository,
            clock=clock,
        ),
        repository,
        clock,
    )


def test_package_exports_the_service_class() -> None:
    assert ExportedAuthorizationService is OperationalPaperCapitalAuthorizationService
    assert services_package.__all__.count("OperationalPaperCapitalAuthorizationService") == 1
    assert len(services_package.__all__) == len(set(services_package.__all__))
    assert EXISTING_SERVICE_EXPORTS <= set(services_package.__all__)


def test_constructor_retains_injected_dependencies() -> None:
    service, repository, clock = _service()

    assert service._repository is repository
    assert service._clock is clock


@pytest.mark.parametrize(
    ("limit", "offset", "state"),
    [
        (17, 4, None),
        (17, 4, OperationalPaperCapitalAuthorizationState.AUTHORIZED),
        (
            cast(int, True),
            cast(int, "raw-offset"),
            cast(OperationalPaperCapitalAuthorizationState, "AUTHORIZED"),
        ),
    ],
)
async def test_list_forwards_exact_values_without_clock_or_coercion(
    limit: int,
    offset: int,
    state: OperationalPaperCapitalAuthorizationState | None,
) -> None:
    service, repository, clock = _service()

    result = await service.list(limit=limit, offset=offset, state=state)

    assert result is PAGE
    assert repository.list_calls == [(limit, offset, state)]
    assert repository.method_calls == ["list"]
    assert clock.calls == 0


async def test_get_forwards_identifier_and_preserves_result_identity() -> None:
    service, repository, clock = _service()

    result = await service.get(AUTHORIZATION_ID)

    assert result is AUTHORIZATION
    assert repository.get_calls == [AUTHORIZATION_ID]
    assert repository.method_calls == ["get"]
    assert clock.calls == 0


async def test_get_none_raises_stable_not_found_without_clock() -> None:
    service, repository, clock = _service()
    repository.get_result = None

    with pytest.raises(OperationalPaperCapitalAuthorizationNotFoundError) as generated:
        await service.get(AUTHORIZATION_ID)

    existing = OperationalPaperCapitalAuthorizationNotFoundError()
    repository.failures["get"] = existing
    with pytest.raises(OperationalPaperCapitalAuthorizationNotFoundError) as propagated:
        await service.get(AUTHORIZATION_ID)

    assert generated.value is not existing
    assert propagated.value is existing
    assert repository.get_calls == [AUTHORIZATION_ID, AUTHORIZATION_ID]
    assert repository.method_calls == ["get", "get"]
    assert clock.calls == 0


async def test_create_forwards_exact_contract_once() -> None:
    service, repository, clock = _service()

    result = await service.create(
        INTENT,
        actor_id=ACTOR_ID,
        idempotency_key=IDEMPOTENCY_KEY,
    )

    assert result is AUTHORIZATION
    assert len(repository.create_calls) == 1
    intent, actor_id, idempotency_key, now = repository.create_calls[0]
    assert intent is INTENT
    assert actor_id == ACTOR_ID
    assert idempotency_key == IDEMPOTENCY_KEY
    assert now is LATER
    assert repository.list_calls == repository.get_calls == repository.revoke_calls == []
    assert repository.method_calls == ["create"]
    assert clock.calls == 1


async def test_revoke_forwards_exact_contract_once() -> None:
    service, repository, clock = _service()

    result = await service.revoke(
        AUTHORIZATION_ID,
        expected_record_version=7,
        actor_id=ACTOR_ID,
    )

    assert result is AUTHORIZATION
    assert repository.revoke_calls == [(AUTHORIZATION_ID, 7, ACTOR_ID, LATER)]
    assert repository.list_calls == repository.get_calls == repository.create_calls == []
    assert repository.method_calls == ["revoke"]
    assert clock.calls == 1


@pytest.mark.parametrize("method", ["create", "revoke"])
async def test_mutation_failure_uses_one_exact_timestamp_without_fallbacks(
    method: str,
) -> None:
    service, repository, clock = _service()
    original_exception = PersistenceError()
    repository.failures[method] = original_exception

    with pytest.raises(PersistenceError) as caught:
        if method == "create":
            await service.create(
                INTENT,
                actor_id=ACTOR_ID,
                idempotency_key=IDEMPOTENCY_KEY,
            )
            assert repository.create_calls == [(INTENT, ACTOR_ID, IDEMPOTENCY_KEY, LATER)]
        else:
            await service.revoke(
                AUTHORIZATION_ID,
                expected_record_version=7,
                actor_id=ACTOR_ID,
            )
            assert repository.revoke_calls == [(AUTHORIZATION_ID, 7, ACTOR_ID, LATER)]

    assert caught.value is original_exception
    assert repository.method_calls == [method]
    assert clock.calls == 1


async def test_mutations_forward_invalid_looking_values_without_validation() -> None:
    service, repository, clock = _service()
    raw_idempotency_key = object()
    raw_record_version = cast(int, True)

    await service.create(
        INTENT,
        actor_id=ACTOR_ID,
        idempotency_key=cast(str, raw_idempotency_key),
    )
    await service.revoke(
        AUTHORIZATION_ID,
        expected_record_version=raw_record_version,
        actor_id=ACTOR_ID,
    )

    assert repository.create_calls[0][2] is raw_idempotency_key
    assert repository.revoke_calls[0][1] is raw_record_version
    assert repository.method_calls == ["create", "revoke"]
    assert clock.calls == 2


@pytest.mark.parametrize(
    ("method", "original_exception", "expected_clock_calls"),
    [
        ("create", OperationalPaperCapitalAuthorizationIdempotencyConflictError(), 1),
        (
            "create",
            OperationalPaperCapitalAuthorizationInsufficientAvailableCapitalError(),
            1,
        ),
        ("create", OperationalPaperCapitalReservationConflictError(), 1),
        ("revoke", OperationalPaperCapitalAuthorizationStateTransitionConflictError(), 1),
        ("revoke", OperationalPaperCapitalAuthorizationRecordVersionConflictError(), 1),
        ("list", PersistenceError(), 0),
        ("revoke", PersistenceUnavailableError(), 1),
        ("get", OperationalPaperCapitalAuthorizationNotFoundError(), 0),
    ],
)
async def test_repository_errors_propagate_with_identity(
    method: str,
    original_exception: Exception,
    expected_clock_calls: int,
) -> None:
    service, repository, clock = _service()
    repository.failures[method] = original_exception

    with pytest.raises(type(original_exception)) as caught:
        if method == "create":
            await service.create(
                INTENT,
                actor_id=ACTOR_ID,
                idempotency_key=IDEMPOTENCY_KEY,
            )
        elif method == "list":
            await service.list(limit=17, offset=4)
        elif method == "revoke":
            await service.revoke(
                AUTHORIZATION_ID,
                expected_record_version=7,
                actor_id=ACTOR_ID,
            )
        else:
            await service.get(AUTHORIZATION_ID)

    assert caught.value is original_exception
    assert repository.method_calls == [method]
    assert clock.calls == expected_clock_calls


def test_service_module_has_no_database_sql_or_transport_imports() -> None:
    tree = ast.parse(inspect.getsource(service_module))
    imported_modules = {
        module
        for node in ast.walk(tree)
        for module in ([node.module] if isinstance(node, ast.ImportFrom) else [])
        if module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    imported_symbols = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    forbidden_prefixes = ("app.api", "app.database", "psycopg", "fastapi", "supabase")
    assert not any(module.startswith(forbidden_prefixes) for module in imported_modules)
    assert imported_symbols.isdisjoint({"Database", "DatabaseConnection", "HTTPException", "SQL"})
    assert "Database" not in vars(service_module)


def test_public_async_use_case_surface_is_exact() -> None:
    public_functions = {
        name
        for name, function in inspect.getmembers(
            OperationalPaperCapitalAuthorizationService,
            inspect.isfunction,
        )
        if not name.startswith("_") and inspect.iscoroutinefunction(function)
    }

    assert public_functions == {"list", "get", "create", "revoke"}
