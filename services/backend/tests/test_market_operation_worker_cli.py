"""CLI boundary tests for the Phase 7 market-operation worker."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from io import StringIO
from types import SimpleNamespace
from typing import cast

import httpx
import pytest

import app.cli as cli_module
from app.cli import (
    EXIT_OK,
    _market_operation_worker_payload,
    build_parser,
    main,
)
from app.core.config import Settings
from app.market_data.operations import MarketOperationSnapshot


def test_parser_exposes_market_data_worker_run_once() -> None:
    args = build_parser().parse_args(["market-data", "worker", "run-once"])

    assert args.group == "market-data"
    assert args.command == "worker"
    assert args.worker_command == "run-once"


def test_worker_run_once_uses_full_settings_and_prints_idle_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = cast(Settings, object())
    transport = httpx.MockTransport(lambda request: httpx.Response(500, request=request))
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli_module,
        "get_settings",
        lambda: settings,
    )

    async def fake_run_once(
        received_settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        owner_id: object = None,
        clock: object = None,
    ) -> MarketOperationSnapshot | None:
        captured["settings"] = received_settings
        captured["transport"] = transport
        captured["owner_id"] = owner_id
        captured["clock"] = clock
        return None

    monkeypatch.setattr(
        cli_module,
        "run_market_operation_worker_once",
        fake_run_once,
    )

    output = StringIO()
    errors = StringIO()

    code = main(
        ["market-data", "worker", "run-once"],
        transport=transport,
        stdout=output,
        stderr=errors,
    )

    assert code == EXIT_OK
    assert errors.getvalue() == ""
    assert captured["settings"] is settings
    assert captured["transport"] is transport
    assert captured["owner_id"] is None
    assert captured["clock"] is None
    assert json.loads(output.getvalue()) == {
        "status": "IDLE",
        "operation": None,
    }


def test_worker_payload_exposes_only_sanitized_terminal_state() -> None:
    now = datetime(2026, 8, 12, 20, 0, tzinfo=UTC)

    operation = cast(
        MarketOperationSnapshot,
        SimpleNamespace(
            operation_id="20000000-0000-4000-8000-000000000001",
            state=SimpleNamespace(value="COMPLETED"),
            record_version=9,
            local_job_id="20000000-0000-4000-8000-000000000001",
            progress=SimpleNamespace(
                chunks_planned=2,
                chunks_completed=2,
                chunks_failed=0,
                candles_estimated=2,
                candles_received=2,
                candles_persisted=2,
                requests_completed=2,
                updated_at=now,
            ),
            result=SimpleNamespace(
                dataset_version="c" * 64,
                dataset_checksum="d" * 64,
                completed_at=now,
            ),
            failure=None,
            started_at=now,
            finished_at=now,
        ),
    )

    payload = _market_operation_worker_payload(operation)

    assert payload["status"] == "PROCESSED"
    assert payload["state"] == "COMPLETED"
    assert payload["record_version"] == 9
    assert payload["failure_code"] is None

    result = cast(dict[str, object], payload["result"])
    assert result["dataset_version"] == "c" * 64
    assert result["dataset_checksum"] == "d" * 64

    progress = cast(dict[str, object], payload["progress"])
    assert progress["chunks_completed"] == 2
    assert progress["candles_persisted"] == 2

    assert "lease" not in payload
    assert "request" not in payload
    assert "plan" not in payload
