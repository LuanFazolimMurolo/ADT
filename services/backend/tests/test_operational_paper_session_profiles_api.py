"""Remote-free contracts for the operational paper-session profile admin API."""

from __future__ import annotations

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
    get_operational_paper_session_profile_service,
)
from app.api.routes import admin_operational_paper_session_profiles
from app.backtesting.domain import (
    FeeModel,
    InstrumentConstraints,
    IntrabarPolicy,
    PositionSizedExecutionAssumptions,
    PositionSizingKind,
    PositionSizingPolicy,
    SlippageModel,
    StopLossKind,
    StopLossPolicy,
    StopLossRiskLimits,
)
from app.database import Database
from app.domain.errors import DomainError
from app.indicators.regime import MarketRegimePolicy
from app.main import create_app
from app.market_data.domain import Exchange, MarketType, TradingPair
from app.market_data.timeframes import TIMEFRAMES
from app.operational_mandates import OperationalMandateInstrument
from app.operational_mandates.errors import (
    OperationalMandateNotFoundError,
    OperationalMandateStateTransitionConflictError,
)
from app.operational_paper_session_profiles import (
    MAX_OPERATIONAL_PAPER_SESSION_PROFILE_DESCRIPTION_LENGTH,
    MAX_OPERATIONAL_PAPER_SESSION_PROFILE_NAME_LENGTH,
    OPERATIONAL_PAPER_SESSION_PROFILE_SPEC_SCHEMA_VERSION,
    OperationalPaperSessionProfile,
    OperationalPaperSessionProfileCreateIntent,
    OperationalPaperSessionProfileMandateBinding,
    OperationalPaperSessionProfileRevision,
    OperationalPaperSessionProfileSpecification,
    OperationalPaperSessionProfileState,
    OperationalPaperSessionProfileStrategySnapshot,
    build_operational_paper_session_profile_strategy_snapshot,
    operational_paper_session_profile_specification_checksum,
)
from app.operational_paper_session_profiles.errors import (
    OperationalPaperSessionProfileChecksumMismatchError,
    OperationalPaperSessionProfileNotFoundError,
    OperationalPaperSessionProfileRecordVersionConflictError,
    OperationalPaperSessionProfileRevisionConflictError,
    OperationalPaperSessionProfileStateTransitionConflictError,
)
from app.repositories.operational_paper_session_profiles import (
    PostgresOperationalPaperSessionProfileRepository,
)
from app.services import OperationalPaperSessionProfileService
from app.strategies.errors import (
    StrategyDefinitionArchivedError,
    StrategyDefinitionCompatibilityError,
    StrategyDefinitionNotFoundError,
    StrategyDefinitionRevisionConflictError,
)
from app.strategies.registry import StrategyPluginRegistry

PREFIX: Final = "/api/v1/admin/operational-paper-session-profiles"
ADMIN_ID: Final = UUID("10000000-0000-4000-8000-000000000001")
OTHER_ACTOR_ID: Final = UUID("10000000-0000-4000-8000-000000000002")
PROFILE_ID: Final = UUID("20000000-0000-4000-8000-000000000001")
OTHER_PROFILE_ID: Final = UUID("20000000-0000-4000-8000-000000000002")
MANDATE_ID: Final = UUID("30000000-0000-4000-8000-000000000001")
STRATEGY_ID: Final = UUID("40000000-0000-4000-8000-000000000001")
NOW: Final = datetime(2026, 8, 24, 18, 0, tzinfo=UTC)
AUTH_HEADERS: Final = {"Authorization": "Bearer phase-7-07-gate-3-token"}
IDEMPOTENCY_KEY: Final = "gate-3-profile-create-1"
MANDATE_CHECKSUM: Final = "a" * 64
STRATEGY_PARAMETERS_CHECKSUM: Final = "b" * 64
APPROVAL_CHECKSUM: Final = "c" * 64

Current = tuple[OperationalPaperSessionProfile, OperationalPaperSessionProfileRevision]
CurrentPage = tuple[list[Current], int]
RevisionPage = tuple[list[OperationalPaperSessionProfileRevision], int]


def _binding() -> OperationalPaperSessionProfileMandateBinding:
    return OperationalPaperSessionProfileMandateBinding(
        mandate_id=MANDATE_ID,
        approved_revision=11,
        specification_checksum=MANDATE_CHECKSUM,
    )


def _instrument() -> OperationalMandateInstrument:
    return OperationalMandateInstrument(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        pair=TradingPair("BTC", "USDT"),
    )


def _execution() -> PositionSizedExecutionAssumptions:
    return PositionSizedExecutionAssumptions(
        fees=FeeModel(Decimal("1.25"), Decimal("2.50")),
        slippage=SlippageModel(fixed_bps=Decimal("3.75")),
        intrabar_policy=IntrabarPolicy.CONSERVATIVE,
        force_close_at_end=False,
        position_sizing=PositionSizingPolicy(
            kind=PositionSizingKind.EQUITY_PERCENT,
            value=Decimal("25.125"),
            minimum_quote_reserve=Decimal("10.50"),
        ),
    )


def _constraints() -> InstrumentConstraints:
    return InstrumentConstraints(
        minimum_quantity=Decimal("0.00000001"),
        quantity_step=Decimal("0.00000001"),
        price_tick=Decimal("0.01"),
        minimum_notional=Decimal("10.25"),
        maximum_notional=Decimal("10000.75"),
    )


def _risk() -> StopLossRiskLimits:
    return StopLossRiskLimits(
        max_order_notional=Decimal("500.50"),
        max_position_notional=Decimal("1000.75"),
        max_open_orders=4,
        max_total_orders=50,
        max_drawdown_pct=Decimal("20.25"),
        stop_on_max_drawdown=True,
        allow_all_in=False,
        minimum_quote_reserve=Decimal("20.50"),
        stop_loss=StopLossPolicy(
            kind=StopLossKind.FIXED_PERCENT,
            value=Decimal("5.125"),
        ),
    )


def _regime() -> MarketRegimePolicy:
    return MarketRegimePolicy(
        fast_ema_period=8,
        slow_ema_period=21,
        atr_period=13,
        volatile_atr_ratio=Decimal("0.03125"),
        trend_strength_threshold=Decimal("1.125"),
        schema_version=1,
    )


def _intent() -> OperationalPaperSessionProfileCreateIntent:
    return OperationalPaperSessionProfileCreateIntent(
        name="Primary paper profile",
        description="Deterministic non-capital policy.",
        mandate_binding=_binding(),
        selected_instrument=_instrument(),
        timeframe=TIMEFRAMES["1h"],
        start_at=NOW,
        warmup_candles=20,
        strategy_definition_id=STRATEGY_ID,
        expected_strategy_definition_revision=17,
        expected_strategy_parameters_checksum=STRATEGY_PARAMETERS_CHECKSUM,
        execution=_execution(),
        instrument_constraints=_constraints(),
        risk_limits=_risk(),
        history_window=512,
        max_candles=10_000,
        max_orders=1_000,
        max_events=10_000,
        engine_version="paper-engine-v1",
        market_regime_policy=_regime(),
    )


def _snapshot() -> OperationalPaperSessionProfileStrategySnapshot:
    return build_operational_paper_session_profile_strategy_snapshot(
        strategy_definition_id=STRATEGY_ID,
        source_revision=17,
        plugin_name="gate-3-strategy",
        plugin_version="2.0.0",
        plugin_schema_version=3,
        strategy_lifecycle_version=2,
        parameters=(
            ("enabled", True),
            ("label", "paper"),
            ("period", 13),
            ("threshold", Decimal("0.123456789")),
        ),
        parameters_checksum=STRATEGY_PARAMETERS_CHECKSUM,
    )


def _specification() -> OperationalPaperSessionProfileSpecification:
    intent = _intent()
    return OperationalPaperSessionProfileSpecification(
        schema_version=OPERATIONAL_PAPER_SESSION_PROFILE_SPEC_SCHEMA_VERSION,
        name=intent.name,
        description=intent.description,
        mandate_binding=intent.mandate_binding,
        selected_instrument=intent.selected_instrument,
        timeframe=intent.timeframe,
        start_at=intent.start_at,
        warmup_candles=intent.warmup_candles,
        strategy_snapshot=_snapshot(),
        execution=intent.execution,
        instrument_constraints=intent.instrument_constraints,
        risk_limits=intent.risk_limits,
        history_window=intent.history_window,
        max_candles=intent.max_candles,
        max_orders=intent.max_orders,
        max_events=intent.max_events,
        engine_version=intent.engine_version,
        market_regime_policy=intent.market_regime_policy,
    )


def _revision(
    *,
    profile_id: UUID = PROFILE_ID,
    revision: int = 3,
) -> OperationalPaperSessionProfileRevision:
    specification = _specification()
    return OperationalPaperSessionProfileRevision(
        profile_id=profile_id,
        revision=revision,
        specification=specification,
        specification_checksum=operational_paper_session_profile_specification_checksum(
            specification
        ),
        created_by=ADMIN_ID,
        created_at=NOW,
    )


def _profile(
    state: OperationalPaperSessionProfileState = OperationalPaperSessionProfileState.DRAFT,
    *,
    profile_id: UUID = PROFILE_ID,
    current_revision: int = 3,
) -> OperationalPaperSessionProfile:
    approved = state in {
        OperationalPaperSessionProfileState.APPROVED,
        OperationalPaperSessionProfileState.ARCHIVED,
    }
    archived = state is OperationalPaperSessionProfileState.ARCHIVED
    checksum = operational_paper_session_profile_specification_checksum(_specification())
    return OperationalPaperSessionProfile(
        profile_id=profile_id,
        state=state,
        current_revision=current_revision,
        record_version=7 + int(approved) + int(archived),
        approved_revision=current_revision if approved else None,
        approved_checksum=checksum if approved else None,
        created_by=ADMIN_ID,
        created_at=NOW,
        approved_by=ADMIN_ID if approved else None,
        approved_at=NOW + timedelta(minutes=1) if approved else None,
        archived_by=ADMIN_ID if archived else None,
        archived_at=NOW + timedelta(minutes=2) if archived else None,
        create_idempotency_key=IDEMPOTENCY_KEY,
        create_intent_fingerprint="d" * 64,
    )


def _current(*, profile_id: UUID = PROFILE_ID, revision: int = 3) -> Current:
    return (
        _profile(profile_id=profile_id, current_revision=revision),
        _revision(profile_id=profile_id, revision=revision),
    )


def _intent_payload() -> dict[str, object]:
    return {
        "name": "  Primary paper profile  ",
        "description": "  Deterministic non-capital policy.  ",
        "mandate_binding": {
            "mandate_id": str(MANDATE_ID),
            "approved_revision": 11,
            "specification_checksum": MANDATE_CHECKSUM,
        },
        "selected_instrument": {
            "exchange": "binance",
            "market_type": "spot",
            "base_asset": " btc ",
            "quote_asset": "usdt",
        },
        "timeframe": "1h",
        "start_at": NOW.isoformat(),
        "warmup_candles": 20,
        "strategy_definition_id": str(STRATEGY_ID),
        "expected_strategy_definition_revision": 17,
        "expected_strategy_parameters_checksum": STRATEGY_PARAMETERS_CHECKSUM,
        "execution": {
            "fees": {"maker_fee_bps": "1.25", "taker_fee_bps": "2.50"},
            "slippage": {"kind": "FIXED_BPS", "fixed_bps": "3.75"},
            "intrabar_policy": "CONSERVATIVE",
            "force_close_at_end": False,
            "position_sizing": {
                "kind": "equity_percent",
                "value": "25.125",
                "minimum_quote_reserve": "10.50",
            },
        },
        "instrument_constraints": {
            "minimum_quantity": "0.00000001",
            "quantity_step": "0.00000001",
            "price_tick": "0.01",
            "minimum_notional": "10.25",
            "maximum_notional": "10000.75",
        },
        "risk_limits": {
            "max_order_notional": "500.50",
            "max_position_notional": "1000.75",
            "max_open_orders": 4,
            "max_total_orders": 50,
            "max_drawdown_pct": "20.25",
            "stop_on_max_drawdown": True,
            "allow_all_in": False,
            "minimum_quote_reserve": "20.50",
            "stop_loss": {"kind": "fixed_percent", "value": "5.125"},
        },
        "history_window": 512,
        "max_candles": 10_000,
        "max_orders": 1_000,
        "max_events": 10_000,
        "engine_version": "paper-engine-v1",
        "market_regime_policy": {
            "fast_ema_period": 8,
            "slow_ema_period": 21,
            "atr_period": 13,
            "volatile_atr_ratio": "0.03125",
            "trend_strength_threshold": "1.125",
            "schema_version": 1,
        },
    }


def _expected_specification_json() -> dict[str, object]:
    snapshot = _snapshot()
    return {
        "schema_version": 1,
        "name": "Primary paper profile",
        "description": "Deterministic non-capital policy.",
        "mandate_binding": {
            "mandate_id": str(MANDATE_ID),
            "approved_revision": 11,
            "specification_checksum": MANDATE_CHECKSUM,
        },
        "selected_instrument": {
            "exchange": "binance",
            "market_type": "spot",
            "base_asset": "BTC",
            "quote_asset": "USDT",
        },
        "timeframe": "1h",
        "start_at": NOW.isoformat().replace("+00:00", "Z"),
        "warmup_candles": 20,
        "strategy_snapshot": {
            "snapshot_schema_version": 1,
            "strategy_definition_id": str(STRATEGY_ID),
            "source_revision": 17,
            "plugin_name": "gate-3-strategy",
            "plugin_version": "2.0.0",
            "plugin_schema_version": 3,
            "strategy_lifecycle_version": 2,
            "parameters": [
                {"name": "enabled", "type": "boolean", "value": True},
                {"name": "label", "type": "string", "value": "paper"},
                {"name": "period", "type": "integer", "value": 13},
                {"name": "threshold", "type": "decimal", "value": "0.123456789"},
            ],
            "parameters_checksum": STRATEGY_PARAMETERS_CHECKSUM,
            "snapshot_checksum": snapshot.snapshot_checksum,
        },
        "execution": {
            "fees": {"maker_fee_bps": "1.25", "taker_fee_bps": "2.5"},
            "slippage": {"kind": "FIXED_BPS", "fixed_bps": "3.75"},
            "intrabar_policy": "CONSERVATIVE",
            "force_close_at_end": False,
            "position_sizing": {
                "kind": "equity_percent",
                "value": "25.125",
                "minimum_quote_reserve": "10.5",
            },
        },
        "instrument_constraints": {
            "minimum_quantity": "0.00000001",
            "quantity_step": "0.00000001",
            "price_tick": "0.01",
            "minimum_notional": "10.25",
            "maximum_notional": "10000.75",
        },
        "risk_limits": {
            "max_order_notional": "500.5",
            "max_position_notional": "1000.75",
            "max_open_orders": 4,
            "max_total_orders": 50,
            "max_drawdown_pct": "20.25",
            "stop_on_max_drawdown": True,
            "allow_all_in": False,
            "minimum_quote_reserve": "20.5",
            "stop_loss": {"kind": "fixed_percent", "value": "5.125"},
        },
        "history_window": 512,
        "max_candles": 10_000,
        "max_orders": 1_000,
        "max_events": 10_000,
        "engine_version": "paper-engine-v1",
        "market_regime_policy": {
            "fast_ema_period": 8,
            "slow_ema_period": 21,
            "atr_period": 13,
            "volatile_atr_ratio": "0.03125",
            "trend_strength_threshold": "1.125",
            "schema_version": 1,
        },
    }


class FakeJWTVerifier:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    async def verify(self, token: str) -> UUID:
        self.tokens.append(token)
        return ADMIN_ID


class FakeAdminService:
    def __init__(self) -> None:
        self.allowed = True
        self.checked_users: list[UUID] = []

    async def is_admin(self, user_id: UUID) -> bool:
        self.checked_users.append(user_id)
        return self.allowed


class FakeProfileService:
    def __init__(self) -> None:
        self.list_call: tuple[int, int, OperationalPaperSessionProfileState | None] | None = None
        self.get_calls: list[UUID] = []
        self.revision_list_call: tuple[UUID, int, int] | None = None
        self.get_revision_call: tuple[UUID, int] | None = None
        self.create_call: tuple[OperationalPaperSessionProfileCreateIntent, UUID, str] | None = None
        self.replace_call: (
            tuple[
                UUID,
                OperationalPaperSessionProfileCreateIntent,
                int,
                int,
                UUID,
            ]
            | None
        ) = None
        self.approve_call: tuple[UUID, int, str, int, UUID] | None = None
        self.archive_call: tuple[UUID, int, UUID] | None = None
        self.list_result: CurrentPage = (
            [_current(), _current(profile_id=OTHER_PROFILE_ID, revision=2)],
            11,
        )
        self.revision_list_result: RevisionPage = (
            [_revision(revision=3), _revision(revision=2)],
            3,
        )
        self.error: Exception | None = None

    def _raise_configured_error(self) -> None:
        if self.error is not None:
            raise self.error

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        state: OperationalPaperSessionProfileState | None = None,
    ) -> CurrentPage:
        self.list_call = (limit, offset, state)
        self._raise_configured_error()
        return self.list_result

    async def get(self, profile_id: UUID) -> Current:
        self.get_calls.append(profile_id)
        self._raise_configured_error()
        return _current(profile_id=profile_id)

    async def list_revisions(
        self,
        profile_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> RevisionPage:
        self.revision_list_call = (profile_id, limit, offset)
        self._raise_configured_error()
        return self.revision_list_result

    async def get_revision(
        self,
        profile_id: UUID,
        revision: int,
    ) -> OperationalPaperSessionProfileRevision:
        self.get_revision_call = (profile_id, revision)
        self._raise_configured_error()
        return _revision(profile_id=profile_id, revision=revision)

    async def create(
        self,
        intent: OperationalPaperSessionProfileCreateIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
    ) -> Current:
        self.create_call = (intent, actor_id, idempotency_key)
        self._raise_configured_error()
        return _current()

    async def replace_draft(
        self,
        profile_id: UUID,
        intent: OperationalPaperSessionProfileCreateIntent,
        *,
        expected_revision: int,
        expected_record_version: int,
        actor_id: UUID,
    ) -> Current:
        self.replace_call = (
            profile_id,
            intent,
            expected_revision,
            expected_record_version,
            actor_id,
        )
        self._raise_configured_error()
        return _current(profile_id=profile_id)

    async def approve(
        self,
        profile_id: UUID,
        *,
        expected_revision: int,
        expected_checksum: str,
        expected_record_version: int,
        actor_id: UUID,
    ) -> OperationalPaperSessionProfile:
        self.approve_call = (
            profile_id,
            expected_revision,
            expected_checksum,
            expected_record_version,
            actor_id,
        )
        self._raise_configured_error()
        return _profile(OperationalPaperSessionProfileState.APPROVED)

    async def archive(
        self,
        profile_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
    ) -> OperationalPaperSessionProfile:
        self.archive_call = (profile_id, expected_record_version, actor_id)
        self._raise_configured_error()
        return _profile(OperationalPaperSessionProfileState.ARCHIVED)


@pytest.fixture
def api() -> tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService]:
    application = create_app()
    service = FakeProfileService()
    verifier = FakeJWTVerifier()
    admin_service = FakeAdminService()

    async def verifier_override() -> FakeJWTVerifier:
        return verifier

    async def admin_service_override() -> FakeAdminService:
        return admin_service

    async def profile_service_override() -> FakeProfileService:
        return service

    application.dependency_overrides[get_jwt_verifier] = verifier_override
    application.dependency_overrides[get_admin_service] = admin_service_override
    application.dependency_overrides[get_operational_paper_session_profile_service] = (
        profile_service_override
    )
    return application, service, verifier, admin_service


@pytest.fixture
async def client(
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
) -> AsyncIterator[AsyncClient]:
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
        ("POST", f"{PREFIX}/{PROFILE_ID}/archive", {"expected_record_version": 7}),
    ],
)
async def test_anonymous_requests_are_rejected_before_service(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    response = await client.request(method, path, json=payload)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert api[1].list_call is None
    assert api[1].archive_call is None
    assert api[2].tokens == []
    assert api[3].checked_users == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", PREFIX, None),
        ("POST", f"{PREFIX}/{PROFILE_ID}/archive", {"expected_record_version": 7}),
    ],
)
async def test_non_administrators_are_rejected_before_service(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    api[3].allowed = False

    response = await client.request(method, path, headers=AUTH_HEADERS, json=payload)

    assert response.status_code == 403
    assert api[1].list_call is None
    assert api[1].archive_call is None
    assert api[3].checked_users == [ADMIN_ID]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({}, (20, 0, None)),
        (
            {"limit": 7, "offset": 4, "state": "APPROVED"},
            (7, 4, OperationalPaperSessionProfileState.APPROVED),
        ),
    ],
)
async def test_list_preserves_bounds_state_order_total_and_no_store(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
    params: dict[str, str | int],
    expected: tuple[int, int, OperationalPaperSessionProfileState | None],
) -> None:
    response = await client.get(PREFIX, headers=AUTH_HEADERS, params=params)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert api[1].list_call == expected
    body = response.json()
    assert (body["limit"], body["offset"], body["total"]) == (*expected[:2], 11)
    assert [item["profile"]["profile_id"] for item in body["items"]] == [
        str(PROFILE_ID),
        str(OTHER_PROFILE_ID),
    ]
    assert "create_idempotency_key" not in response.text
    assert "create_intent_fingerprint" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"offset": 1_000_001},
        {"state": "UNKNOWN"},
    ],
)
async def test_list_rejects_invalid_transport_bounds(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
    params: dict[str, str | int],
) -> None:
    response = await client.get(PREFIX, headers=AUTH_HEADERS, params=params)

    assert response.status_code == 422
    assert api[1].list_call is None


@pytest.mark.asyncio
async def test_create_converts_complete_intent_and_uses_authenticated_actor(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
) -> None:
    response = await client.post(
        PREFIX,
        headers=AUTH_HEADERS,
        json={"intent": _intent_payload(), "idempotency_key": IDEMPOTENCY_KEY},
    )

    assert response.status_code == 201
    assert api[1].create_call == (_intent(), ADMIN_ID, IDEMPOTENCY_KEY)
    assert api[1].create_call[1] != OTHER_ACTOR_ID
    assert response.json()["revision"]["specification"] == _expected_specification_json()


@pytest.mark.asyncio
async def test_create_replay_uses_same_endpoint_and_deterministic_response(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
) -> None:
    payload = {"intent": _intent_payload(), "idempotency_key": IDEMPOTENCY_KEY}

    first = await client.post(PREFIX, headers=AUTH_HEADERS, json=payload)
    second = await client.post(PREFIX, headers=AUTH_HEADERS, json=payload)

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert api[1].create_call == (_intent(), ADMIN_ID, IDEMPOTENCY_KEY)


@pytest.mark.asyncio
async def test_payload_cannot_spoof_actor_or_server_generated_fields(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
) -> None:
    forbidden = {
        "actor_id": str(OTHER_ACTOR_ID),
        "profile_id": str(PROFILE_ID),
        "state": "APPROVED",
        "record_version": 99,
        "specification_checksum": APPROVAL_CHECKSUM,
    }

    response = await client.post(
        PREFIX,
        headers=AUTH_HEADERS,
        json={
            "intent": _intent_payload(),
            "idempotency_key": IDEMPOTENCY_KEY,
            **forbidden,
        },
    )

    assert response.status_code == 422
    assert api[1].create_call is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_path", "value", "expected_status"),
    [
        (("timeframe",), "2h", 422),
        (("start_at",), "2026-08-24T18:00:00-03:00", 422),
        (("execution", "fees", "maker_fee_bps"), 1.25, 422),
        (("execution", "force_close_at_end"), True, 400),
    ],
)
async def test_create_distinguishes_transport_and_domain_validation(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
    field_path: tuple[str, ...],
    value: object,
    expected_status: int,
) -> None:
    intent = _intent_payload()
    target = intent
    for part in field_path[:-1]:
        target = cast(dict[str, object], target[part])
    target[field_path[-1]] = value

    response = await client.post(
        PREFIX,
        headers=AUTH_HEADERS,
        json={"intent": intent, "idempotency_key": IDEMPOTENCY_KEY},
    )

    assert response.status_code == expected_status
    assert api[1].create_call is None


@pytest.mark.asyncio
async def test_create_accepts_domain_valid_decomposed_name_at_canonical_boundary(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
) -> None:
    raw_name = "e\u0301" * MAX_OPERATIONAL_PAPER_SESSION_PROFILE_NAME_LENGTH
    canonical_name = "é" * MAX_OPERATIONAL_PAPER_SESSION_PROFILE_NAME_LENGTH
    assert len(raw_name) > MAX_OPERATIONAL_PAPER_SESSION_PROFILE_NAME_LENGTH
    assert len(canonical_name) == MAX_OPERATIONAL_PAPER_SESSION_PROFILE_NAME_LENGTH
    intent = _intent_payload()
    intent["name"] = raw_name

    response = await client.post(
        PREFIX,
        headers=AUTH_HEADERS,
        json={"intent": intent, "idempotency_key": IDEMPOTENCY_KEY},
    )

    assert response.status_code == 201
    assert api[1].create_call is not None
    assert api[1].create_call[0].name == canonical_name


@pytest.mark.asyncio
async def test_create_accepts_name_whitespace_beyond_raw_boundary(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
) -> None:
    canonical_name = "N" * MAX_OPERATIONAL_PAPER_SESSION_PROFILE_NAME_LENGTH
    raw_name = f" \r\n{canonical_name}\r\n "
    assert len(raw_name) > MAX_OPERATIONAL_PAPER_SESSION_PROFILE_NAME_LENGTH
    intent = _intent_payload()
    intent["name"] = raw_name

    response = await client.post(
        PREFIX,
        headers=AUTH_HEADERS,
        json={"intent": intent, "idempotency_key": IDEMPOTENCY_KEY},
    )

    assert response.status_code == 201
    assert api[1].create_call is not None
    assert api[1].create_call[0].name == canonical_name


@pytest.mark.asyncio
async def test_create_accepts_description_newlines_beyond_raw_boundary(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
) -> None:
    canonical_description = "D" * MAX_OPERATIONAL_PAPER_SESSION_PROFILE_DESCRIPTION_LENGTH
    raw_description = f"\r\n{canonical_description}\r\n"
    assert len(raw_description) > MAX_OPERATIONAL_PAPER_SESSION_PROFILE_DESCRIPTION_LENGTH
    intent = _intent_payload()
    intent["description"] = raw_description

    response = await client.post(
        PREFIX,
        headers=AUTH_HEADERS,
        json={"intent": intent, "idempotency_key": IDEMPOTENCY_KEY},
    )

    assert response.status_code == 201
    assert api[1].create_call is not None
    assert api[1].create_call[0].description == canonical_description


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "maximum"),
    [
        ("name", MAX_OPERATIONAL_PAPER_SESSION_PROFILE_NAME_LENGTH),
        ("description", MAX_OPERATIONAL_PAPER_SESSION_PROFILE_DESCRIPTION_LENGTH),
    ],
)
async def test_create_rejects_canonically_oversized_text_through_domain_contract(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
    field: str,
    maximum: int,
) -> None:
    intent = _intent_payload()
    intent[field] = "X" * (maximum + 1)

    response = await client.post(
        PREFIX,
        headers=AUTH_HEADERS,
        json={"intent": intent, "idempotency_key": IDEMPOTENCY_KEY},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == ("operational_paper_session_profile_bounds_exceeded")
    assert api[1].create_call is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field_path",
    [
        ("mandate_binding", "approved_revision"),
        ("expected_strategy_definition_revision",),
    ],
)
async def test_create_preserves_each_bigint_bounds_error_taxonomy(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
    field_path: tuple[str, ...],
) -> None:
    intent = _intent_payload()
    target = intent
    for part in field_path[:-1]:
        target = cast(dict[str, object], target[part])
    target[field_path[-1]] = 1 << 63

    response = await client.post(
        PREFIX,
        headers=AUTH_HEADERS,
        json={"intent": intent, "idempotency_key": IDEMPOTENCY_KEY},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == ("operational_paper_session_profile_bounds_exceeded")
    assert api[1].create_call is None


@pytest.mark.asyncio
async def test_create_retains_invalid_specification_taxonomy_for_nested_semantics(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
) -> None:
    intent = _intent_payload()
    execution = cast(dict[str, object], intent["execution"])
    execution["force_close_at_end"] = True

    response = await client.post(
        PREFIX,
        headers=AUTH_HEADERS,
        json={"intent": intent, "idempotency_key": IDEMPOTENCY_KEY},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == (
        "operational_paper_session_profile_invalid_specification"
    )
    assert api[1].create_call is None


@pytest.mark.asyncio
async def test_get_forwards_uuid_projects_current_revision_and_maps_not_found(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
) -> None:
    response = await client.get(f"{PREFIX}/{PROFILE_ID}", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert api[1].get_calls == [PROFILE_ID]
    assert response.json()["revision"]["revision"] == 3

    api[1].error = OperationalPaperSessionProfileNotFoundError()
    missing = await client.get(f"{PREFIX}/{PROFILE_ID}", headers=AUTH_HEADERS)
    malformed = await client.get(f"{PREFIX}/not-a-uuid", headers=AUTH_HEADERS)

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "operational_paper_session_profile_not_found"
    assert malformed.status_code == 422


@pytest.mark.asyncio
async def test_replace_forwards_distinct_tokens_intent_and_authenticated_actor(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
) -> None:
    response = await client.patch(
        f"{PREFIX}/{PROFILE_ID}",
        headers=AUTH_HEADERS,
        json={
            "intent": _intent_payload(),
            "expected_revision": 3,
            "expected_record_version": 7,
        },
    )

    assert response.status_code == 200
    assert api[1].replace_call == (PROFILE_ID, _intent(), 3, 7, ADMIN_ID)
    assert response.json()["profile"]["current_revision"] == 3
    assert response.json()["profile"]["record_version"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_revision", True),
        ("expected_revision", "3"),
        ("expected_revision", 3.0),
        ("expected_record_version", True),
        ("expected_record_version", "7"),
        ("expected_record_version", 7.0),
    ],
)
async def test_replace_rejects_coerced_concurrency_tokens(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
    field: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "intent": _intent_payload(),
        "expected_revision": 3,
        "expected_record_version": 7,
    }
    payload[field] = value

    response = await client.patch(
        f"{PREFIX}/{PROFILE_ID}",
        headers=AUTH_HEADERS,
        json=payload,
    )

    assert response.status_code == 422
    assert api[1].replace_call is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        OperationalPaperSessionProfileRevisionConflictError(),
        OperationalPaperSessionProfileRecordVersionConflictError(),
        OperationalPaperSessionProfileStateTransitionConflictError(),
    ],
)
async def test_replace_conflicts_use_existing_domain_error_contract(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
    error: DomainError,
) -> None:
    api[1].error = error
    response = await client.patch(
        f"{PREFIX}/{PROFILE_ID}",
        headers=AUTH_HEADERS,
        json={
            "intent": _intent_payload(),
            "expected_revision": 3,
            "expected_record_version": 7,
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == error.code


@pytest.mark.asyncio
async def test_history_preserves_order_total_bounds_and_no_store(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
) -> None:
    response = await client.get(
        f"{PREFIX}/{PROFILE_ID}/revisions",
        headers=AUTH_HEADERS,
        params={"limit": 6, "offset": 2},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert api[1].revision_list_call == (PROFILE_ID, 6, 2)
    assert [item["revision"] for item in response.json()["items"]] == [3, 2]
    assert response.json()["total"] == 3

    api[1].revision_list_result = ([], 3)
    beyond = await client.get(
        f"{PREFIX}/{PROFILE_ID}/revisions",
        headers=AUTH_HEADERS,
        params={"limit": 6, "offset": 99},
    )
    assert beyond.json()["items"] == []
    assert beyond.json()["total"] == 3

    api[1].error = OperationalPaperSessionProfileNotFoundError()
    missing = await client.get(
        f"{PREFIX}/{PROFILE_ID}/revisions",
        headers=AUTH_HEADERS,
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_exact_revision_forwards_positive_identity_and_maps_not_found(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
) -> None:
    response = await client.get(
        f"{PREFIX}/{PROFILE_ID}/revisions/2",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert api[1].get_revision_call == (PROFILE_ID, 2)
    assert response.json()["revision"] == 2

    invalid = await client.get(f"{PREFIX}/{PROFILE_ID}/revisions/0", headers=AUTH_HEADERS)
    assert invalid.status_code == 422

    api[1].error = OperationalPaperSessionProfileNotFoundError()
    missing = await client.get(
        f"{PREFIX}/{PROFILE_ID}/revisions/99",
        headers=AUTH_HEADERS,
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_approve_forwards_distinct_guards_and_authenticated_actor(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
) -> None:
    response = await client.post(
        f"{PREFIX}/{PROFILE_ID}/approve",
        headers=AUTH_HEADERS,
        json={
            "expected_revision": 3,
            "expected_checksum": APPROVAL_CHECKSUM,
            "expected_record_version": 7,
        },
    )

    assert response.status_code == 200
    assert api[1].approve_call == (PROFILE_ID, 3, APPROVAL_CHECKSUM, 7, ADMIN_ID)
    assert response.json()["state"] == "APPROVED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        OperationalPaperSessionProfileChecksumMismatchError(),
        OperationalPaperSessionProfileRevisionConflictError(),
        OperationalPaperSessionProfileRecordVersionConflictError(),
        OperationalPaperSessionProfileStateTransitionConflictError(),
        StrategyDefinitionCompatibilityError(),
        StrategyDefinitionRevisionConflictError(),
        StrategyDefinitionArchivedError(),
        StrategyDefinitionNotFoundError(),
        OperationalMandateStateTransitionConflictError(),
        OperationalMandateNotFoundError(),
    ],
)
async def test_approve_surfaces_existing_safe_domain_errors(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
    error: DomainError,
) -> None:
    api[1].error = error
    response = await client.post(
        f"{PREFIX}/{PROFILE_ID}/approve",
        headers=AUTH_HEADERS,
        json={
            "expected_revision": 3,
            "expected_checksum": APPROVAL_CHECKSUM,
            "expected_record_version": 7,
        },
    )

    assert response.status_code in {404, 409}
    assert response.json()["error"]["code"] == error.code
    assert "traceback" not in response.text.lower()


@pytest.mark.asyncio
async def test_archive_forwards_record_version_and_authenticated_actor(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
) -> None:
    response = await client.post(
        f"{PREFIX}/{PROFILE_ID}/archive",
        headers=AUTH_HEADERS,
        json={"expected_record_version": 8},
    )

    assert response.status_code == 200
    assert api[1].archive_call == (PROFILE_ID, 8, ADMIN_ID)
    assert response.json()["state"] == "ARCHIVED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        OperationalPaperSessionProfileNotFoundError(),
        OperationalPaperSessionProfileRecordVersionConflictError(),
        OperationalPaperSessionProfileStateTransitionConflictError(),
    ],
)
async def test_archive_surfaces_not_found_and_conflicts(
    client: AsyncClient,
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
    error: DomainError,
) -> None:
    api[1].error = error
    response = await client.post(
        f"{PREFIX}/{PROFILE_ID}/archive",
        headers=AUTH_HEADERS,
        json={"expected_record_version": 8},
    )

    assert response.status_code in {404, 409}
    assert response.json()["error"]["code"] == error.code


def test_router_and_openapi_expose_exactly_eight_protected_operations(
    api: tuple[FastAPI, FakeProfileService, FakeJWTVerifier, FakeAdminService],
) -> None:
    expected_methods = {
        PREFIX: {"GET", "POST"},
        f"{PREFIX}/{{profile_id}}": {"GET", "PATCH"},
        f"{PREFIX}/{{profile_id}}/revisions": {"GET"},
        f"{PREFIX}/{{profile_id}}/revisions/{{revision}}": {"GET"},
        f"{PREFIX}/{{profile_id}}/approve": {"POST"},
        f"{PREFIX}/{{profile_id}}/archive": {"POST"},
    }
    inventory: dict[str, set[str]] = {}
    for route in admin_operational_paper_session_profiles.router.routes:
        route_path = getattr(route, "path", None)
        route_methods = getattr(route, "methods", None)
        assert isinstance(route_path, str)
        assert route_methods is not None
        inventory.setdefault(route_path, set()).update(str(item) for item in route_methods)
    assert inventory == expected_methods

    schema = api[0].openapi()
    paths = cast(dict[str, dict[str, object]], schema["paths"])
    profile_paths = {path: item for path, item in paths.items() if path.startswith(PREFIX)}
    assert set(profile_paths) == set(expected_methods)
    assert not any(
        path.startswith("/api/v1/app/operational-paper-session-profiles")
        or path.startswith("/api/v1/operational-paper-session-profiles")
        for path in paths
    )

    operation_ids: set[str] = set()
    for path, methods in expected_methods.items():
        for method in methods:
            operation = cast(dict[str, object], profile_paths[path][method.lower()])
            assert operation.get("security")
            operation_id = operation.get("operationId")
            assert isinstance(operation_id, str)
            operation_ids.add(operation_id)
            responses = cast(dict[str, object], operation["responses"])
            assert {"400", "401", "403", "404", "409", "422", "500", "503"}.issubset(responses)
    assert len(operation_ids) == 8

    components = cast(dict[str, object], schema["components"])
    schemas = cast(dict[str, dict[str, object]], components["schemas"])
    aggregate = cast(
        dict[str, object],
        schemas["OperationalPaperSessionProfileResponse"]["properties"],
    )
    assert "create_idempotency_key" not in aggregate
    assert "create_intent_fingerprint" not in aggregate
    create_properties = cast(
        dict[str, object],
        schemas["OperationalPaperSessionProfileCreateRequest"]["properties"],
    )
    assert set(create_properties) == {"intent", "idempotency_key"}


def test_dependency_composes_repository_builtins_and_utc_clock_without_build() -> None:
    database = Database("postgresql://adt_test@127.0.0.1:1/adt_test")

    service = get_operational_paper_session_profile_service(database)

    assert isinstance(service, OperationalPaperSessionProfileService)
    assert isinstance(service._repository, PostgresOperationalPaperSessionProfileRepository)
    assert service._repository._database is database
    assert isinstance(service._registry, StrategyPluginRegistry)
    assert service._registry.identities == StrategyPluginRegistry.builtins().identities
    observed_at = service._clock()
    assert observed_at.tzinfo is UTC
