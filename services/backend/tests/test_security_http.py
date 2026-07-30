"""HTTP security, correlation and OpenAPI contract tests."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest
from pydantic import AnyHttpUrl, SecretStr

from app.core.config import Settings
from app.core.logging import JsonLogFormatter
from app.main import create_app

ALLOWED_ORIGIN = "http://localhost:5173"


def _settings(*, production: bool = False) -> Settings:
    return Settings(
        supabase_url=AnyHttpUrl("https://phase1d.example.invalid"),
        supabase_publishable_key=SecretStr("public-test-key"),
        supabase_database_url=SecretStr(
            "postgresql://phase1d@db.example.invalid:5432/adt"
            + ("?sslmode=require" if production else "")
        ),
        environment="production" if production else "test",
        log_level="WARNING",
        cors_origins=(["https://admin.example.invalid"] if production else [ALLOWED_ORIGIN]),
        api_host="127.0.0.1",
        api_port=8000,
    )


def _assert_security_headers(response: httpx.Response) -> None:
    UUID(response.headers["x-request-id"])
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["permissions-policy"]
    assert "default-src 'none'" in response.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_request_id_is_propagated_or_safely_replaced() -> None:
    application = create_app(_settings())
    transport = httpx.ASGITransport(app=application)
    valid_request_id = "123e4567-e89b-42d3-a456-426614174000"
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        valid = await client.get("/health", headers={"X-Request-ID": valid_request_id})
        invalid = await client.get("/health", headers={"X-Request-ID": "not-a-uuid"})

    assert valid.headers["x-request-id"] == valid_request_id
    assert invalid.headers["x-request-id"] != "not-a-uuid"
    UUID(invalid.headers["x-request-id"])
    _assert_security_headers(valid)
    _assert_security_headers(invalid)


@pytest.mark.asyncio
async def test_admin_responses_are_never_cacheable() -> None:
    application = create_app(_settings())
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/admin/not-a-route")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    _assert_security_headers(response)


@pytest.mark.asyncio
async def test_cors_is_explicit_for_origins_methods_and_headers() -> None:
    application = create_app(_settings())
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        allowed = await client.get("/health", headers={"Origin": ALLOWED_ORIGIN})
        denied = await client.get(
            "/health",
            headers={"Origin": "https://attacker.example.invalid"},
        )
        allowed_preflight = await client.options(
            "/api/v1/admin/simulations",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,x-request-id",
            },
        )
        denied_preflight = await client.options(
            "/api/v1/admin/simulations",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "DELETE",
            },
        )

    assert allowed.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "access-control-allow-origin" not in denied.headers
    assert allowed_preflight.status_code == 200
    assert allowed_preflight.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "POST" in allowed_preflight.headers["access-control-allow-methods"]
    assert denied_preflight.status_code == 400
    assert "access-control-allow-credentials" not in allowed.headers
    _assert_security_headers(allowed_preflight)
    _assert_security_headers(denied_preflight)


@pytest.mark.asyncio
async def test_unhandled_500_keeps_safe_body_cors_and_security_headers() -> None:
    application = create_app(_settings())
    secret = "postgresql://user:password@internal.example.invalid/adt"

    async def explode() -> None:
        raise RuntimeError(secret)

    application.add_api_route("/test/boom", explode, methods=["GET"])
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/test/boom", headers={"Origin": ALLOWED_ORIGIN})

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "An internal server error occurred.",
        }
    }
    assert secret not in response.text
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    _assert_security_headers(response)


@pytest.mark.asyncio
async def test_development_docs_have_compatible_csp() -> None:
    application = create_app(_settings())
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        docs = await client.get("/docs")
        api = await client.get("/health")

    assert docs.status_code == 200
    assert "https://cdn.jsdelivr.net" in docs.headers["content-security-policy"]
    assert "'unsafe-inline'" in docs.headers["content-security-policy"]
    assert "https://cdn.jsdelivr.net" not in api.headers["content-security-policy"]
    assert "'unsafe-inline'" not in api.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_production_disables_docs_and_enables_hsts() -> None:
    application = create_app(_settings(production=True))
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        docs = await client.get("/docs")
        openapi = await client.get("/openapi.json")
        health = await client.get("/health")

    assert docs.status_code == 404
    assert openapi.status_code == 404
    assert health.headers["strict-transport-security"] == ("max-age=31536000; includeSubDomains")


def test_openapi_matches_error_decimal_and_request_id_contracts() -> None:
    schema = create_app(_settings()).openapi()
    components = schema["components"]["schemas"]
    operation = schema["paths"]["/api/v1/admin/simulations"]["post"]

    assert "ErrorResponse" in components
    assert (
        operation["responses"]["422"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/ErrorResponse"
    )
    assert set(("400", "401", "403", "404", "409", "422", "500", "503")).issubset(
        operation["responses"]
    )
    assert all(
        response["headers"]["X-Request-ID"]["schema"]["format"] == "uuid"
        for response in operation["responses"].values()
    )
    assert (
        components["SimulationCreateRequest"]["properties"]["initial_capital"]["type"] == "string"
    )
    assert components["SystemStatus"]["properties"]["timestamp"]["format"] == "date-time"
    assert components["SystemStatus"]["properties"]["status"]["const"] == "operational"


@pytest.mark.asyncio
async def test_large_request_body_is_rejected_before_route_processing() -> None:
    application = create_app(_settings())
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/admin/simulations",
            content=b"x" * 1_048_577,
            headers={"Content-Type": "application/json", "Origin": ALLOWED_ORIGIN},
        )

    assert response.status_code == 413
    assert response.json()["error"] == {
        "code": "request_too_large",
        "message": "The request body is too large.",
    }
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    _assert_security_headers(response)


@pytest.mark.asyncio
async def test_streamed_body_is_limited_even_when_route_does_not_consume_it() -> None:
    application = create_app(_settings())
    route_was_called = False

    async def bodyless_route() -> dict[str, str]:
        nonlocal route_was_called
        route_was_called = True
        return {"status": "unexpected"}

    async def oversized_chunks() -> AsyncIterator[bytes]:
        yield b"x" * 700_000
        yield b"x" * 400_000

    application.add_api_route("/test/bodyless", bodyless_route, methods=["POST"])
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/test/bodyless",
            content=oversized_chunks(),
            headers={"Origin": ALLOWED_ORIGIN},
        )

    assert response.status_code == 413
    assert route_was_called is False
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    _assert_security_headers(response)


@pytest.mark.asyncio
async def test_request_frame_overhead_is_bounded_before_route_processing() -> None:
    application = create_app(_settings())
    route_was_called = False

    async def bodyless_route() -> dict[str, str]:
        nonlocal route_was_called
        route_was_called = True
        return {"status": "unexpected"}

    async def excessive_tiny_chunks() -> AsyncIterator[bytes]:
        for _ in range(1_025):
            yield b"x"

    application.add_api_route("/test/frame-limit", bodyless_route, methods=["POST"])
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/test/frame-limit",
            content=excessive_tiny_chunks(),
            headers={"Origin": ALLOWED_ORIGIN},
        )

    assert response.status_code == 413
    assert route_was_called is False
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    _assert_security_headers(response)


def test_json_formatter_ignores_unapproved_record_attributes() -> None:
    secret = "Bearer token-that-must-not-be-logged"
    record = logging.LogRecord(
        name="app.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Safe fixed message",
        args=(),
        exc_info=None,
    )
    record.request_id = "123e4567-e89b-42d3-a456-426614174000"
    record.authorization = secret
    record.exception_type = "RuntimeError"

    payload = JsonLogFormatter().format(record)
    parsed = json.loads(payload)

    assert secret not in payload
    assert parsed["message"] == "Safe fixed message"
    assert parsed["exception_type"] == "RuntimeError"
    assert "authorization" not in parsed
