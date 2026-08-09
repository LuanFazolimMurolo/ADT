"""Authenticated minimal paper-session catalog HTTP tests."""

from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from app.api.dependencies.auth import (
    AppPaperSessionReadAccess,
    get_app_paper_session_read_access,
    get_authenticated_user,
    require_administrator,
)
from app.api.dependencies.resources import (
    get_admin_service,
    get_paper_trading_read_service,
)
from app.api.routes import admin_paper_dashboard, app_paper_sessions
from app.main import app
from app.paper_trading.domain import paper_session_id
from app.paper_trading.query import PaperSessionPage, PaperSessionSummaryView
from tests.test_paper_trading import _config


def catalog_page(*, page: int, page_size: int) -> PaperSessionPage:
    configs = (
        _config(),
        replace(_config(), initial_capital=Decimal("2000")),
    )
    items = tuple(
        sorted(
            (PaperSessionSummaryView(config=config, summary=None) for config in configs),
            key=lambda item: item.session_id,
        )
    )
    start = (page - 1) * page_size
    selected = items[start : start + page_size]
    return PaperSessionPage(
        items=selected,
        page=page,
        page_size=page_size,
        total=len(items),
        total_pages=(len(items) + page_size - 1) // page_size,
    )


class StubReadService:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def list_sessions(self, *, page: int, page_size: int) -> PaperSessionPage:
        self.calls.append((page, page_size))
        return catalog_page(page=page, page_size=page_size)


class ForbiddenReadService:
    def list_sessions(self, *, page: int, page_size: int) -> PaperSessionPage:
        pytest.fail(f"non-admin enumerated paper sessions: page={page}, page_size={page_size}")


def catalog_client(
    *,
    is_project_owner_reader: bool,
    service: object,
) -> TestClient:
    application = FastAPI()
    application.include_router(app_paper_sessions.router)
    application.dependency_overrides[get_app_paper_session_read_access] = lambda: (
        AppPaperSessionReadAccess(
            user_id=uuid4(),
            is_project_owner_reader=is_project_owner_reader,
        )
    )
    application.dependency_overrides[get_paper_trading_read_service] = lambda: service
    return TestClient(application)


def test_non_admin_receives_empty_catalog_without_calling_read_service() -> None:
    with catalog_client(
        is_project_owner_reader=False,
        service=ForbiddenReadService(),
    ) as client:
        response = client.get(
            "/api/v1/app/paper-trading/sessions",
            params={"page": 7, "page_size": 100},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "items": [],
        "page": 7,
        "page_size": 100,
        "total": 0,
        "total_pages": 0,
    }


def test_project_owner_receives_minimal_projection_in_service_order() -> None:
    service = StubReadService()
    with catalog_client(is_project_owner_reader=True, service=service) as client:
        response = client.get(
            "/api/v1/app/paper-trading/sessions",
            params={"page": 1, "page_size": 20},
        )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert service.calls == [(1, 20)]
    assert payload["page"] == 1
    assert payload["page_size"] == 20
    assert payload["total"] == 2
    assert payload["total_pages"] == 1
    assert [item["session_id"] for item in payload["items"]] == [
        item.session_id for item in catalog_page(page=1, page_size=20).items
    ]
    assert set(payload["items"][0]) == {
        "session_id",
        "base_asset",
        "quote_asset",
        "timeframe",
        "strategy_name",
        "strategy_version",
    }
    assert payload["items"][0]["base_asset"] == "BTC"
    assert payload["items"][0]["quote_asset"] == "USDT"
    assert payload["items"][0]["timeframe"] == "1m"
    assert payload["items"][0]["strategy_name"] == "paper-buy-test"
    assert payload["items"][0]["strategy_version"] == "1"
    forbidden = {
        "strategy_parameters",
        "initial_capital",
        "portfolio",
        "equity",
        "pnl",
        "runner",
        "orders",
        "fills",
    }
    assert forbidden.isdisjoint(payload["items"][0])
    assert "quantity" not in response.text


def test_catalog_propagates_bounded_pagination_and_allows_empty_late_page() -> None:
    service = StubReadService()
    with catalog_client(is_project_owner_reader=True, service=service) as client:
        maximum = client.get(
            "/api/v1/app/paper-trading/sessions",
            params={"page": 1, "page_size": 100},
        )
        late = client.get(
            "/api/v1/app/paper-trading/sessions",
            params={"page": 50, "page_size": 20},
        )

    assert maximum.status_code == status.HTTP_200_OK
    assert maximum.json()["page_size"] == 100
    assert late.status_code == status.HTTP_200_OK
    assert late.json()["items"] == []
    assert late.json()["page"] == 50
    assert service.calls == [(1, 100), (50, 20)]


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0, "page_size": 20},
        {"page": 1, "page_size": 101},
    ],
)
def test_catalog_rejects_invalid_pagination(params: dict[str, int]) -> None:
    service = StubReadService()
    with catalog_client(is_project_owner_reader=True, service=service) as client:
        response = client.get("/api/v1/app/paper-trading/sessions", params=params)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert service.calls == []


def test_catalog_preserves_authentication_failure() -> None:
    application = FastAPI()
    application.include_router(app_paper_sessions.router)

    async def unauthenticated() -> UUID:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    application.dependency_overrides[get_authenticated_user] = unauthenticated
    application.dependency_overrides[get_admin_service] = lambda: pytest.fail(
        "authorization lookup must not run without authentication"
    )
    application.dependency_overrides[get_paper_trading_read_service] = ForbiddenReadService

    with TestClient(application) as client:
        response = client.get("/api/v1/app/paper-trading/sessions")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_catalog_router_is_get_only() -> None:
    routes = tuple(app_paper_sessions.router.routes)
    assert len(routes) == 1
    assert routes[0].path == "/api/v1/app/paper-trading/sessions"
    assert routes[0].methods == {"GET"}


def test_catalog_openapi_is_authenticated_minimal_and_not_administrative() -> None:
    contract = app.openapi()
    operation = contract["paths"]["/api/v1/app/paper-trading/sessions"]["get"]
    assert {"200", "401", "422", "500", "503"}.issubset(operation["responses"])
    assert "403" not in operation["responses"]

    parameters = {item["name"]: item for item in operation["parameters"]}
    assert parameters["page"]["schema"]["default"] == 1
    assert parameters["page"]["schema"]["minimum"] == 1
    assert parameters["page_size"]["schema"]["default"] == 20
    assert parameters["page_size"]["schema"]["maximum"] == 100

    item_properties = set(
        contract["components"]["schemas"]["AppPaperSessionCatalogItemResponse"]["properties"]
    )
    assert item_properties == {
        "session_id",
        "base_asset",
        "quote_asset",
        "timeframe",
        "strategy_name",
        "strategy_version",
    }


def test_administrative_dashboard_still_requires_administrator() -> None:
    application = FastAPI()
    application.include_router(admin_paper_dashboard.router)

    async def reject_administrator() -> UUID:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    application.dependency_overrides[require_administrator] = reject_administrator

    with TestClient(application) as client:
        response = client.get("/api/v1/admin/paper-trading/dashboard")

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_catalog_session_ids_are_deterministic_config_identities() -> None:
    domain_page = catalog_page(page=1, page_size=20)
    assert tuple(item.session_id for item in domain_page.items) == tuple(
        sorted(paper_session_id(item.config) for item in domain_page.items)
    )
