"""Remote-free tests for worker-runtime observability HTTP reads."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.api.dependencies.auth import require_administrator
from app.api.dependencies.resources import (
    get_worker_runtime_observability_service,
)
from app.main import create_app
from app.market_data.operations import MarketOperationState
from app.market_data.worker_observability import (
    WorkerRuntimeActivityState,
    WorkerRuntimeEventType,
    WorkerRuntimeFailureCode,
    WorkerRuntimeLifecycleState,
)
from app.services.worker_observability import (
    WorkerRuntimeEventListObservation,
    WorkerRuntimeEventObservation,
    WorkerRuntimeHealthState,
    WorkerRuntimeListObservation,
    WorkerRuntimeObservation,
)

ADMIN_ID: Final = UUID("10000000-0000-4000-8000-000000000001")
INTERNAL_RUNTIME_ID: Final = UUID("20000000-0000-4000-8000-000000000002")
OPERATION_ID: Final = UUID("30000000-0000-4000-8000-000000000003")
NOW: Final = datetime(2026, 8, 20, 21, 0, tzinfo=UTC)
AUTH_HEADERS: Final = {"Authorization": "Bearer phase-7-05-test-token"}


class FakeWorkerRuntimeObservabilityService:
    def __init__(self) -> None:
        self.runtime_limits: list[int] = []
        self.event_limits: list[int] = []

    async def list_runtimes(
        self,
        *,
        limit: int,
    ) -> WorkerRuntimeListObservation:
        self.runtime_limits.append(limit)

        return WorkerRuntimeListObservation(
            observed_at=NOW,
            stale_after_seconds=120,
            items=(
                WorkerRuntimeObservation(
                    health_state=(WorkerRuntimeHealthState.HEALTHY),
                    lifecycle_state=(WorkerRuntimeLifecycleState.RUNNING),
                    activity_state=(WorkerRuntimeActivityState.ACTIVE),
                    started_at=NOW,
                    heartbeat_at=NOW,
                    stopped_at=None,
                    failure_code=None,
                ),
                WorkerRuntimeObservation(
                    health_state=(WorkerRuntimeHealthState.FAILED),
                    lifecycle_state=(WorkerRuntimeLifecycleState.FAILED),
                    activity_state=(WorkerRuntimeActivityState.IDLE),
                    started_at=NOW,
                    heartbeat_at=NOW,
                    stopped_at=NOW,
                    failure_code=(WorkerRuntimeFailureCode.LOCAL_STATE_FAILURE),
                ),
            ),
        )

    async def list_events(
        self,
        *,
        limit: int,
    ) -> WorkerRuntimeEventListObservation:
        self.event_limits.append(limit)

        return WorkerRuntimeEventListObservation(
            observed_at=NOW,
            items=(
                WorkerRuntimeEventObservation(
                    event_id=9,
                    event_type=(WorkerRuntimeEventType.OPERATION_SETTLED),
                    occurred_at=NOW,
                    operation_id=OPERATION_ID,
                    operation_state=(MarketOperationState.COMPLETED),
                ),
                WorkerRuntimeEventObservation(
                    event_id=8,
                    event_type=(WorkerRuntimeEventType.RUNTIME_STARTED),
                    occurred_at=NOW,
                    operation_id=None,
                    operation_state=None,
                ),
            ),
        )


@pytest.fixture
def api() -> tuple[
    FastAPI,
    FakeWorkerRuntimeObservabilityService,
]:
    application = create_app()
    service = FakeWorkerRuntimeObservabilityService()

    async def administrator_override() -> UUID:
        return ADMIN_ID

    async def service_override() -> FakeWorkerRuntimeObservabilityService:
        return service

    application.dependency_overrides[require_administrator] = administrator_override
    application.dependency_overrides[get_worker_runtime_observability_service] = service_override

    return application, service


@pytest.fixture
async def client(
    api: tuple[
        FastAPI,
        FakeWorkerRuntimeObservabilityService,
    ],
) -> AsyncIterator[AsyncClient]:
    application, _service = api

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as http_client:
        yield http_client


@pytest.mark.asyncio
async def test_runtime_list_is_bounded_sanitized_and_no_store(
    client: AsyncClient,
    api: tuple[
        FastAPI,
        FakeWorkerRuntimeObservabilityService,
    ],
) -> None:
    response = await client.get(
        ("/api/v1/admin/market-data/worker-observability/runtimes?limit=7"),
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert api[1].runtime_limits == [7]

    body = response.json()

    assert body["observed_at"] == "2026-08-20T21:00:00Z"
    assert body["stale_after_seconds"] == 120
    assert body["count"] == 2

    assert body["items"][0]["health_state"] == "HEALTHY"
    assert body["items"][0]["lifecycle_state"] == "RUNNING"
    assert body["items"][0]["activity_state"] == "ACTIVE"

    assert body["items"][1]["health_state"] == "FAILED"
    assert body["items"][1]["failure_code"] == "LOCAL_STATE_FAILURE"

    assert "runtime_id" not in response.text
    assert str(INTERNAL_RUNTIME_ID) not in response.text


@pytest.mark.asyncio
async def test_event_list_is_bounded_sanitized_and_no_store(
    client: AsyncClient,
    api: tuple[
        FastAPI,
        FakeWorkerRuntimeObservabilityService,
    ],
) -> None:
    response = await client.get(
        ("/api/v1/admin/market-data/worker-observability/events?limit=9"),
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert api[1].event_limits == [9]

    body = response.json()

    assert body["observed_at"] == "2026-08-20T21:00:00Z"
    assert body["count"] == 2

    assert body["items"][0]["event_id"] == 9
    assert body["items"][0]["event_type"] == "OPERATION_SETTLED"
    assert body["items"][0]["operation_id"] == str(OPERATION_ID)
    assert body["items"][0]["operation_state"] == "COMPLETED"

    assert body["items"][1]["event_type"] == "RUNTIME_STARTED"
    assert body["items"][1]["operation_id"] is None

    assert "runtime_id" not in response.text
    assert str(INTERNAL_RUNTIME_ID) not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        ("/api/v1/admin/market-data/worker-observability/runtimes"),
        ("/api/v1/admin/market-data/worker-observability/events"),
    ],
)
@pytest.mark.parametrize("limit", [0, 101])
async def test_http_limit_is_bounded_before_service_read(
    client: AsyncClient,
    api: tuple[
        FastAPI,
        FakeWorkerRuntimeObservabilityService,
    ],
    path: str,
    limit: int,
) -> None:
    response = await client.get(
        path,
        headers=AUTH_HEADERS,
        params={"limit": limit},
    )

    assert response.status_code == 422
    assert api[1].runtime_limits == []
    assert api[1].event_limits == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        ("/api/v1/admin/market-data/worker-observability/runtimes"),
        ("/api/v1/admin/market-data/worker-observability/events"),
    ],
)
@pytest.mark.parametrize(
    "status_code",
    [
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    ],
)
async def test_observability_reads_require_administrator(
    client: AsyncClient,
    api: tuple[
        FastAPI,
        FakeWorkerRuntimeObservabilityService,
    ],
    path: str,
    status_code: int,
) -> None:
    async def reject_administrator() -> UUID:
        raise HTTPException(status_code=status_code)

    api[0].dependency_overrides[require_administrator] = reject_administrator

    response = await client.get(path)

    assert response.status_code == status_code
    assert api[1].runtime_limits == []
    assert api[1].event_limits == []
