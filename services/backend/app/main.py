"""ADT FastAPI application factory and runtime lifecycle."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.exceptions import setup_exception_handlers
from app.api.routes import (
    admin,
    admin_settings,
    admin_simulations,
    health,
    public,
    system,
)
from app.auth import SupabaseJWTVerifier
from app.core.config import Settings, settings
from app.core.logging import setup_logging
from app.database import Database

logger = logging.getLogger(__name__)


def create_app(app_settings: Settings = settings) -> FastAPI:
    """Create the application and bind resources to its lifespan."""

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
    )
    application.state.settings = app_settings

    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )

    setup_exception_handlers(application)
    application.include_router(health.router)
    application.include_router(system.router)
    application.include_router(public.router)
    application.include_router(admin.router)
    application.include_router(admin_simulations.router)
    application.include_router(admin_settings.router)

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )
