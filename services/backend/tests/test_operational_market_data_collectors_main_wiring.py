"""Gate 3B application wiring for operational market-data collector control."""

from __future__ import annotations

from uuid import uuid4

import pytest

import app.operational_market_data_collectors as collectors
from app.api.dependencies.resources import (
    get_operational_market_data_collector_service,
)
from app.api.routes import admin_operational_market_data_collectors
from app.database import Database
from app.main import app
from app.services.operational_market_data_collectors import (
    OperationalMarketDataCollectorService,
)

_PREFIX = "/api/v1/admin/operational-market-data-collectors"


def test_main_includes_collector_router_exactly_once() -> None:
    """FastAPI >=0.137 retains included routers as lazy route-tree nodes."""

    original_routers = [getattr(route, "original_router", None) for route in app.routes]

    assert (
        sum(
            candidate is admin_operational_market_data_collectors.router
            for candidate in original_routers
        )
        == 1
    )


def test_full_application_openapi_contains_six_collector_operations() -> None:
    document = app.openapi()

    expected = {
        (
            f"{_PREFIX}/start",
            "post",
        ),
        (
            f"{_PREFIX}/{{epoch_id}}",
            "get",
        ),
        (
            f"{_PREFIX}/{{epoch_id}}/commands",
            "get",
        ),
        (
            f"{_PREFIX}/{{epoch_id}}/pause",
            "post",
        ),
        (
            f"{_PREFIX}/{{epoch_id}}/resume",
            "post",
        ),
        (
            f"{_PREFIX}/{{epoch_id}}/stop",
            "post",
        ),
    }

    found = {
        (
            path,
            method,
        )
        for path, path_item in document["paths"].items()
        if path.startswith(_PREFIX)
        for method in path_item
        if method
        in {
            "get",
            "post",
            "put",
            "patch",
            "delete",
        }
    }

    assert found == expected


@pytest.mark.asyncio
async def test_resource_getter_builds_real_postgres_control_service(
    database: Database,
) -> None:
    service = get_operational_market_data_collector_service(database)

    assert isinstance(
        service,
        OperationalMarketDataCollectorService,
    )

    with pytest.raises(collectors.OperationalMarketDataCollectorNotFoundError):
        await service.get(uuid4())


def test_main_does_not_host_collector_execution_runtime() -> None:
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app/main.py").read_text(encoding="utf-8")

    assert "admin_operational_market_data_collectors" in source

    for forbidden in (
        "OperationalMarketDataCollectorWorker",
        "OperationalMarketDataCollectorSupervisor",
        "OperationalMarketDataCollectorRuntimeExecutor",
        "ContinuousCollectionRunner",
    ):
        assert forbidden not in source


def test_openapi_collector_operations_remain_administrator_contracts() -> None:
    document = app.openapi()

    for path, method in (
        (
            f"{_PREFIX}/start",
            "post",
        ),
        (
            f"{_PREFIX}/{{epoch_id}}",
            "get",
        ),
        (
            f"{_PREFIX}/{{epoch_id}}/commands",
            "get",
        ),
        (
            f"{_PREFIX}/{{epoch_id}}/pause",
            "post",
        ),
        (
            f"{_PREFIX}/{{epoch_id}}/resume",
            "post",
        ),
        (
            f"{_PREFIX}/{{epoch_id}}/stop",
            "post",
        ),
    ):
        operation = document["paths"][path][method]

        responses = operation["responses"]

        assert {
            "400",
            "401",
            "403",
            "404",
            "409",
            "413",
            "422",
            "500",
            "503",
        } <= set(responses)


def test_wiring_does_not_publish_execution_host_in_app_state() -> None:
    source = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app/main.py").read_text(
        encoding="utf-8"
    )

    forbidden_state = (
        "application.state.operational_market_data_collector_worker",
        "application.state.operational_market_data_collector_supervisor",
        "application.state.operational_market_data_collector_runtime",
    )

    for token in forbidden_state:
        assert token not in source
