"""Safe request-correlation identifiers shared by middleware and errors."""

from uuid import UUID, uuid4

from fastapi import Request

REQUEST_ID_HEADER = "X-Request-ID"


def normalize_request_id(value: str | None) -> str:
    """Accept canonical UUIDs only, preventing log/header injection."""
    if value is not None:
        try:
            return str(UUID(value))
        except (ValueError, AttributeError):
            pass
    return str(uuid4())


def get_request_id(request: Request) -> str:
    """Return the middleware identifier or create one for direct handler tests."""
    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str):
        return request_id
    request_id = normalize_request_id(None)
    request.state.request_id = request_id
    return request_id
