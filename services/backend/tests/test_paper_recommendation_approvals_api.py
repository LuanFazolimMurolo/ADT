"""Remote-free HTTP contract tests for PAPER recommendation approvals."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.paper_recommendation_approvals import (
    get_paper_recommendation_approval_service,
)
from app.api.routes import admin_paper_recommendation_approvals
from app.paper_recommendation_approvals import (
    PaperRecommendationApproval,
    PaperRecommendationApprovalIntent,
    approve_paper_recommendation,
    revoke_paper_recommendation_approval,
)
from app.services.ml_recommendations import MLRecommendationNotFoundError
from app.services.paper_recommendation_approvals import (
    PaperRecommendationApprovalIdempotencyConflictError,
    PaperRecommendationApprovalNotFoundError,
    PaperRecommendationApprovalRecordVersionConflictError,
)

ADMIN_ID = UUID("10000000-0000-4000-8000-000000000001")
OTHER_ADMIN_ID = UUID("20000000-0000-4000-8000-000000000002")
APPROVAL_ID = UUID("30000000-0000-4000-8000-000000000003")
NOW = datetime(2026, 9, 27, 9, tzinfo=UTC)
RECOMMENDATION_ID = "a" * 64
IDEMPOTENCY_KEY = "ml-paper-approval:http:1"


def _intent() -> PaperRecommendationApprovalIntent:
    return PaperRecommendationApprovalIntent(
        recommendation_identity=RECOMMENDATION_ID,
        recommendation_checksum="b" * 64,
        candidate_identity="c" * 64,
        candidate_checksum="d" * 64,
        configuration_checksum="e" * 64,
        parameters_checksum="f" * 64,
    )


def _approved() -> PaperRecommendationApproval:
    return approve_paper_recommendation(
        _intent(),
        approval_id=APPROVAL_ID,
        actor_id=ADMIN_ID,
        approved_at=NOW,
        idempotency_key=IDEMPOTENCY_KEY,
    )


def _revoked() -> PaperRecommendationApproval:
    return revoke_paper_recommendation_approval(
        _approved(),
        actor_id=OTHER_ADMIN_ID,
        revoked_at=NOW + timedelta(minutes=1),
    )


class _ServiceDouble:
    def __init__(self) -> None:
        self.approve_result: PaperRecommendationApproval | Exception = _approved()
        self.get_result: PaperRecommendationApproval | Exception = _approved()
        self.revoke_result: PaperRecommendationApproval | Exception = _revoked()

        self.approve_calls: list[tuple[str, UUID, str]] = []
        self.get_calls: list[UUID] = []
        self.revoke_calls: list[tuple[UUID, int, UUID]] = []

    async def approve(
        self,
        recommendation_id: str,
        *,
        actor_id: UUID,
        idempotency_key: str,
    ) -> PaperRecommendationApproval:
        self.approve_calls.append((recommendation_id, actor_id, idempotency_key))

        if isinstance(self.approve_result, Exception):
            raise self.approve_result

        return self.approve_result

    async def get(
        self,
        approval_id: UUID,
    ) -> PaperRecommendationApproval:
        self.get_calls.append(approval_id)

        if isinstance(self.get_result, Exception):
            raise self.get_result

        return self.get_result

    async def revoke(
        self,
        approval_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
    ) -> PaperRecommendationApproval:
        self.revoke_calls.append((approval_id, expected_record_version, actor_id))

        if isinstance(self.revoke_result, Exception):
            raise self.revoke_result

        return self.revoke_result


@pytest.fixture
def api() -> tuple[FastAPI, _ServiceDouble]:
    application = FastAPI()
    service = _ServiceDouble()

    application.include_router(admin_paper_recommendation_approvals.router)

    application.dependency_overrides[require_administrator] = lambda: ADMIN_ID
    application.dependency_overrides[get_paper_recommendation_approval_service] = lambda: service

    return application, service


@pytest.fixture
async def client(
    api: tuple[FastAPI, _ServiceDouble],
) -> AsyncIterator[AsyncClient]:
    application, _service = api

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as http_client:
        yield http_client


def _assert_approved_payload(payload: dict[str, object]) -> None:
    assert payload == {
        "schema_version": 1,
        "approval_id": str(APPROVAL_ID),
        "state": "APPROVED",
        "record_version": 1,
        "recommendation_identity": RECOMMENDATION_ID,
        "recommendation_checksum": "b" * 64,
        "candidate_identity": "c" * 64,
        "candidate_checksum": "d" * 64,
        "configuration_checksum": "e" * 64,
        "parameters_checksum": "f" * 64,
        "approval_checksum": _approved().approval_checksum,
        "approved_by": str(ADMIN_ID),
        "approved_at": NOW.isoformat().replace("+00:00", "Z"),
        "create_idempotency_key": IDEMPOTENCY_KEY,
        "create_intent_fingerprint": _approved().create_intent_fingerprint,
        "revoked_by": None,
        "revoked_at": None,
    }


@pytest.mark.asyncio
async def test_create_records_server_bound_human_approval(
    client: AsyncClient,
    api: tuple[FastAPI, _ServiceDouble],
) -> None:
    _application, service = api

    response = await client.post(
        f"/api/v1/admin/ml/recommendations/{RECOMMENDATION_ID}/paper-approvals",
        json={"idempotency_key": IDEMPOTENCY_KEY},
    )

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    _assert_approved_payload(response.json())

    assert service.approve_calls == [
        (
            RECOMMENDATION_ID,
            ADMIN_ID,
            IDEMPOTENCY_KEY,
        )
    ]


@pytest.mark.asyncio
async def test_create_request_rejects_authoritative_client_fields(
    client: AsyncClient,
    api: tuple[FastAPI, _ServiceDouble],
) -> None:
    _application, service = api

    response = await client.post(
        f"/api/v1/admin/ml/recommendations/{RECOMMENDATION_ID}/paper-approvals",
        json={
            "idempotency_key": IDEMPOTENCY_KEY,
            "recommendation_checksum": "0" * 64,
            "candidate_checksum": "0" * 64,
            "approved_by": str(OTHER_ADMIN_ID),
            "state": "REVOKED",
        },
    )

    assert response.status_code == 422
    assert service.approve_calls == []


@pytest.mark.asyncio
async def test_create_validates_idempotency_key(
    client: AsyncClient,
    api: tuple[FastAPI, _ServiceDouble],
) -> None:
    _application, service = api

    response = await client.post(
        f"/api/v1/admin/ml/recommendations/{RECOMMENDATION_ID}/paper-approvals",
        json={"idempotency_key": "contains spaces"},
    )

    assert response.status_code == 422
    assert service.approve_calls == []


@pytest.mark.asyncio
async def test_create_maps_missing_recommendation_to_404(
    client: AsyncClient,
    api: tuple[FastAPI, _ServiceDouble],
) -> None:
    _application, service = api
    service.approve_result = MLRecommendationNotFoundError()

    response = await client.post(
        f"/api/v1/admin/ml/recommendations/{RECOMMENDATION_ID}/paper-approvals",
        json={"idempotency_key": IDEMPOTENCY_KEY},
    )

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["detail"]["code"] == "ml_recommendation_not_found"


@pytest.mark.asyncio
async def test_create_maps_idempotency_conflict_to_409(
    client: AsyncClient,
    api: tuple[FastAPI, _ServiceDouble],
) -> None:
    _application, service = api
    service.approve_result = PaperRecommendationApprovalIdempotencyConflictError()

    response = await client.post(
        f"/api/v1/admin/ml/recommendations/{RECOMMENDATION_ID}/paper-approvals",
        json={"idempotency_key": IDEMPOTENCY_KEY},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "paper_recommendation_approval_idempotency_conflict"
    )


@pytest.mark.asyncio
async def test_get_returns_persisted_approval_evidence(
    client: AsyncClient,
    api: tuple[FastAPI, _ServiceDouble],
) -> None:
    _application, service = api

    response = await client.get(f"/api/v1/admin/ml/paper-recommendation-approvals/{APPROVAL_ID}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    _assert_approved_payload(response.json())
    assert service.get_calls == [APPROVAL_ID]


@pytest.mark.asyncio
async def test_get_missing_approval_maps_to_404(
    client: AsyncClient,
    api: tuple[FastAPI, _ServiceDouble],
) -> None:
    _application, service = api
    service.get_result = PaperRecommendationApprovalNotFoundError()

    response = await client.get(f"/api/v1/admin/ml/paper-recommendation-approvals/{APPROVAL_ID}")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == ("paper_recommendation_approval_not_found")


@pytest.mark.asyncio
async def test_revoke_requires_expected_record_version(
    client: AsyncClient,
    api: tuple[FastAPI, _ServiceDouble],
) -> None:
    _application, service = api

    response = await client.post(
        f"/api/v1/admin/ml/paper-recommendation-approvals/{APPROVAL_ID}/revoke",
        json={},
    )

    assert response.status_code == 422
    assert service.revoke_calls == []


@pytest.mark.asyncio
async def test_revoke_records_human_revocation_only(
    client: AsyncClient,
    api: tuple[FastAPI, _ServiceDouble],
) -> None:
    _application, service = api

    response = await client.post(
        f"/api/v1/admin/ml/paper-recommendation-approvals/{APPROVAL_ID}/revoke",
        json={"expected_record_version": 1},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"

    payload = response.json()
    assert payload["state"] == "REVOKED"
    assert payload["record_version"] == 2
    assert payload["recommendation_identity"] == RECOMMENDATION_ID
    assert payload["candidate_identity"] == "c" * 64
    assert payload["approval_checksum"] == _approved().approval_checksum
    assert payload["revoked_by"] == str(OTHER_ADMIN_ID)
    assert payload["revoked_at"] == (NOW + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")

    # Authentication supplies ADMIN_ID to the route.
    assert service.revoke_calls == [
        (
            APPROVAL_ID,
            1,
            ADMIN_ID,
        )
    ]


@pytest.mark.asyncio
async def test_revoke_maps_record_version_conflict_to_409(
    client: AsyncClient,
    api: tuple[FastAPI, _ServiceDouble],
) -> None:
    _application, service = api
    service.revoke_result = PaperRecommendationApprovalRecordVersionConflictError()

    response = await client.post(
        f"/api/v1/admin/ml/paper-recommendation-approvals/{APPROVAL_ID}/revoke",
        json={"expected_record_version": 1},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "paper_recommendation_approval_record_version_conflict"
    )


@pytest.mark.asyncio
async def test_revoke_missing_approval_maps_to_404(
    client: AsyncClient,
    api: tuple[FastAPI, _ServiceDouble],
) -> None:
    _application, service = api
    service.revoke_result = PaperRecommendationApprovalNotFoundError()

    response = await client.post(
        f"/api/v1/admin/ml/paper-recommendation-approvals/{APPROVAL_ID}/revoke",
        json={"expected_record_version": 1},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == ("paper_recommendation_approval_not_found")


def test_route_source_contains_no_operational_authority_calls() -> None:
    source = Path(admin_paper_recommendation_approvals.__file__).read_text()

    forbidden = (
        "PaperTradingService",
        "OperationalPaperSessionActivation",
        "OperationalPaperCapitalAuthorization",
        "OperationalPaperSessionRun",
        "run_once(",
        "START",
    )

    for symbol in forbidden:
        assert symbol not in source
