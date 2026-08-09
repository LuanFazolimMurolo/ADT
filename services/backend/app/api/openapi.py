"""Reusable OpenAPI metadata for the stable HTTP error boundary."""

from typing import Any, Final

from fastapi import status

from app.api.schemas.errors import ErrorResponse


def _error_response(description: str) -> dict[str, Any]:
    return {
        "model": ErrorResponse,
        "description": description,
    }


AUTHENTICATED_ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_401_UNAUTHORIZED: _error_response("Authentication is missing or invalid."),
    status.HTTP_500_INTERNAL_SERVER_ERROR: _error_response(
        "An unexpected authenticated request failure was safely normalized."
    ),
    status.HTTP_503_SERVICE_UNAVAILABLE: _error_response(
        "A required authorization service is temporarily unavailable."
    ),
}


AUTHENTICATED_QUERY_ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    **AUTHENTICATED_ERROR_RESPONSES,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _error_response(
        "The request does not satisfy the declared contract."
    ),
}


AUTHENTICATED_MARKET_ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    **AUTHENTICATED_ERROR_RESPONSES,
    status.HTTP_400_BAD_REQUEST: _error_response("The market-data request is invalid."),
    status.HTTP_404_NOT_FOUND: _error_response(
        "The requested local market-data dataset does not exist."
    ),
    status.HTTP_409_CONFLICT: _error_response(
        "The requested local market-data state is not available."
    ),
    status.HTTP_422_UNPROCESSABLE_CONTENT: _error_response(
        "The request does not satisfy the declared contract."
    ),
    status.HTTP_503_SERVICE_UNAVAILABLE: _error_response(
        "A required authentication or local market-data service is temporarily unavailable."
    ),
}


APP_PAPER_SESSION_ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_401_UNAUTHORIZED: _error_response("Authentication is missing or invalid."),
    status.HTTP_403_FORBIDDEN: _error_response(
        "The authenticated user cannot read paper sessions."
    ),
    status.HTTP_404_NOT_FOUND: _error_response("The authorized paper session does not exist."),
    status.HTTP_409_CONFLICT: _error_response(
        "The authorized paper-session state is not currently available."
    ),
    status.HTTP_422_UNPROCESSABLE_CONTENT: _error_response(
        "The request does not satisfy the declared contract."
    ),
    status.HTTP_500_INTERNAL_SERVER_ERROR: _error_response(
        "An unexpected paper-session read failure was safely normalized."
    ),
    status.HTTP_503_SERVICE_UNAVAILABLE: _error_response(
        "A required authentication or local paper-session service is unavailable."
    ),
}


ADMIN_ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_400_BAD_REQUEST: _error_response("Malformed administrative request."),
    status.HTTP_401_UNAUTHORIZED: _error_response("Authentication is missing or invalid."),
    status.HTTP_403_FORBIDDEN: _error_response("The authenticated user is not an administrator."),
    status.HTTP_404_NOT_FOUND: _error_response("The requested resource does not exist."),
    status.HTTP_409_CONFLICT: _error_response("The requested state change is not allowed."),
    status.HTTP_413_CONTENT_TOO_LARGE: _error_response(
        "The request body exceeds the application limit."
    ),
    status.HTTP_422_UNPROCESSABLE_CONTENT: _error_response(
        "The request does not satisfy the declared contract."
    ),
    status.HTTP_500_INTERNAL_SERVER_ERROR: _error_response(
        "An unexpected failure was safely normalized."
    ),
    status.HTTP_503_SERVICE_UNAVAILABLE: _error_response(
        "A required service is temporarily unavailable."
    ),
}

PUBLIC_ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_500_INTERNAL_SERVER_ERROR: _error_response(
        "An unexpected failure was safely normalized."
    ),
    status.HTTP_503_SERVICE_UNAVAILABLE: _error_response(
        "The database is temporarily unavailable."
    ),
}


MARKET_ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_400_BAD_REQUEST: _error_response("The market request is invalid."),
    status.HTTP_404_NOT_FOUND: _error_response("The requested market instrument does not exist."),
    status.HTTP_409_CONFLICT: _error_response("The market instrument is not currently tradable."),
    status.HTTP_429_TOO_MANY_REQUESTS: _error_response(
        "The upstream market source requested backoff."
    ),
    status.HTTP_500_INTERNAL_SERVER_ERROR: _error_response(
        "An unexpected market-data failure was safely normalized."
    ),
    status.HTTP_502_BAD_GATEWAY: _error_response("The upstream market response is invalid."),
    status.HTTP_503_SERVICE_UNAVAILABLE: _error_response(
        "The public market source is temporarily unavailable."
    ),
}


PAPER_TRADING_ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_400_BAD_REQUEST: _error_response("The paper-trading request is invalid."),
    status.HTTP_404_NOT_FOUND: _error_response("The paper-trading resource does not exist."),
    status.HTTP_409_CONFLICT: _error_response(
        "The paper-trading state is not currently available."
    ),
    status.HTTP_500_INTERNAL_SERVER_ERROR: _error_response(
        "An unexpected paper-trading failure was safely normalized."
    ),
    status.HTTP_503_SERVICE_UNAVAILABLE: _error_response(
        "The local paper-trading store is temporarily unavailable."
    ),
}
