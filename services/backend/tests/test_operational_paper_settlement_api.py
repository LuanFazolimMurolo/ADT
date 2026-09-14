"""HTTP boundary tests for Phase 7-14 official-era and settlement operations."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient, Response

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_operational_paper_capital_era_service,
    get_operational_paper_session_settlement_service,
)
from app.api.exceptions import setup_exception_handlers
from app.api.routes.admin_operational_paper_capital_eras import (
    router as era_router,
)
from app.api.routes.admin_operational_paper_session_settlements import (
    router as settlement_router,
)
from app.operational_paper_capital_eras import (
    OperationalPaperCapitalEra,
    OperationalPaperCapitalEraDesignationIntent,
)
from app.operational_paper_session_settlements import (
    OperationalPaperSessionSettlementIntent,
)
from app.services.operational_paper_capital_eras import (
    OperationalPaperCapitalEraService,
)
from app.services.operational_paper_session_settlements import (
    OperationalPaperSessionSettlementService,
)

ADMIN_ID = UUID("00000000-0000-0000-0000-000000000001")
SIMULATION_ID = UUID("00000000-0000-0000-0000-000000000002")
ERA_ID = UUID("00000000-0000-0000-0000-000000000003")
EPOCH_ID = UUID("00000000-0000-0000-0000-000000000004")
SETTLEMENT_ID = UUID("00000000-0000-0000-0000-000000000005")

SHA_A = "a" * 64
SHA_B = "b" * 64

NOW = datetime(2026, 9, 13, 20, 0, tzinfo=UTC)


def _era() -> OperationalPaperCapitalEra:
    from app.operational_paper_capital_eras import (
        OperationalPaperCapitalEraSpecification,
        operational_paper_capital_era_designation_intent_fingerprint,
        operational_paper_capital_era_specification_checksum,
    )

    intent = OperationalPaperCapitalEraDesignationIntent(
        simulation_id=SIMULATION_ID,
    )
    specification = OperationalPaperCapitalEraSpecification(
        1,
        1,
        SIMULATION_ID,
        "USDT",
        Decimal("10000"),
        NOW,
    )

    return OperationalPaperCapitalEra(
        era_id=ERA_ID,
        schema_version=specification.schema_version,
        designation_contract_version=specification.designation_contract_version,
        simulation_id=specification.simulation_id,
        currency=specification.currency,
        initial_capital=specification.initial_capital,
        simulation_started_at=specification.simulation_started_at,
        era_checksum=operational_paper_capital_era_specification_checksum(specification),
        designated_by=ADMIN_ID,
        designated_at=NOW,
        designation_idempotency_key="api:era:1",
        designation_intent_fingerprint=(
            operational_paper_capital_era_designation_intent_fingerprint(intent)
        ),
    )


def _settlement() -> SimpleNamespace:
    return SimpleNamespace(
        settlement_id=SETTLEMENT_ID,
        settlement_checksum=SHA_B,
        settled_by=ADMIN_ID,
        settled_at=NOW,
        settle_idempotency_key="api:settlement:1",
        settle_intent_fingerprint=SHA_A,
    )


class _EraService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def designate(
        self,
        intent: OperationalPaperCapitalEraDesignationIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
    ) -> OperationalPaperCapitalEra:
        self.calls.append(
            (
                intent,
                actor_id,
                idempotency_key,
            )
        )
        return _era()


class _SettlementService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def settle(
        self,
        intent: OperationalPaperSessionSettlementIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
    ) -> object:
        self.calls.append(
            (
                intent,
                actor_id,
                idempotency_key,
            )
        )
        return _settlement()


def _app(
    era_service: _EraService | None = None,
    settlement_service: _SettlementService | None = None,
    *,
    auth_error: int | None = None,
) -> FastAPI:
    application = FastAPI()
    setup_exception_handlers(application)
    application.include_router(era_router)
    application.include_router(settlement_router)

    era_service = era_service or _EraService()
    settlement_service = settlement_service or _SettlementService()

    async def era_dependency() -> OperationalPaperCapitalEraService:
        return cast(
            OperationalPaperCapitalEraService,
            era_service,
        )

    async def settlement_dependency() -> OperationalPaperSessionSettlementService:
        return cast(
            OperationalPaperSessionSettlementService,
            settlement_service,
        )

    application.dependency_overrides[get_operational_paper_capital_era_service] = era_dependency
    application.dependency_overrides[get_operational_paper_session_settlement_service] = (
        settlement_dependency
    )

    if auth_error is None:

        async def administrator_dependency() -> UUID:
            return ADMIN_ID

    else:
        denied_status = auth_error

        async def administrator_dependency() -> UUID:
            raise HTTPException(
                status_code=denied_status,
                detail="denied",
            )

    application.dependency_overrides[require_administrator] = administrator_dependency

    return application


async def _request(
    application: FastAPI,
    method: str,
    path: str,
    *,
    json: dict[str, object],
) -> Response:
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.request(
            method,
            path,
            json=json,
        )


@pytest.mark.asyncio
async def test_era_designation_builds_exact_intent_and_returns_201() -> None:
    service = _EraService()

    response = await _request(
        _app(era_service=service),
        "POST",
        "/api/v1/admin/operational-paper-capital-eras",
        json={
            "simulation_id": str(SIMULATION_ID),
            "idempotency_key": "api:era:1",
        },
    )

    assert response.status_code == 201
    assert len(service.calls) == 1

    intent, actor_id, key = service.calls[0]

    assert isinstance(
        intent,
        OperationalPaperCapitalEraDesignationIntent,
    )
    assert intent.simulation_id == SIMULATION_ID
    assert actor_id == ADMIN_ID
    assert key == "api:era:1"

    body = response.json()

    assert body["era_id"] == str(ERA_ID)
    assert body["simulation_id"] == str(SIMULATION_ID)
    assert body["initial_capital"] == "10000"
    assert "designation_idempotency_key" not in body
    assert "designation_intent_fingerprint" not in body


@pytest.mark.asyncio
async def test_era_request_rejects_browser_supplied_capital() -> None:
    service = _EraService()

    response = await _request(
        _app(era_service=service),
        "POST",
        "/api/v1/admin/operational-paper-capital-eras",
        json={
            "simulation_id": str(SIMULATION_ID),
            "idempotency_key": "api:era:1",
            "initial_capital": "999999",
        },
    )

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.asyncio
async def test_settlement_builds_identity_only_intent_and_returns_201() -> None:
    service = _SettlementService()

    response = await _request(
        _app(settlement_service=service),
        "POST",
        "/api/v1/admin/operational-paper-session-settlements",
        json={
            "epoch_id": str(EPOCH_ID),
            "epoch_checksum": SHA_A,
            "idempotency_key": "api:settlement:1",
        },
    )

    assert response.status_code == 201
    assert len(service.calls) == 1

    intent, actor_id, key = service.calls[0]

    assert isinstance(
        intent,
        OperationalPaperSessionSettlementIntent,
    )
    assert intent.epoch_id == EPOCH_ID
    assert intent.epoch_checksum == SHA_A
    assert actor_id == ADMIN_ID
    assert key == "api:settlement:1"

    body = response.json()

    assert body == {
        "settlement_id": str(SETTLEMENT_ID),
        "settlement_checksum": SHA_B,
        "settled_by": str(ADMIN_ID),
        "settled_at": NOW.isoformat().replace("+00:00", "Z"),
    }


@pytest.mark.asyncio
async def test_settlement_request_rejects_financial_and_local_evidence() -> None:
    service = _SettlementService()

    response = await _request(
        _app(settlement_service=service),
        "POST",
        "/api/v1/admin/operational-paper-session-settlements",
        json={
            "epoch_id": str(EPOCH_ID),
            "epoch_checksum": SHA_A,
            "idempotency_key": "api:settlement:1",
            "initial_capital": "10000",
            "final_quote_cash": "11000",
            "realized_pnl": "1000",
            "portfolio": {},
            "state_checksum": SHA_B,
            "timeline_content_checksum": SHA_B,
            "ledger_movement_id": str(SETTLEMENT_ID),
        },
    )

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_gate_3_routes_require_administrator(
    status_code: int,
) -> None:
    era_response = await _request(
        _app(auth_error=status_code),
        "POST",
        "/api/v1/admin/operational-paper-capital-eras",
        json={
            "simulation_id": str(SIMULATION_ID),
            "idempotency_key": "api:era:auth",
        },
    )

    settlement_response = await _request(
        _app(auth_error=status_code),
        "POST",
        "/api/v1/admin/operational-paper-session-settlements",
        json={
            "epoch_id": str(EPOCH_ID),
            "epoch_checksum": SHA_A,
            "idempotency_key": "api:settlement:auth",
        },
    )

    assert era_response.status_code == status_code
    assert settlement_response.status_code == status_code


def test_gate_3_openapi_request_contract_has_no_financial_authority() -> None:
    application = _app()

    schema = application.openapi()
    components = schema["components"]["schemas"]

    era_request = components["OperationalPaperCapitalEraDesignationRequest"]["properties"]
    settlement_request = components["OperationalPaperSessionSettlementRequest"]["properties"]

    assert set(era_request) == {
        "simulation_id",
        "idempotency_key",
    }

    assert set(settlement_request) == {
        "epoch_id",
        "epoch_checksum",
        "idempotency_key",
    }

    rendered = str(
        {
            "era": era_request,
            "settlement": settlement_request,
        }
    )

    for forbidden in (
        "initial_capital",
        "final_quote_cash",
        "realized_pnl",
        "unrealized_pnl",
        "portfolio",
        "state_checksum",
        "timeline_content_checksum",
        "ledger_movement_id",
    ):
        assert forbidden not in rendered
