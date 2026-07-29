import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.exceptions import setup_exception_handlers
from app.api.routes import health, system
from app.core.config import settings
from app.core.logging import setup_logging

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title=settings.api_title,
        version="0.0.0",
        description="ADT — Automatic Dry Trade Backend",
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Exception handlers
    setup_exception_handlers(app)

    # Routes
    app.include_router(health.router)
    app.include_router(system.router)

    logger.info("Application initialized successfully")
    return app


app = create_app()


@app.on_event("startup")
async def startup() -> None:
    """Run on application startup."""
    logger.info(f"API starting on {settings.api_host}:{settings.api_port}")


@app.on_event("shutdown")
async def shutdown() -> None:
    """Run on application shutdown."""
    logger.info("API shutting down")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )
