import logging
import sys

from app.core.config import Settings, settings


def setup_logging(app_settings: Settings = settings) -> None:
    """Configure structured logging."""
    log_level = getattr(logging, app_settings.log_level.upper(), logging.INFO)

    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Log application startup
    logger = logging.getLogger(__name__)
    logger.info("ADT Backend started in %s environment", app_settings.environment)
