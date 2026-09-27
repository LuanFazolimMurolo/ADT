"""Global OpenAPI wiring tests for Phase 8-17 recommendation reads."""

from __future__ import annotations

from typing import cast

from app.api.routes import admin_ml_recommendations
from app.main import create_app

_HTTP_METHOD_KEYS = frozenset(
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

_COLLECTION_PATH = "/api/v1/admin/ml/recommendations"
_DETAIL_PATH = "/api/v1/admin/ml/recommendations/{recommendation_id}"


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def test_global_openapi_exposes_exact_ml_recommendation_paths() -> None:
    application = create_app()
    schema = application.openapi()

    paths = _mapping(schema["paths"])

    recommendation_paths = {
        path: _mapping(item)
        for path, item in paths.items()
        if path.startswith("/api/v1/admin/ml/recommendations")
    }

    assert set(recommendation_paths) == {
        _COLLECTION_PATH,
        _DETAIL_PATH,
    }

    for path_item in recommendation_paths.values():
        methods = set(path_item) & _HTTP_METHOD_KEYS
        assert methods == {"get"}


def test_global_openapi_preserves_admin_ml_tags() -> None:
    application = create_app()
    schema = application.openapi()

    paths = _mapping(schema["paths"])

    collection = _mapping(paths[_COLLECTION_PATH])
    detail = _mapping(paths[_DETAIL_PATH])

    collection_get = _mapping(collection["get"])
    detail_get = _mapping(detail["get"])

    assert collection_get["tags"] == ["admin ml"]
    assert detail_get["tags"] == ["admin ml"]


def test_fastapi_router_has_exact_get_only_recommendation_surface() -> None:
    inventory: set[tuple[str, frozenset[str]]] = set()

    for route in admin_ml_recommendations.router.routes:
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
            _COLLECTION_PATH,
            frozenset({"GET"}),
        ),
        (
            _DETAIL_PATH,
            frozenset({"GET"}),
        ),
    }


def test_global_openapi_exposes_bounded_limit_contract() -> None:
    application = create_app()
    schema = application.openapi()

    paths = _mapping(schema["paths"])
    collection = _mapping(paths[_COLLECTION_PATH])
    operation = _mapping(collection["get"])

    parameters = operation["parameters"]
    assert isinstance(parameters, list)

    parameter_map = {
        item["name"]: item
        for item in parameters
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }

    limit = _mapping(parameter_map["limit"])
    limit_schema = _mapping(limit["schema"])

    assert limit_schema["default"] == 50
    assert limit_schema["minimum"] == 1
    assert limit_schema["maximum"] == 100
