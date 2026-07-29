import logging
import sys

from app.core.config import settings


def setup_logging() -> None:
    """Configure structured logging."""
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

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
    logger.info(f"ADT Backend started in {settings.environment} environment")
