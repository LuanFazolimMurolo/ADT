"""Authenticated /app identity boundary tests."""

from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_authenticated_user
from app.api.dependencies.resources import get_admin_service
from app.api.routes import user_app


class _StubAdminService:
    def __init__(self, *, is_admin: bool) -> None:
        self._is_admin = is_admin
        self.calls: list[UUID] = []

    async def is_admin(self, user_id: UUID) -> bool:
        self.calls.append(user_id)
        return self._is_admin


def _client(
    user_id: UUID,
    *,
    is_admin: bool,
) -> tuple[TestClient, _StubAdminService]:
    application = FastAPI()
    application.include_router(user_app.router)
    admin_service = _StubAdminService(is_admin=is_admin)

    async def authenticated_user_override() -> UUID:
        return user_id

    def admin_service_override() -> _StubAdminService:
        return admin_service

    application.dependency_overrides[get_authenticated_user] = authenticated_user_override
    application.dependency_overrides[get_admin_service] = admin_service_override
    return TestClient(application), admin_service


def test_app_me_returns_authenticated_non_admin_without_private_claims() -> None:
    user_id = uuid4()
    client, admin_service = _client(user_id, is_admin=False)

    response = client.get("/api/v1/app/me")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "user_id": str(user_id),
        "is_admin": False,
    }
    assert admin_service.calls == [user_id]
    assert "email" not in response.text.lower()
    assert "token" not in response.text.lower()


def test_app_me_exposes_backend_authoritative_admin_membership() -> None:
    user_id = uuid4()
    client, admin_service = _client(user_id, is_admin=True)

    response = client.get("/api/v1/app/me")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["is_admin"] is True
    assert admin_service.calls == [user_id]


def test_app_me_preserves_authentication_failure() -> None:
    application = FastAPI()
    application.include_router(user_app.router)

    async def unauthenticated_override() -> UUID:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    application.dependency_overrides[get_authenticated_user] = unauthenticated_override

    with TestClient(application) as client:
        response = client.get("/api/v1/app/me")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_app_me_is_get_only() -> None:
    user_id = uuid4()
    client, _ = _client(user_id, is_admin=False)

    response = client.post("/api/v1/app/me")

    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
