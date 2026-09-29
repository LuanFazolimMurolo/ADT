"""Global OpenAPI wiring tests for PAPER recommendation approvals."""

from __future__ import annotations

from typing import cast

from app.api.routes import admin_paper_recommendation_approvals
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

_CREATE_PATH = "/api/v1/admin/ml/recommendations/{recommendation_id}/paper-approvals"
_DETAIL_PATH = "/api/v1/admin/ml/paper-recommendation-approvals/{approval_id}"
_REVOKE_PATH = "/api/v1/admin/ml/paper-recommendation-approvals/{approval_id}/revoke"

_EXISTING_COLLECTION_PATH = "/api/v1/admin/ml/recommendations"
_EXISTING_DETAIL_PATH = "/api/v1/admin/ml/recommendations/{recommendation_id}"


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def test_global_openapi_exposes_exact_approval_paths() -> None:
    application = create_app()
    schema = application.openapi()

    paths = _mapping(schema["paths"])

    approval_paths = {
        path: _mapping(item)
        for path, item in paths.items()
        if ("/paper-approvals" in path or "/paper-recommendation-approvals" in path)
    }

    assert set(approval_paths) == {
        _CREATE_PATH,
        _DETAIL_PATH,
        _REVOKE_PATH,
    }

    assert (set(approval_paths[_CREATE_PATH]) & _HTTP_METHOD_KEYS) == {"post"}

    assert (set(approval_paths[_DETAIL_PATH]) & _HTTP_METHOD_KEYS) == {"get"}

    assert (set(approval_paths[_REVOKE_PATH]) & _HTTP_METHOD_KEYS) == {"post"}


def test_global_openapi_preserves_existing_ml_recommendation_reads() -> None:
    application = create_app()
    schema = application.openapi()

    paths = _mapping(schema["paths"])

    collection = _mapping(paths[_EXISTING_COLLECTION_PATH])
    detail = _mapping(paths[_EXISTING_DETAIL_PATH])

    assert (set(collection) & _HTTP_METHOD_KEYS) == {"get"}
    assert (set(detail) & _HTTP_METHOD_KEYS) == {"get"}


def test_global_openapi_preserves_admin_ml_tags() -> None:
    application = create_app()
    schema = application.openapi()

    paths = _mapping(schema["paths"])

    create = _mapping(paths[_CREATE_PATH])
    detail = _mapping(paths[_DETAIL_PATH])
    revoke = _mapping(paths[_REVOKE_PATH])

    assert _mapping(create["post"])["tags"] == ["admin ml"]
    assert _mapping(detail["get"])["tags"] == ["admin ml"]
    assert _mapping(revoke["post"])["tags"] == ["admin ml"]


def test_fastapi_router_has_exact_approval_surface() -> None:
    inventory: set[tuple[str, frozenset[str]]] = set()

    for route in admin_paper_recommendation_approvals.router.routes:
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
            _CREATE_PATH,
            frozenset({"POST"}),
        ),
        (
            _DETAIL_PATH,
            frozenset({"GET"}),
        ),
        (
            _REVOKE_PATH,
            frozenset({"POST"}),
        ),
    }


def test_approval_surface_has_no_delete_put_or_patch() -> None:
    application = create_app()
    schema = application.openapi()

    paths = _mapping(schema["paths"])

    for path in (
        _CREATE_PATH,
        _DETAIL_PATH,
        _REVOKE_PATH,
    ):
        path_item = _mapping(paths[path])
        methods = set(path_item) & _HTTP_METHOD_KEYS

        assert methods.isdisjoint(
            {
                "delete",
                "put",
                "patch",
            }
        )


def test_create_contract_accepts_only_human_decision_input() -> None:
    application = create_app()
    schema = application.openapi()

    paths = _mapping(schema["paths"])
    create = _mapping(paths[_CREATE_PATH])
    operation = _mapping(create["post"])

    request_body = _mapping(operation["requestBody"])
    content = _mapping(request_body["content"])
    json_content = _mapping(content["application/json"])
    body_schema = _mapping(json_content["schema"])

    reference = body_schema["$ref"]
    assert isinstance(reference, str)

    schema_name = reference.rsplit("/", 1)[-1]

    components = _mapping(schema["components"])
    schemas = _mapping(components["schemas"])
    request_schema = _mapping(schemas[schema_name])

    properties = _mapping(request_schema["properties"])

    assert set(properties) == {"idempotency_key"}
    assert request_schema["additionalProperties"] is False
