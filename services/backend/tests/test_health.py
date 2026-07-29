from collections.abc import AsyncIterator

import httpx
import pytest_asyncio

from app.main import app


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Provide test client."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


async def test_health(client: httpx.AsyncClient) -> None:
    """Test health endpoint."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


async def test_readiness(client: httpx.AsyncClient) -> None:
    """Test readiness endpoint."""
    response = await client.get("/health/readiness")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
