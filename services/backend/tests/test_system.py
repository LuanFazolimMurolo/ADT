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


async def test_system_status(client: httpx.AsyncClient) -> None:
    """Test system status endpoint."""
    response = await client.get("/api/v1/system/status")
    assert response.status_code == 200

    data = response.json()
    assert "status" in data
    assert "version" in data
    assert "environment" in data
    assert "timestamp" in data
    assert data["status"] == "operational"
