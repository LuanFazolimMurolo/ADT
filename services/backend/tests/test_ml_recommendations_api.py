"""Administrator read-only HTTP contract tests for Phase 8-17."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.ml_recommendations import (
    get_ml_recommendation_read_service,
)
from app.api.routes import admin_ml_recommendations
from app.services.ml_recommendations import (
    ML_RECOMMENDATION_MAX_LIMIT,
    MLRecommendationReadService,
)
from tests.test_ml_recommendations import _recommendation

ADMIN_ID = UUID("10000000-0000-4000-8000-000000000001")


class TrackingMLRecommendationReadService(MLRecommendationReadService):
    def __init__(self) -> None:
        self.list_limits: list[int] = []
        self.get_ids: list[str] = []

        super().__init__((_recommendation(),))

    def list(
        self,
        *,
        limit: int = 50,
    ):
        self.list_limits.append(limit)
        return super().list(limit=limit)

    def get(
        self,
        recommendation_id: str,
    ):
        self.get_ids.append(recommendation_id)
        return super().get(recommendation_id)


@pytest.fixture
def api() -> tuple[
    FastAPI,
    TrackingMLRecommendationReadService,
]:
    application = FastAPI()
    application.include_router(admin_ml_recommendations.router)

    service = TrackingMLRecommendationReadService()

    async def administrator_override() -> UUID:
        return ADMIN_ID

    def service_override() -> TrackingMLRecommendationReadService:
        return service

    application.dependency_overrides[require_administrator] = administrator_override

    application.dependency_overrides[get_ml_recommendation_read_service] = service_override

    return application, service


@pytest_asyncio.fixture
async def client(
    api: tuple[
        FastAPI,
        TrackingMLRecommendationReadService,
    ],
) -> AsyncIterator[AsyncClient]:
    application, _service = api

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as http_client:
        yield http_client


@pytest.mark.asyncio
async def test_list_serializes_bounded_recommendation_evidence(
    client: AsyncClient,
    api: tuple[
        FastAPI,
        TrackingMLRecommendationReadService,
    ],
) -> None:
    response = await client.get(
        "/api/v1/admin/ml/recommendations",
        params={"limit": 1},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"

    body = response.json()

    assert body["schema_version"] == 1
    assert body["count"] == 1
    assert len(body["items"]) == 1

    item = body["items"][0]
    recommendation = _recommendation()

    assert item["identity"] == recommendation.identity
    assert item["checksum"] == recommendation.checksum

    assert item["candidate"]["identity"] == recommendation.candidate.identity
    assert item["candidate"]["checksum"] == recommendation.candidate.checksum

    assert item["candidate"]["plugin_name"] == (recommendation.candidate.plugin_name)

    assert item["rationale_codes"] == list(recommendation.rationale_codes)

    assert len(item["evidence"]) == len(recommendation.evidence)

    assert api[1].list_limits == [1]


@pytest.mark.asyncio
async def test_detail_reads_exact_recommendation(
    client: AsyncClient,
    api: tuple[
        FastAPI,
        TrackingMLRecommendationReadService,
    ],
) -> None:
    recommendation = _recommendation()

    response = await client.get(f"/api/v1/admin/ml/recommendations/{recommendation.identity}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"

    body = response.json()

    assert body["schema_version"] == 1
    assert body["identity"] == recommendation.identity
    assert body["checksum"] == recommendation.checksum
    assert body["recommendation_time"].endswith("Z")

    assert api[1].get_ids == [recommendation.identity]


@pytest.mark.asyncio
async def test_missing_recommendation_returns_404(
    client: AsyncClient,
    api: tuple[
        FastAPI,
        TrackingMLRecommendationReadService,
    ],
) -> None:
    missing = "f" * 64

    response = await client.get(f"/api/v1/admin/ml/recommendations/{missing}")

    assert response.status_code == 404
    assert api[1].get_ids == [missing]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit",
    (
        0,
        ML_RECOMMENDATION_MAX_LIMIT + 1,
    ),
)
async def test_http_limit_is_rejected_before_service_access(
    client: AsyncClient,
    api: tuple[
        FastAPI,
        TrackingMLRecommendationReadService,
    ],
    limit: int,
) -> None:
    response = await client.get(
        "/api/v1/admin/ml/recommendations",
        params={"limit": limit},
    )

    assert response.status_code == 422
    assert api[1].list_limits == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code",
    (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    ),
)
@pytest.mark.parametrize(
    "request_path",
    (
        "/api/v1/admin/ml/recommendations",
        "/api/v1/admin/ml/recommendations/" + ("a" * 64),
    ),
)
async def test_routes_require_administrator_before_service_access(
    client: AsyncClient,
    api: tuple[
        FastAPI,
        TrackingMLRecommendationReadService,
    ],
    status_code: int,
    request_path: str,
) -> None:
    async def reject_administrator() -> UUID:
        raise HTTPException(status_code=status_code)

    api[0].dependency_overrides[require_administrator] = reject_administrator

    response = await client.get(request_path)

    assert response.status_code == status_code
    assert api[1].list_limits == []
    assert api[1].get_ids == []


def test_http_boundary_is_admin_scoped_and_get_only() -> None:
    routes = {route.path: route for route in admin_ml_recommendations.router.routes}

    assert set(routes) == {
        "/api/v1/admin/ml/recommendations",
        "/api/v1/admin/ml/recommendations/{recommendation_id}",
    }

    assert all(route.methods == {"GET"} for route in routes.values())


def test_openapi_declares_bounded_read_only_contract(
    api: tuple[
        FastAPI,
        TrackingMLRecommendationReadService,
    ],
) -> None:
    schema = api[0].openapi()

    list_operation = schema["paths"]["/api/v1/admin/ml/recommendations"]["get"]

    parameters = {parameter["name"]: parameter for parameter in list_operation["parameters"]}

    limit_schema = parameters["limit"]["schema"]

    assert limit_schema["default"] == 50
    assert limit_schema["minimum"] == 1
    assert limit_schema["maximum"] == ML_RECOMMENDATION_MAX_LIMIT

    assert "/api/v1/admin/ml/recommendations/{recommendation_id}" in schema["paths"]


def test_http_module_has_no_mutating_methods() -> None:
    forbidden_methods = {
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    }

    for route in admin_ml_recommendations.router.routes:
        assert route.methods.isdisjoint(forbidden_methods)
