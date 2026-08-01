"""ADT FastAPI application factory and runtime lifecycle."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.api.exceptions import setup_exception_handlers
from app.api.routes import (
    admin,
    admin_settings,
    admin_simulations,
    admin_strategies,
    health,
    public,
    system,
)
from app.auth import SupabaseJWTVerifier
from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.database import Database
from app.middleware import RequestContextMiddleware

logger = logging.getLogger(__name__)

_REQUEST_ID_OPENAPI_HEADER = {
    "description": "UUID correlation identifier assigned to this request.",
    "schema": {"type": "string", "format": "uuid"},
}


def _install_openapi_contract(application: FastAPI) -> None:
    """Document the correlation header on every declared response."""

    def custom_openapi() -> dict[str, object]:
        if application.openapi_schema is not None:
            return application.openapi_schema

        schema = get_openapi(
            title=application.title,
            version=application.version,
            description=application.description,
            routes=application.routes,
        )
        paths = schema.get("paths", {})
        if isinstance(paths, dict):
            for path_item in paths.values():
                if not isinstance(path_item, dict):
                    continue
                for operation in path_item.values():
                    if not isinstance(operation, dict):
                        continue
                    responses = operation.get("responses")
                    if not isinstance(responses, dict):
                        continue
                    for response in responses.values():
                        if not isinstance(response, dict):
                            continue
                        headers = response.setdefault("headers", {})
                        if isinstance(headers, dict):
                            headers.setdefault(
                                "X-Request-ID",
                                _REQUEST_ID_OPENAPI_HEADER,
                            )

        application.openapi_schema = schema
        return schema

    application.openapi = custom_openapi  # type: ignore[method-assign]


def create_app(app_settings: Settings | None = None) -> FastAPI:
    """Create the application and bind resources to its lifespan."""

    app_settings = app_settings or get_settings()
    setup_logging(app_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database = Database(app_settings.supabase_database_url.get_secret_value())
        async with httpx.AsyncClient(follow_redirects=False) as http_client:
            application.state.settings = app_settings
            application.state.database = database
            application.state.jwt_verifier = SupabaseJWTVerifier(
                issuer=app_settings.supabase_issuer,
                http_client=http_client,
            )

            try:
                await database.open()
                logger.info(
                    "API starting on %s:%s",
                    app_settings.api_host,
                    app_settings.api_port,
                )
                yield
            finally:
                await database.close()
                logger.info("API shutdown completed")

    application = FastAPI(
        title=app_settings.api_title,
        version="0.1.0",
        description="ADT — Automatic Dry Trade Backend",
        lifespan=lifespan,
        docs_url=None if app_settings.environment == "production" else "/docs",
        redoc_url=None if app_settings.environment == "production" else "/redoc",
        openapi_url=(None if app_settings.environment == "production" else "/openapi.json"),
    )
    application.state.settings = app_settings

    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Accept", "Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    application.add_middleware(
        RequestContextMiddleware,
        production=app_settings.environment == "production",
        cors_origins=app_settings.cors_origins_list,
    )

    setup_exception_handlers(application)
    application.include_router(health.router)
    application.include_router(system.router)
    application.include_router(public.router)
    application.include_router(admin.router)
    application.include_router(admin_simulations.router)
    application.include_router(admin_settings.router)
    application.include_router(admin_strategies.router)
    _install_openapi_contract(application)

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    runtime_settings: Settings = app.state.settings
    uvicorn.run(
        app,
        host=runtime_settings.api_host,
        port=runtime_settings.api_port,
        log_level=runtime_settings.log_level.lower(),
        log_config=None,
    )
