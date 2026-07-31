import json
import logging
import sys
from datetime import UTC, datetime
from typing import Final

from app.core.config import Settings, settings

_STRUCTURED_FIELDS: Final[tuple[str, ...]] = (
    "request_id",
    "method",
    "path",
    "http_status",
    "duration_ms",
    "error_code",
    "exception_type",
    "operation",
    "provider",
    "attempts",
    "used_weight",
)


class JsonLogFormatter(logging.Formatter):
    """Serialize a narrow, non-sensitive set of log record attributes."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field_name in _STRUCTURED_FIELDS:
            value = getattr(record, field_name, None)
            if isinstance(value, (str, int, float)):
                payload[field_name] = value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def setup_logging(app_settings: Settings = settings) -> None:
    """Configure structured logging."""
    log_level = getattr(logging, app_settings.log_level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    logger = logging.getLogger(__name__)
    logger.info("ADT Backend logging initialized")
