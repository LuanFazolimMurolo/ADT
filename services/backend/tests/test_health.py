import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """Provide test client."""
    return TestClient(app)


def test_health(client: TestClient) -> None:
    """Test health endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_readiness(client: TestClient) -> None:
    """Test readiness endpoint."""
    response = client.get("/health/readiness")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
