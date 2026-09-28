"""Structured logging contract tests."""

from __future__ import annotations

import json
import logging

from app.core.logging import JsonLogFormatter


def _format(
    *,
    message: str = "test message",
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    record = logging.LogRecord(
        name="adt.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )

    for key, value in (extra or {}).items():
        setattr(
            record,
            key,
            value,
        )

    payload = JsonLogFormatter().format(
        record
    )

    return json.loads(payload)


def test_formatter_includes_safe_operational_fields() -> None:
    payload = _format(
        extra={
            "component": "market-supervisor",
            "event": "supervisor_starting",
            "operation": "poll",
            "attempts": 2,
        }
    )

    assert payload["component"] == (
        "market-supervisor"
    )
    assert payload["event"] == (
        "supervisor_starting"
    )
    assert payload["operation"] == "poll"
    assert payload["attempts"] == 2


def test_formatter_ignores_unlisted_fields() -> None:
    payload = _format(
        extra={
            "component": "paper-supervisor",
            "secret": "must-not-appear",
            "database_url": (
                "postgresql://user:password@example/db"
            ),
        }
    )

    assert payload["component"] == (
        "paper-supervisor"
    )
    assert "secret" not in payload
    assert "database_url" not in payload


def test_formatter_keeps_core_shape() -> None:
    payload = _format(
        message="hello"
    )

    assert payload["level"] == "INFO"
    assert payload["logger"] == "adt.test"
    assert payload["message"] == "hello"

    assert isinstance(
        payload["timestamp"],
        str,
    )


def test_formatter_does_not_promote_arbitrary_exception_context() -> None:
    payload = _format(
        extra={
            "exception_type": "ValueError",
            "exception_detail": (
                "sensitive arbitrary details"
            ),
        }
    )

    assert payload["exception_type"] == (
        "ValueError"
    )
    assert "exception_detail" not in payload


def test_formatter_includes_retry_delay_seconds() -> None:
    payload = _format(
        extra={
            "component": "paper-supervisor",
            "event": "persistence_retry",
            "operation": "list_nonterminal",
            "retry_delay_seconds": 0.25,
        }
    )

    assert payload["component"] == (
        "paper-supervisor"
    )
    assert payload["event"] == (
        "persistence_retry"
    )
    assert payload["operation"] == (
        "list_nonterminal"
    )
    assert payload["retry_delay_seconds"] == 0.25
