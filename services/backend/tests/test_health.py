from collections.abc import AsyncIterator

import httpx
import pytest_asyncio

from app.api.dependencies.resources import get_database
from app.main import app


class FakeDatabase:
    """Local readiness dependency with no network access."""

    def __init__(self) -> None:
        self.healthy = True

    async def health_check(self) -> bool:
        return self.healthy


@pytest_asyncio.fixture
async def client() -> AsyncIterator[tuple[httpx.AsyncClient, FakeDatabase]]:
    """Provide test client."""
    database = FakeDatabase()

    async def override_database() -> FakeDatabase:
        return database

    app.dependency_overrides[get_database] = override_database
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client, database
    app.dependency_overrides.clear()


async def test_health(client: tuple[httpx.AsyncClient, FakeDatabase]) -> None:
    """Test health endpoint."""
    http_client, _database = client
    response = await http_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


async def test_readiness(client: tuple[httpx.AsyncClient, FakeDatabase]) -> None:
    """Test readiness endpoint."""
    http_client, _database = client
    response = await http_client.get("/health/readiness")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


async def test_readiness_fails_closed(
    client: tuple[httpx.AsyncClient, FakeDatabase],
) -> None:
    """Readiness returns a safe 503 when PostgreSQL is unavailable."""
    http_client, database = client
    database.healthy = False

    response = await http_client.get("/health/readiness")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
