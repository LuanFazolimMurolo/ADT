"""Stable, non-sensitive error response schemas."""

from app.api.schemas.common import ApiSchema, JsonValue, NonBlankText


class ErrorDetail(ApiSchema):
    """A sanitized validation failure for one request location."""

    code: NonBlankText
    message: NonBlankText
    field: NonBlankText | None = None


class ErrorPayload(ApiSchema):
    """Machine-readable error nested inside the standard envelope."""

    code: NonBlankText
    message: NonBlankText
    details: JsonValue | None = None


class ErrorResponse(ApiSchema):
    """Envelope returned for every handled API failure."""

    error: ErrorPayload
