"""Project-owner paper-session read policy tests."""

from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from app.api.dependencies.auth import (
    AppPaperSessionReadAccess,
    get_app_paper_session_read_access,
    get_authenticated_user,
    require_app_paper_session_reader,
)
from app.api.dependencies.resources import get_admin_service
from app.paper_trading.repository import PaperTradingRepository


class StubAdminService:
    def __init__(self, *, is_admin: bool) -> None:
        self.is_admin_result = is_admin
        self.calls: list[UUID] = []

    async def is_admin(self, user_id: UUID) -> bool:
        self.calls.append(user_id)
        return self.is_admin_result


@pytest.mark.asyncio
@pytest.mark.parametrize("is_admin", [True, False])
async def test_app_paper_session_access_uses_authenticated_user_and_admin_service(
    is_admin: bool,
) -> None:
    user_id = uuid4()
    admin_service = StubAdminService(is_admin=is_admin)

    access = await get_app_paper_session_read_access(user_id, admin_service)  # type: ignore[arg-type]

    assert access == AppPaperSessionReadAccess(
        user_id=user_id,
        is_project_owner_reader=is_admin,
    )
    assert admin_service.calls == [user_id]


@pytest.mark.asyncio
async def test_require_app_paper_session_reader_accepts_project_owner() -> None:
    user_id = uuid4()

    result = await require_app_paper_session_reader(
        AppPaperSessionReadAccess(user_id=user_id, is_project_owner_reader=True)
    )

    assert result == user_id


@pytest.mark.asyncio
async def test_require_app_paper_session_reader_rejects_non_admin_before_session_lookup() -> None:
    user_id = uuid4()
    access = AppPaperSessionReadAccess(user_id=user_id, is_project_owner_reader=False)

    with pytest.raises(HTTPException) as error:
        await require_app_paper_session_reader(access)

    assert error.value.status_code == status.HTTP_403_FORBIDDEN


def test_policy_preserves_401_from_authenticated_user_without_repository_read() -> None:
    application = FastAPI()
    admin_service = StubAdminService(is_admin=True)

    @application.get("/policy")
    async def policy(
        _access: AppPaperSessionReadAccess = Depends(get_app_paper_session_read_access),
    ) -> dict[str, bool]:
        return {"allowed": True}

    async def unauthenticated() -> UUID:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    application.dependency_overrides[get_authenticated_user] = unauthenticated
    application.dependency_overrides[get_admin_service] = lambda: admin_service

    with TestClient(application) as client:
        response = client.get("/policy")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert admin_service.calls == []


def test_session_scoped_policy_denies_existing_and_missing_ids_identically() -> None:
    application = FastAPI()
    user_id = uuid4()

    @application.get("/future-session/{session_id}")
    async def future_session(
        session_id: str,
        _reader_id: UUID = Depends(require_app_paper_session_reader),
    ) -> dict[str, str]:
        pytest.fail(f"session lookup must not run for {session_id}")

    application.dependency_overrides[get_app_paper_session_read_access] = lambda: (
        AppPaperSessionReadAccess(
            user_id=user_id,
            is_project_owner_reader=False,
        )
    )

    with TestClient(application) as client:
        existing = client.get(f"/future-session/{'a' * 64}")
        missing = client.get(f"/future-session/{'b' * 64}")

    assert existing.status_code == status.HTTP_403_FORBIDDEN
    assert missing.status_code == status.HTTP_403_FORBIDDEN
    assert existing.json() == missing.json()


def test_policy_module_has_no_paper_repository_dependency() -> None:
    dependencies = {
        get_app_paper_session_read_access.__module__,
        require_app_paper_session_reader.__module__,
    }
    assert dependencies == {"app.api.dependencies.auth"}


@pytest.mark.asyncio
async def test_policy_does_not_read_paper_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_repository_read(*_args: object, **_kwargs: object) -> None:
        pytest.fail("paper repository must not be read while evaluating access")

    for method_name in (
        "list_session_configs_page",
        "list_session_ids",
        "load_config",
        "load_state",
    ):
        monkeypatch.setattr(
            PaperTradingRepository,
            method_name,
            forbidden_repository_read,
        )

    user_id = uuid4()
    admin_service = StubAdminService(is_admin=False)

    access = await get_app_paper_session_read_access(user_id, admin_service)  # type: ignore[arg-type]

    assert access == AppPaperSessionReadAccess(
        user_id=user_id,
        is_project_owner_reader=False,
    )
