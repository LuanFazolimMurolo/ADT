"""Remote-free API tests for the Phase 1B HTTP boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import AnyHttpUrl, SecretStr

from app.api.dependencies.resources import (
    get_admin_service,
    get_capital_movement_service,
    get_database,
    get_jwt_verifier,
    get_public_simulation_service,
    get_settings_service,
    get_simulation_service,
)
from app.auth import (
    AuthenticationError,
    ExpiredTokenError,
    InvalidTokenError,
    JWKSUnavailableError,
)
from app.core.config import Settings
from app.domain.errors import (
    ActiveSimulationExistsError,
    InsufficientBalanceError,
    SimulationTerminalError,
)
from app.domain.models import (
    AdministrativeMovementType,
    CapitalMovement,
    JsonObject,
    JsonValue,
    LedgerMovementType,
    PublicSimulationSummary,
    SimulationDetails,
    SimulationRun,
    SimulationStatus,
    SystemSetting,
)
from app.main import create_app

ADMIN_ID: Final = UUID("10000000-0000-4000-8000-000000000001")
SIMULATION_ID: Final = UUID("20000000-0000-4000-8000-000000000002")
MOVEMENT_ID: Final = UUID("30000000-0000-4000-8000-000000000003")
NOW: Final = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
AUTH_HEADERS: Final = {"Authorization": "Bearer phase-1b-test-token"}
DATABASE_URL: Final = "postgresql://phase1b_user@db.internal.invalid:5432/adt"
SENSITIVE_DATABASE_MARKER: Final = "database-credential-that-must-not-leak"


@dataclass(frozen=True, slots=True)
class SimulationCreateCall:
    """Arguments received by the simulation service fake."""

    name: str
    initial_capital: Decimal
    currency: str
    created_by: UUID


@dataclass(frozen=True, slots=True)
class MovementCreateCall:
    """Arguments received by the movement service fake."""

    simulation_id: UUID
    movement_type: AdministrativeMovementType
    amount: Decimal
    reason: str
    created_by: UUID
    metadata: JsonObject | None


@dataclass(frozen=True, slots=True)
class SettingUpdateCall:
    """Arguments received by the settings service fake."""

    key: str
    value: JsonValue
    updated_by: UUID


class FakeDatabase:
    """Minimal database health-check boundary."""

    def __init__(self) -> None:
        self.healthy = True
        self.check_count = 0

    async def health_check(self) -> bool:
        """Return the configured health state."""
        self.check_count += 1
        return self.healthy


class FakeJWTVerifier:
    """JWT verifier whose outcome is controlled without network access."""

    def __init__(self) -> None:
        self.user_id = ADMIN_ID
        self.error: AuthenticationError | None = None
        self.tokens: list[str] = []

    async def verify(self, token: str) -> UUID:
        """Record the opaque token and return or raise the configured result."""
        self.tokens.append(token)
        if self.error is not None:
            raise self.error
        return self.user_id


class FakeAdminService:
    """Database-backed administrator decision fake."""

    def __init__(self) -> None:
        self.allowed = True
        self.checked_users: list[UUID] = []

    async def is_admin(self, user_id: UUID) -> bool:
        """Return whether the verified subject is allowed."""
        self.checked_users.append(user_id)
        return self.allowed


class FakePublicSimulationService:
    """Safe public-view service fake."""

    def __init__(self) -> None:
        self.summary: PublicSimulationSummary | None = PublicSimulationSummary(
            simulation_name="Simulação pública",
            currency="BRL",
            initial_capital=Decimal("1000.00000000"),
            current_balance=Decimal("1012.50000000"),
            total_profit_loss=Decimal("12.50000000"),
            started_at=NOW,
            status=SimulationStatus.ACTIVE,
        )

    async def get_active(self) -> PublicSimulationSummary | None:
        """Return the UUID-free projection."""
        return self.summary


def _active_simulation() -> SimulationDetails:
    simulation = SimulationRun(
        id=SIMULATION_ID,
        name="Simulação administrativa",
        status=SimulationStatus.ACTIVE,
        currency="BRL",
        initial_capital=Decimal("1000.00000000"),
        started_at=NOW,
        ended_at=None,
        created_by=ADMIN_ID,
        created_at=NOW,
        updated_at=NOW,
    )
    return SimulationDetails(
        simulation=simulation,
        current_balance=Decimal("1000.00000000"),
        total_profit_loss=Decimal("0.00000000"),
    )


class FakeSimulationService:
    """Simulation application-service fake with lifecycle behavior."""

    def __init__(self) -> None:
        self.details = _active_simulation()
        self.create_calls: list[SimulationCreateCall] = []
        self.create_error: Exception | None = None

    async def list(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[SimulationDetails], int]:
        """Return one simulation for list-route compatibility."""
        del limit, offset
        return [self.details], 1

    async def get(self, simulation_id: UUID) -> SimulationDetails:
        """Return the configured simulation."""
        assert simulation_id == SIMULATION_ID
        return self.details

    async def create(
        self,
        *,
        name: str,
        initial_capital: Decimal,
        currency: str,
        created_by: UUID,
    ) -> SimulationDetails:
        """Record validated inputs and emulate creation."""
        self.create_calls.append(
            SimulationCreateCall(
                name=name,
                initial_capital=initial_capital,
                currency=currency,
                created_by=created_by,
            )
        )
        if self.create_error is not None:
            raise self.create_error

        simulation = replace(
            self.details.simulation,
            name=name,
            initial_capital=initial_capital,
            currency=currency,
            created_by=created_by,
        )
        self.details = SimulationDetails(
            simulation=simulation,
            current_balance=initial_capital,
            total_profit_loss=Decimal("0"),
        )
        return self.details

    async def complete(self, simulation_id: UUID) -> SimulationDetails:
        """Emulate a one-way completion transition."""
        return self._transition(simulation_id, SimulationStatus.COMPLETED)

    async def cancel(self, simulation_id: UUID) -> SimulationDetails:
        """Emulate a one-way cancellation transition."""
        return self._transition(simulation_id, SimulationStatus.CANCELLED)

    def _transition(
        self,
        simulation_id: UUID,
        target_status: SimulationStatus,
    ) -> SimulationDetails:
        assert simulation_id == SIMULATION_ID
        if self.details.simulation.status is not SimulationStatus.ACTIVE:
            raise SimulationTerminalError()
        simulation = replace(
            self.details.simulation,
            status=target_status,
            ended_at=NOW,
            updated_at=NOW,
        )
        self.details = replace(self.details, simulation=simulation)
        return self.details


class FakeCapitalMovementService:
    """Append-only movement service fake."""

    def __init__(self) -> None:
        self.create_calls: list[MovementCreateCall] = []
        self.create_error: Exception | None = None
        self.movements: list[CapitalMovement] = []

    async def list(
        self,
        simulation_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[CapitalMovement], int]:
        """Return recorded movements for route compatibility."""
        assert simulation_id == SIMULATION_ID
        return self.movements[offset : offset + limit], len(self.movements)

    async def create(
        self,
        *,
        simulation_id: UUID,
        movement_type: AdministrativeMovementType,
        amount: Decimal,
        reason: str,
        created_by: UUID,
        metadata: JsonObject | None = None,
    ) -> CapitalMovement:
        """Record an append request and map its public type to the ledger."""
        self.create_calls.append(
            MovementCreateCall(
                simulation_id=simulation_id,
                movement_type=movement_type,
                amount=amount,
                reason=reason,
                created_by=created_by,
                metadata=metadata,
            )
        )
        if self.create_error is not None:
            raise self.create_error

        ledger_type = {
            AdministrativeMovementType.DEPOSIT: LedgerMovementType.ADMIN_DEPOSIT,
            AdministrativeMovementType.WITHDRAWAL: LedgerMovementType.ADMIN_WITHDRAWAL,
            AdministrativeMovementType.ADJUSTMENT: LedgerMovementType.ADJUSTMENT,
        }[movement_type]
        movement = CapitalMovement(
            id=MOVEMENT_ID,
            simulation_id=simulation_id,
            type=ledger_type,
            amount=amount,
            reason=reason,
            reference_id=None,
            created_by=created_by,
            created_at=NOW,
            metadata=metadata,
        )
        self.movements.append(movement)
        return movement


class FakeSettingsService:
    """Settings service fake that permits only value replacement."""

    def __init__(self) -> None:
        self.setting = SystemSetting(
            key="paper_trading_enabled",
            value=True,
            description="Paper trading switch.",
            is_public=True,
            updated_by=None,
            created_at=NOW,
            updated_at=NOW,
        )
        self.update_calls: list[SettingUpdateCall] = []

    async def list(self) -> list[SystemSetting]:
        """Return the non-secret fake setting."""
        return [self.setting]

    async def update_value(
        self,
        key: str,
        *,
        value: JsonValue,
        updated_by: UUID,
    ) -> SystemSetting:
        """Record only the mutable value and acting administrator."""
        self.update_calls.append(SettingUpdateCall(key=key, value=value, updated_by=updated_by))
        self.setting = replace(
            self.setting,
            value=value,
            updated_by=updated_by,
            updated_at=NOW,
        )
        return self.setting


@dataclass(slots=True)
class ApiHarness:
    """All typed fakes attached to one application instance."""

    app: FastAPI
    client: AsyncClient
    database: FakeDatabase
    verifier: FakeJWTVerifier
    admin_service: FakeAdminService
    public_service: FakePublicSimulationService
    simulation_service: FakeSimulationService
    movement_service: FakeCapitalMovementService
    settings_service: FakeSettingsService


def _test_settings() -> Settings:
    return Settings(
        supabase_url=AnyHttpUrl("https://phase1b.example.invalid"),
        supabase_publishable_key=SecretStr("public-test-key"),
        supabase_database_url=SecretStr(DATABASE_URL),
        environment="test",
        log_level="WARNING",
        cors_origins=["http://localhost:5173"],
        api_host="127.0.0.1",
        api_port=8000,
    )


@pytest_asyncio.fixture
async def api_harness() -> AsyncIterator[ApiHarness]:
    """Create the real route graph with remote-free dependency overrides."""
    application = create_app(_test_settings())
    database = FakeDatabase()
    verifier = FakeJWTVerifier()
    admin_service = FakeAdminService()
    public_service = FakePublicSimulationService()
    simulation_service = FakeSimulationService()
    movement_service = FakeCapitalMovementService()
    settings_service = FakeSettingsService()

    async def override_database() -> FakeDatabase:
        return database

    async def override_jwt_verifier() -> FakeJWTVerifier:
        return verifier

    async def override_admin_service() -> FakeAdminService:
        return admin_service

    async def override_public_service() -> FakePublicSimulationService:
        return public_service

    async def override_simulation_service() -> FakeSimulationService:
        return simulation_service

    async def override_movement_service() -> FakeCapitalMovementService:
        return movement_service

    async def override_settings_service() -> FakeSettingsService:
        return settings_service

    application.dependency_overrides[get_database] = override_database
    application.dependency_overrides[get_jwt_verifier] = override_jwt_verifier
    application.dependency_overrides[get_admin_service] = override_admin_service
    application.dependency_overrides[get_public_simulation_service] = override_public_service
    application.dependency_overrides[get_simulation_service] = override_simulation_service
    application.dependency_overrides[get_capital_movement_service] = override_movement_service
    application.dependency_overrides[get_settings_service] = override_settings_service

    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport,
        base_url="http://phase1b.test",
    ) as client:
        yield ApiHarness(
            app=application,
            client=client,
            database=database,
            verifier=verifier,
            admin_service=admin_service,
            public_service=public_service,
            simulation_service=simulation_service,
            movement_service=movement_service,
            settings_service=settings_service,
        )
    application.dependency_overrides.clear()


async def test_health_reports_only_service_state(api_harness: ApiHarness) -> None:
    response = await api_harness.client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


async def test_database_health_reports_healthy_without_connection_details(
    api_harness: ApiHarness,
) -> None:
    response = await api_harness.client.get("/health/database")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
    assert api_harness.database.check_count == 1
    assert "postgresql://" not in response.text
    assert "phase1b_user" not in response.text
    assert "db.internal.invalid" not in response.text


async def test_database_health_failure_is_safe_503(api_harness: ApiHarness) -> None:
    api_harness.database.healthy = False

    response = await api_harness.client.get("/health/database")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "database_unavailable",
            "message": "O banco de dados está temporariamente indisponível.",
        }
    }
    for sensitive_value in (
        DATABASE_URL,
        "phase1b_user",
        "db.internal.invalid",
        "postgresql://",
    ):
        assert sensitive_value not in response.text


async def test_public_simulation_needs_no_auth_and_omits_identifiers(
    api_harness: ApiHarness,
) -> None:
    response = await api_harness.client.get("/api/v1/public/simulation")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "name": "Simulação pública",
        "currency": "BRL",
        "initial_capital": "1000.00000000",
        "current_balance": "1012.50000000",
        "total_profit_loss": "12.50000000",
        "started_at": "2026-07-29T12:00:00Z",
        "status": "ACTIVE",
    }
    assert str(SIMULATION_ID) not in response.text
    assert all(
        forbidden_field not in payload
        for forbidden_field in ("id", "simulation_id", "user_id", "email", "token")
    )
    assert api_harness.verifier.tokens == []
    assert api_harness.admin_service.checked_users == []


async def test_missing_bearer_token_returns_401(api_harness: ApiHarness) -> None:
    response = await api_harness.client.get("/api/v1/admin/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {
        "error": {
            "code": "authentication_required",
            "message": "Valid authentication is required.",
        }
    }
    assert api_harness.verifier.tokens == []


@pytest.mark.parametrize(
    ("authentication_error", "expected_code"),
    [
        (InvalidTokenError(), "invalid_token"),
        (ExpiredTokenError(), "token_expired"),
    ],
)
async def test_invalid_or_expired_bearer_token_returns_safe_401(
    api_harness: ApiHarness,
    authentication_error: AuthenticationError,
    expected_code: str,
) -> None:
    api_harness.verifier.error = authentication_error
    opaque_token = "jwt-value-that-must-not-be-returned"

    response = await api_harness.client.get(
        "/api/v1/admin/me",
        headers={"Authorization": f"Bearer {opaque_token}"},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == expected_code
    assert opaque_token not in response.text
    assert api_harness.admin_service.checked_users == []


async def test_jwks_unavailability_returns_safe_503(api_harness: ApiHarness) -> None:
    api_harness.verifier.error = JWKSUnavailableError()

    response = await api_harness.client.get("/api/v1/admin/me", headers=AUTH_HEADERS)

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "authentication_keys_unavailable",
            "message": "Authentication service is temporarily unavailable.",
        }
    }
    assert "phase-1b-test-token" not in response.text
    assert "traceback" not in response.text.lower()


async def test_authenticated_non_admin_returns_403(api_harness: ApiHarness) -> None:
    api_harness.admin_service.allowed = False

    response = await api_harness.client.get("/api/v1/admin/me", headers=AUTH_HEADERS)

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "forbidden",
            "message": "You are not allowed to perform this action.",
        }
    }
    assert api_harness.admin_service.checked_users == [ADMIN_ID]


async def test_administrator_can_read_own_uuid_and_status(api_harness: ApiHarness) -> None:
    response = await api_harness.client.get("/api/v1/admin/me", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"user_id": str(ADMIN_ID), "is_admin": True}
    assert api_harness.verifier.tokens == ["phase-1b-test-token"]
    assert api_harness.admin_service.checked_users == [ADMIN_ID]


async def test_administrator_can_create_simulation_with_decimal_capital(
    api_harness: ApiHarness,
) -> None:
    response = await api_harness.client.post(
        "/api/v1/admin/simulations",
        headers=AUTH_HEADERS,
        json={
            "name": "  Simulação nova  ",
            "initial_capital": "2500.12500000",
            "currency": "brl",
        },
    )

    assert response.status_code == 201
    assert response.json()["initial_capital"] == "2500.12500000"
    assert response.json()["current_balance"] == "2500.12500000"
    assert api_harness.simulation_service.create_calls == [
        SimulationCreateCall(
            name="Simulação nova",
            initial_capital=Decimal("2500.12500000"),
            currency="brl",
            created_by=ADMIN_ID,
        )
    ]


async def test_second_active_simulation_conflict_has_stable_error(
    api_harness: ApiHarness,
) -> None:
    api_harness.simulation_service.create_error = ActiveSimulationExistsError()

    response = await api_harness.client.post(
        "/api/v1/admin/simulations",
        headers=AUTH_HEADERS,
        json={
            "name": "Segunda ativa",
            "initial_capital": "1000",
            "currency": "BRL",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "active_simulation_exists",
            "message": "Já existe uma simulação ativa.",
        }
    }


@pytest.mark.parametrize(
    "invalid_capital",
    ["0", "-0.00000001", "NaN", "Infinity", "-Infinity"],
)
async def test_invalid_initial_capital_payload_is_rejected_with_422(
    api_harness: ApiHarness,
    invalid_capital: str,
) -> None:
    response = await api_harness.client.post(
        "/api/v1/admin/simulations",
        headers=AUTH_HEADERS,
        json={
            "name": "Capital inválido",
            "initial_capital": invalid_capital,
            "currency": "BRL",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert api_harness.simulation_service.create_calls == []
    assert "traceback" not in response.text.lower()


@pytest.mark.parametrize(
    ("movement_type", "amount", "stored_type"),
    [
        ("DEPOSIT", "25.00000000", "ADMIN_DEPOSIT"),
        ("WITHDRAWAL", "-5.50000000", "ADMIN_WITHDRAWAL"),
        ("ADJUSTMENT", "-2.25000000", "ADJUSTMENT"),
    ],
)
async def test_allowed_administrative_movements_are_appended(
    api_harness: ApiHarness,
    movement_type: str,
    amount: str,
    stored_type: str,
) -> None:
    response = await api_harness.client.post(
        f"/api/v1/admin/simulations/{SIMULATION_ID}/movements",
        headers=AUTH_HEADERS,
        json={
            "type": movement_type,
            "amount": amount,
            "reason": "  ajuste administrativo  ",
            "metadata": {"ticket": "ADT-1"},
        },
    )

    assert response.status_code == 201
    assert response.json()["type"] == stored_type
    assert response.json()["amount"] == amount
    call = api_harness.movement_service.create_calls[-1]
    assert call.movement_type is AdministrativeMovementType(movement_type)
    assert call.amount == Decimal(amount)
    assert call.reason == "ajuste administrativo"
    assert call.created_by == ADMIN_ID


async def test_initial_capital_cannot_be_submitted_through_api(
    api_harness: ApiHarness,
) -> None:
    response = await api_harness.client.post(
        f"/api/v1/admin/simulations/{SIMULATION_ID}/movements",
        headers=AUTH_HEADERS,
        json={
            "type": "INITIAL_CAPITAL",
            "amount": "1000",
            "reason": "tentativa indevida",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert api_harness.movement_service.create_calls == []


async def test_insufficient_balance_error_is_a_safe_conflict(
    api_harness: ApiHarness,
) -> None:
    api_harness.movement_service.create_error = InsufficientBalanceError()

    response = await api_harness.client.post(
        f"/api/v1/admin/simulations/{SIMULATION_ID}/movements",
        headers=AUTH_HEADERS,
        json={
            "type": "WITHDRAWAL",
            "amount": "-5000",
            "reason": "retirada excessiva",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "insufficient_balance",
            "message": "O saldo da simulação é insuficiente para este movimento.",
        }
    }


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
async def test_ledger_has_no_update_or_delete_api(
    api_harness: ApiHarness,
    method: str,
) -> None:
    response = await api_harness.client.request(
        method,
        f"/api/v1/admin/simulations/{SIMULATION_ID}/movements",
        headers=AUTH_HEADERS,
        json={"amount": "1"},
    )

    assert response.status_code == 405
    assert api_harness.movement_service.create_calls == []


@pytest.mark.parametrize(
    ("action", "expected_status"),
    [("complete", "COMPLETED"), ("cancel", "CANCELLED")],
)
async def test_simulation_terminal_transitions_are_one_way(
    api_harness: ApiHarness,
    action: str,
    expected_status: str,
) -> None:
    endpoint = f"/api/v1/admin/simulations/{SIMULATION_ID}/{action}"

    response = await api_harness.client.post(endpoint, headers=AUTH_HEADERS)
    repeated_response = await api_harness.client.post(endpoint, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["status"] == expected_status
    assert response.json()["ended_at"] == "2026-07-29T12:00:00Z"
    assert repeated_response.status_code == 409
    assert repeated_response.json()["error"]["code"] == "simulation_terminal"


async def test_setting_patch_changes_only_value_and_records_admin(
    api_harness: ApiHarness,
) -> None:
    response = await api_harness.client.patch(
        "/api/v1/admin/settings/paper_trading_enabled",
        headers=AUTH_HEADERS,
        json={"value": {"enabled": False}},
    )

    assert response.status_code == 200
    assert response.json()["key"] == "paper_trading_enabled"
    assert response.json()["value"] == {"enabled": False}
    assert response.json()["updated_by"] == str(ADMIN_ID)
    assert api_harness.settings_service.update_calls == [
        SettingUpdateCall(
            key="paper_trading_enabled",
            value={"enabled": False},
            updated_by=ADMIN_ID,
        )
    ]


async def test_setting_patch_rejects_attempt_to_change_key(
    api_harness: ApiHarness,
) -> None:
    response = await api_harness.client.patch(
        "/api/v1/admin/settings/paper_trading_enabled",
        headers=AUTH_HEADERS,
        json={"key": "replacement", "value": False},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert api_harness.settings_service.update_calls == []


async def test_unexpected_api_failure_hides_secrets_and_traceback(
    api_harness: ApiHarness,
) -> None:
    api_harness.simulation_service.create_error = RuntimeError(
        f"{SENSITIVE_DATABASE_MARKER}: driver failed while opening {DATABASE_URL}"
    )

    response = await api_harness.client.post(
        "/api/v1/admin/simulations",
        headers=AUTH_HEADERS,
        json={
            "name": "Erro seguro",
            "initial_capital": "1000",
            "currency": "BRL",
        },
    )

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "An internal server error occurred.",
        }
    }
    assert DATABASE_URL not in response.text
    assert SENSITIVE_DATABASE_MARKER not in response.text
    assert "traceback" not in response.text.lower()
