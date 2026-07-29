import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """Provide test client."""
    return TestClient(app)


def test_system_status(client: TestClient) -> None:
    """Test system status endpoint."""
    response = client.get("/api/v1/system/status")
    assert response.status_code == 200

    data = response.json()
    assert "status" in data
    assert "version" in data
    assert "environment" in data
    assert "timestamp" in data
    assert data["status"] == "operational"
