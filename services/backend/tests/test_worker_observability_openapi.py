"""OpenAPI and wiring contract tests for worker observability."""

from __future__ import annotations

import json
from typing import cast

from app.api.dependencies.resources import (
    get_worker_runtime_observability_service,
)
from app.api.routes import admin_worker_observability
from app.database import Database
from app.main import create_app
from app.repositories import (
    PostgresWorkerRuntimeObservabilityRepository,
)
from app.services.worker_observability import (
    WorkerRuntimeObservabilityService,
)

PREFIX = "/api/v1/admin/market-data/worker-observability"

RUNTIME_PATH = f"{PREFIX}/runtimes"
EVENT_PATH = f"{PREFIX}/events"

HTTP_METHOD_KEYS = frozenset(
    {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "options",
        "head",
        "trace",
    }
)


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def test_openapi_exposes_exactly_two_get_only_observability_paths() -> None:
    application = create_app()
    schema = application.openapi()

    paths = _mapping(schema["paths"])

    observability_paths = {
        path: _mapping(item) for path, item in paths.items() if path.startswith(PREFIX)
    }

    assert set(observability_paths) == {
        RUNTIME_PATH,
        EVENT_PATH,
    }

    for path_item in observability_paths.values():
        methods = set(path_item) & HTTP_METHOD_KEYS
        assert methods == {"get"}


def test_fastapi_router_has_no_worker_control_method() -> None:
    inventory: set[tuple[str, frozenset[str]]] = set()

    for route in admin_worker_observability.router.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)

        assert isinstance(path, str)
        assert methods is not None

        inventory.add(
            (
                path,
                frozenset(str(method) for method in methods),
            )
        )

    assert inventory == {
        (
            RUNTIME_PATH,
            frozenset({"GET"}),
        ),
        (
            EVENT_PATH,
            frozenset({"GET"}),
        ),
    }


def test_worker_observability_openapi_never_exposes_runtime_id() -> None:
    application = create_app()
    schema = application.openapi()

    components = _mapping(schema["components"])
    schemas = _mapping(components["schemas"])

    exposed_schema_names = (
        "WorkerRuntimeResponse",
        "WorkerRuntimeListResponse",
        "WorkerRuntimeEventResponse",
        "WorkerRuntimeEventListResponse",
    )

    exposed = {name: schemas[name] for name in exposed_schema_names}

    serialized = json.dumps(
        exposed,
        sort_keys=True,
    )

    assert "runtime_id" not in serialized

    runtime_schema = _mapping(schemas["WorkerRuntimeResponse"])
    runtime_properties = _mapping(runtime_schema["properties"])

    assert set(runtime_properties) == {
        "health_state",
        "lifecycle_state",
        "activity_state",
        "started_at",
        "heartbeat_at",
        "stopped_at",
        "failure_code",
    }

    event_schema = _mapping(schemas["WorkerRuntimeEventResponse"])
    event_properties = _mapping(event_schema["properties"])

    assert set(event_properties) == {
        "event_id",
        "event_type",
        "occurred_at",
        "operation_id",
        "operation_state",
    }


def test_http_dependency_wires_only_repository_and_read_service() -> None:
    database = Database("postgresql://adt_test@127.0.0.1:1/adt_test")

    service = get_worker_runtime_observability_service(database)

    assert isinstance(
        service,
        WorkerRuntimeObservabilityService,
    )
    assert isinstance(
        service._repository,
        PostgresWorkerRuntimeObservabilityRepository,
    )
