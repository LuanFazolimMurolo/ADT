"""Request correlation, structured access logs, and defensive HTTP headers."""

from __future__ import annotations

import logging
import time
from collections.abc import Collection

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.request_id import REQUEST_ID_HEADER, normalize_request_id

logger = logging.getLogger(__name__)

_API_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_DOCS_CONTENT_SECURITY_POLICY = (
    "default-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'; "
    "script-src 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "connect-src 'self'"
)
_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
_DEFAULT_MAX_REQUEST_BODY_BYTES = 1_048_576
_MAX_REQUEST_BODY_FRAMES = 1_024


class _RequestBodyTooLargeError(Exception):
    """Internal control-flow signal; never includes request content."""


class RequestContextMiddleware:
    """Attach one request ID and safe headers without logging request data."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        production: bool = False,
        cors_origins: Collection[str] = (),
        max_request_body_bytes: int = _DEFAULT_MAX_REQUEST_BODY_BYTES,
    ) -> None:
        self.app = app
        self.production = production
        self.cors_origins = frozenset(cors_origins)
        self.max_request_body_bytes = max_request_body_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        started_at = time.monotonic()
        status_code = 500
        response_started = False

        async def send_with_context(message: Message) -> None:
            nonlocal response_started, status_code
            if message.get("type") == "http.response.start":
                response_started = True
                raw_status = message.get("status", 500)
                status_code = raw_status if isinstance(raw_status, int) else 500
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                docs_request = request.url.path in {"/docs", "/redoc", "/openapi.json"}
                headers["Content-Security-Policy"] = (
                    _DOCS_CONTENT_SECURITY_POLICY
                    if docs_request and not self.production
                    else _API_CONTENT_SECURITY_POLICY
                )
                headers["Permissions-Policy"] = _PERMISSIONS_POLICY
                headers["X-Frame-Options"] = "DENY"
                if request.url.path.startswith("/api/v1/admin"):
                    headers["Cache-Control"] = "no-store"
                if self.production:
                    headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
                request_origin = request.headers.get("origin")
                if (
                    request_origin in self.cors_origins
                    and "access-control-allow-origin" not in headers
                ):
                    # CORSMiddleware handles normal and preflight responses. This
                    # fallback covers the safe 500 emitted here, outside it.
                    headers["Access-Control-Allow-Origin"] = request_origin
                    headers["Access-Control-Expose-Headers"] = REQUEST_ID_HEADER
                    headers.add_vary_header("Origin")
            await send(message)

        try:
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except ValueError:
                    declared_length = 0
                if declared_length > self.max_request_body_bytes:
                    raise _RequestBodyTooLargeError

            # Buffer at most one bounded body before route dispatch. Limiting
            # only while the endpoint calls receive() leaves body-less routes
            # vulnerable to an unconsumed chunked payload.
            buffered_body = bytearray()
            terminal_message: Message | None = None
            request_body_complete = False
            received_body_frames = 0
            while True:
                message = await receive()
                if message.get("type") != "http.request":
                    terminal_message = message
                    break
                received_body_frames += 1
                if received_body_frames > _MAX_REQUEST_BODY_FRAMES:
                    raise _RequestBodyTooLargeError
                body = message.get("body", b"")
                if isinstance(body, bytes):
                    buffered_body.extend(body)
                    if len(buffered_body) > self.max_request_body_bytes:
                        raise _RequestBodyTooLargeError
                if not message.get("more_body", False):
                    request_body_complete = True
                    break

            replayed_body = False
            replayed_terminal = False

            async def replay_receive() -> Message:
                nonlocal replayed_body, replayed_terminal
                if not replayed_body and (request_body_complete or buffered_body):
                    replayed_body = True
                    return {
                        "type": "http.request",
                        "body": bytes(buffered_body),
                        "more_body": not request_body_complete,
                    }
                if terminal_message is not None and not replayed_terminal:
                    replayed_terminal = True
                    return terminal_message
                return await receive()

            await self.app(scope, replay_receive, send_with_context)
        except _RequestBodyTooLargeError:
            if response_started:
                raise
            response = JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "request_too_large",
                        "message": "The request body is too large.",
                    }
                },
                headers={REQUEST_ID_HEADER: request_id},
            )
            await response(scope, receive, send_with_context)
        except Exception as error:
            if response_started:
                raise
            logger.error(
                "Unhandled exception while processing request",
                extra={
                    "request_id": request_id,
                    "exception_type": type(error).__name__,
                    "method": request.method,
                    "path": request.url.path,
                    "http_status": 500,
                },
            )
            response = JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "internal_error",
                        "message": "An internal server error occurred.",
                    }
                },
                headers={REQUEST_ID_HEADER: request_id},
            )
            await response(scope, receive, send_with_context)
        finally:
            logger.info(
                "HTTP request completed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "http_status": status_code,
                    "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
                },
            )
