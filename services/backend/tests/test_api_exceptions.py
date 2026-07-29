"""Unit tests for sanitized API error responses."""

from __future__ import annotations

import json

import pytest
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from app.api.exceptions import (
    domain_exception_handler,
    general_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.domain.errors import SimulationNotFoundError


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/admin/simulations",
            "headers": [],
        },
    )


def _body(response_body: bytes | memoryview[int]) -> dict[str, object]:
    value = json.loads(bytes(response_body))
    assert isinstance(value, dict)
    return value


@pytest.mark.asyncio
async def test_domain_error_uses_stable_envelope() -> None:
    response = await domain_exception_handler(
        _request(),
        SimulationNotFoundError(details={"resource": "simulation"}),
    )

    assert response.status_code == 404
    assert _body(response.body) == {
        "error": {
            "code": "simulation_not_found",
            "message": "A simulação solicitada não foi encontrada.",
            "details": {"resource": "simulation"},
        },
    }


@pytest.mark.asyncio
async def test_validation_error_does_not_echo_input_or_validator_context() -> None:
    secret = "do-not-return-this-password"
    error = RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": ("body", "password"),
                "msg": f"Value error, {secret}",
                "input": secret,
                "ctx": {"error": ValueError(secret)},
            },
        ],
    )

    response = await validation_exception_handler(_request(), error)
    serialized = bytes(response.body).decode()

    assert response.status_code == 422
    assert secret not in serialized
    assert _body(response.body) == {
        "error": {
            "code": "validation_error",
            "message": "Request validation failed.",
            "details": [
                {
                    "code": "value_error",
                    "message": "Invalid value.",
                    "field": "body.password",
                },
            ],
        },
    }


@pytest.mark.asyncio
async def test_http_error_does_not_echo_arbitrary_detail() -> None:
    secret = "Bearer secret-token"
    response = await http_exception_handler(
        _request(),
        HTTPException(status_code=401, detail=secret, headers={"WWW-Authenticate": "Bearer"}),
    )

    assert response.status_code == 401
    assert secret not in bytes(response.body).decode()
    assert response.headers["www-authenticate"] == "Bearer"
    assert _body(response.body)["error"] == {
        "code": "authentication_required",
        "message": "Valid authentication is required.",
    }


@pytest.mark.asyncio
async def test_unhandled_error_hides_internal_exception() -> None:
    secret = "database-credential-that-must-not-leak"
    response = await general_exception_handler(_request(), RuntimeError(secret))

    assert response.status_code == 500
    assert secret not in bytes(response.body).decode()
    assert _body(response.body)["error"] == {
        "code": "internal_error",
        "message": "An internal server error occurred.",
    }
