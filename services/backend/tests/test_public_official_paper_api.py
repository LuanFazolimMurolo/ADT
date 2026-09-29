from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI

from app.api.dependencies.public_official_paper import (
    get_public_official_paper_service,
)
from app.api.routes.public import router
from app.services.public_official_paper import (
    PUBLIC_OFFICIAL_PAPER_LIMIT,
    PublicOfficialPaperAvailability,
    PublicOfficialPaperItem,
    PublicOfficialPaperSnapshot,
)

NOW = datetime(2026, 9, 28, 21, 0, tzinfo=UTC)


class FakeService:
    async def get_snapshot(self) -> PublicOfficialPaperSnapshot:
        return PublicOfficialPaperSnapshot(
            observed_at=NOW,
            limit=PUBLIC_OFFICIAL_PAPER_LIMIT,
            items=(
                PublicOfficialPaperItem(
                    session_id="session-public",
                    desired_state="RUNNING",
                    observed_state="RUNNING",
                    availability=PublicOfficialPaperAvailability.RUNNING,
                    ownership_fresh=True,
                    heartbeat_at=NOW - timedelta(seconds=5),
                    lease_expires_at=NOW + timedelta(seconds=30),
                    failure_code=None,
                ),
            ),
        )


@pytest.mark.asyncio
async def test_public_official_paper_endpoint_is_get_only_and_sanitized() -> None:
    application = FastAPI()
    application.include_router(router)

    application.dependency_overrides[get_public_official_paper_service] = FakeService

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/public/official-paper")

    assert response.status_code == 200

    payload = response.json()

    assert payload["limit"] == 20
    assert payload["count"] == 1
    assert payload["items"][0]["session_id"] == "session-public"
    assert payload["items"][0]["availability"] == "RUNNING"
    assert payload["items"][0]["ownership_fresh"] is True

    forbidden = {
        "worker_id",
        "fencing_token",
        "record_version",
        "activation_checksum",
        "materialization_checksum",
        "authorization_binding",
    }

    assert forbidden.isdisjoint(payload["items"][0])


def test_public_official_paper_route_has_no_mutation_methods() -> None:
    application = FastAPI()
    application.include_router(router)

    operation = application.openapi()["paths"]["/api/v1/public/official-paper"]

    assert set(operation) == {"get"}
