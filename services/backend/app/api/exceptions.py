"""Safe, stable HTTP error handling for the ADT API."""

import logging
from collections.abc import Mapping
from typing import Final

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter, ValidationError
from starlette.exceptions import HTTPException

from app.api.schemas.common import JsonValue
from app.api.schemas.errors import ErrorDetail, ErrorPayload, ErrorResponse
from app.auth import AuthenticationError, JWKSUnavailableError
from app.core.request_id import REQUEST_ID_HEADER, get_request_id
from app.domain.errors import DomainError

logger = logging.getLogger(__name__)

_VALIDATION_STATUS_CODE: Final[int] = 422
_JSON_VALUE_ADAPTER: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)
_HTTP_ERRORS: Final[Mapping[int, tuple[str, str]]] = {
    status.HTTP_400_BAD_REQUEST: ("bad_request", "The request could not be processed."),
    status.HTTP_401_UNAUTHORIZED: (
        "authentication_required",
        "Valid authentication is required.",
    ),
    status.HTTP_403_FORBIDDEN: ("forbidden", "You are not allowed to perform this action."),
    status.HTTP_404_NOT_FOUND: ("not_found", "The requested resource was not found."),
    status.HTTP_409_CONFLICT: (
        "conflict",
        "The request conflicts with the current resource state.",
    ),
    _VALIDATION_STATUS_CODE: (
        "validation_error",
        "Request validation failed.",
    ),
    status.HTTP_503_SERVICE_UNAVAILABLE: (
        "service_unavailable",
        "The service is temporarily unavailable.",
    ),
}
_VALIDATION_MESSAGES: Final[Mapping[str, str]] = {
    "missing": "Field is required.",
    "extra_forbidden": "Unexpected field.",
    "enum": "Value is not allowed.",
    "finite_number": "Value must be finite.",
    "greater_than": "Value is too small.",
    "greater_than_equal": "Value is too small.",
    "less_than": "Value is too large.",
    "less_than_equal": "Value is too large.",
    "string_too_short": "Text is too short.",
    "string_too_long": "Text is too long.",
    "uuid_parsing": "Value must be a valid UUID.",
}


def _response(
    *,
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: JsonValue | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = get_request_id(request)
    payload = ErrorResponse(
        error=ErrorPayload(code=code, message=message, details=details),
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json", exclude_none=True),
        headers={**(headers or {}), REQUEST_ID_HEADER: request_id},
    )


def _safe_json_details(details: object) -> JsonValue | None:
    """Keep only JSON-compatible domain details; never stringify arbitrary objects."""

    if details is None:
        return None
    try:
        return _JSON_VALUE_ADAPTER.validate_python(details)
    except ValidationError:
        logger.warning("Discarded non-JSON-compatible domain error details")
        return None


def _validation_details(exc: RequestValidationError) -> list[JsonValue]:
    """Project Pydantic errors without their input, context, or documentation URL."""

    details: list[JsonValue] = []
    for error in exc.errors():
        error_type = str(error.get("type", "invalid"))
        location = ".".join(str(part) for part in error.get("loc", ()))
        detail = ErrorDetail(
            code=error_type,
            message=_VALIDATION_MESSAGES.get(error_type, "Invalid value."),
            field=location or None,
        )
        details.append(detail.model_dump(mode="json", exclude_none=True))
    return details


async def domain_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Translate a safe domain exception without exposing its original cause."""

    if not isinstance(exc, DomainError):
        raise exc

    logger.info(
        "Domain request failure",
        extra={
            "request_id": get_request_id(request),
            "error_code": exc.code,
            "http_status": exc.status_code,
            "method": request.method,
            "path": request.url.path,
        },
    )
    return _response(
        request=request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=_safe_json_details(exc.details),
    )


async def authentication_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Return a safe authentication failure while preserving stable codes."""

    if not isinstance(exc, AuthenticationError):
        raise exc

    unavailable = isinstance(exc, JWKSUnavailableError)
    status_code = (
        status.HTTP_503_SERVICE_UNAVAILABLE if unavailable else status.HTTP_401_UNAUTHORIZED
    )
    logger.info(
        "Authentication request rejected",
        extra={
            "request_id": get_request_id(request),
            "error_code": exc.code,
            "http_status": status_code,
            "method": request.method,
            "path": request.url.path,
        },
    )
    return _response(
        request=request,
        status_code=status_code,
        code=exc.code,
        message=exc.message,
        headers=None if unavailable else {"WWW-Authenticate": "Bearer"},
    )


async def validation_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Return sanitized validation diagnostics without echoing request values."""

    if not isinstance(exc, RequestValidationError):
        raise exc

    logger.info(
        "Request validation failed",
        extra={
            "request_id": get_request_id(request),
            "method": request.method,
            "path": request.url.path,
        },
    )
    return _response(
        request=request,
        status_code=_VALIDATION_STATUS_CODE,
        code="validation_error",
        message="Request validation failed.",
        details=_validation_details(exc),
    )


async def http_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Normalize framework HTTP errors without trusting arbitrary ``detail`` text."""

    if not isinstance(exc, HTTPException):
        raise exc

    code, message = _HTTP_ERRORS.get(
        exc.status_code,
        ("http_error", "The request could not be completed."),
    )
    logger.info(
        "HTTP request rejected",
        extra={
            "request_id": get_request_id(request),
            "error_code": code,
            "http_status": exc.status_code,
            "method": request.method,
            "path": request.url.path,
        },
    )
    return _response(
        request=request,
        status_code=exc.status_code,
        code=code,
        message=message,
        headers=exc.headers,
    )


async def general_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Hide unexpected exception details and tracebacks from the client."""

    logger.error(
        "Unhandled exception while processing request",
        extra={
            "request_id": get_request_id(request),
            "exception_type": type(exc).__name__,
            "method": request.method,
            "path": request.url.path,
        },
    )
    return _response(
        request=request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="internal_error",
        message="An internal server error occurred.",
    )


def setup_exception_handlers(app: FastAPI) -> None:
    """Register exception handlers from most specific to most general."""

    app.add_exception_handler(AuthenticationError, authentication_exception_handler)
    app.add_exception_handler(DomainError, domain_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)
