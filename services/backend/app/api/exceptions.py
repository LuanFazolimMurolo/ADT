import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

async def validation_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc

    return JSONResponse(
        status_code=422,
        content={
            "detail": exc.errors(),
        },
    )


async def general_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Handle general exceptions."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


def setup_exception_handlers(app: FastAPI) -> None:
    """Register exception handlers."""
    app.add_exception_handler(
        RequestValidationError,
        validation_exception_handler,
    )
    app.add_exception_handler(
        Exception,
        general_exception_handler,
    )
