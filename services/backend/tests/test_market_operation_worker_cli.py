"""CLI boundary tests for the Phase 7 market-operation worker."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from uuid import UUID

import httpx
import pytest
from pydantic import AnyHttpUrl, SecretStr

import app.cli as cli_module
from app.cli import (
    EXIT_OK,
    _market_operation_worker_payload,
    build_parser,
    main,
)
from app.core.config import Settings
from app.market_data.domain import DataRange, Exchange, MarketType, TradingPair
from app.market_data.operation_worker_runtime import MarketOperationWorkerLoopResult
from app.market_data.operations import (
    MarketDatasetSelector,
    MarketOperationRequest,
    MarketOperationSnapshot,
    MarketOperationState,
    MarketOperationType,
    OperationPlanSummary,
    OperationProgress,
    OperationResult,
)
from app.market_data.timeframes import get_timeframe

OPERATION_ID = UUID("20000000-0000-4000-8000-000000000001")
REQUESTER_ID = UUID("30000000-0000-4000-8000-000000000001")


def _settings() -> Settings:
    return Settings(
        supabase_url=AnyHttpUrl("https://project.example.test"),
        supabase_publishable_key=SecretStr("public-test"),
        supabase_database_url=SecretStr("postgresql://test@example.test/adt"),
        environment="test",
        market_http_retries=0,
    )


def _completed_operation(now: datetime) -> MarketOperationSnapshot:
    plan_checksum = "a" * 64
    request = MarketOperationRequest(
        operation_type=MarketOperationType.RAW_BACKFILL,
        dataset=MarketDatasetSelector(
            exchange=Exchange.BINANCE,
            market_type=MarketType.SPOT,
            pair=TradingPair("BTC", "USDT"),
            timeframe=get_timeframe("1h"),
        ),
        data_range=DataRange(now - timedelta(hours=2), now - timedelta(hours=1)),
        plan_checksum=plan_checksum,
        idempotency_key="phase7-01d2b2c-cli",
        requested_by=REQUESTER_ID,
    )
    plan = OperationPlanSummary(
        checksum=plan_checksum,
        chunks_planned=2,
        estimated_candles=2,
        estimated_requests=2,
        created_at=now,
    )
    return MarketOperationSnapshot(
        operation_id=OPERATION_ID,
        request=request,
        plan=plan,
        state=MarketOperationState.COMPLETED,
        progress=OperationProgress(
            chunks_planned=2,
            chunks_completed=2,
            chunks_failed=0,
            candles_estimated=2,
            candles_received=2,
            candles_persisted=2,
            requests_completed=2,
            updated_at=now,
        ),
        created_at=now,
        updated_at=now,
        record_version=9,
        local_job_id=str(OPERATION_ID),
        result=OperationResult(
            dataset_version="c" * 64,
            dataset_checksum="d" * 64,
            completed_at=now,
        ),
        started_at=now,
        finished_at=now,
    )


def test_parser_exposes_market_data_worker_run_once() -> None:
    args = build_parser().parse_args(["market-data", "worker", "run-once"])

    assert args.group == "market-data"
    assert args.command == "worker"
    assert args.worker_command == "run-once"


def test_worker_run_once_uses_full_settings_and_prints_idle_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
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
    operation = _completed_operation(now)

    payload = _market_operation_worker_payload(operation)

    assert payload["status"] == "PROCESSED"
    assert payload["state"] == "COMPLETED"
    assert payload["record_version"] == 9
    assert payload["failure_code"] is None

    result = payload["result"]
    assert isinstance(result, dict)
    assert result["dataset_version"] == "c" * 64
    assert result["dataset_checksum"] == "d" * 64

    progress = payload["progress"]
    assert isinstance(progress, dict)
    assert progress["chunks_completed"] == 2
    assert progress["candles_persisted"] == 2

    assert "lease" not in payload
    assert "request" not in payload
    assert "plan" not in payload


def test_parser_exposes_market_data_worker_loop() -> None:
    args = build_parser().parse_args(
        [
            "market-data",
            "worker",
            "loop",
            "--interval-seconds",
            "2.5",
            "--max-cycles",
            "7",
        ]
    )

    assert args.group == "market-data"
    assert args.command == "worker"
    assert args.worker_command == "loop"
    assert args.interval_seconds == 2.5
    assert args.max_cycles == 7


@pytest.mark.parametrize(
    ("extra_arguments", "expected_max_cycles"),
    (((), None), (("--max-cycles", "4"), 4)),
)
def test_worker_loop_uses_full_settings_and_prints_sanitized_summary(
    monkeypatch: pytest.MonkeyPatch,
    extra_arguments: tuple[str, ...],
    expected_max_cycles: int | None,
) -> None:
    settings = _settings()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            500,
            request=request,
        )
    )
    captured: dict[str, object] = {}

    async def fake_loop(
        received_settings: Settings,
        *,
        interval_seconds: float,
        max_cycles: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> MarketOperationWorkerLoopResult:
        captured["settings"] = received_settings
        captured["interval_seconds"] = interval_seconds
        captured["max_cycles"] = max_cycles
        captured["transport"] = transport

        return MarketOperationWorkerLoopResult(
            cycles_completed=4,
            operations_processed=3,
            idle_cycles=1,
            last_operation_id=OPERATION_ID,
            last_state=MarketOperationState.COMPLETED,
        )

    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_module, "run_market_operation_worker_loop", fake_loop)

    stdout = StringIO()

    code = main(
        [
            "market-data",
            "worker",
            "loop",
            "--interval-seconds",
            "1.5",
            *extra_arguments,
        ],
        transport=transport,
        stdout=stdout,
    )

    assert code == EXIT_OK

    assert captured == {
        "settings": settings,
        "interval_seconds": 1.5,
        "max_cycles": expected_max_cycles,
        "transport": transport,
    }

    payload = json.loads(stdout.getvalue())

    assert payload == {
        "status": "COMPLETED",
        "cycles_completed": 4,
        "operations_processed": 3,
        "idle_cycles": 1,
        "last_operation_id": str(OPERATION_ID),
        "last_state": "COMPLETED",
    }


def test_worker_loop_parser_allows_unbounded_cycles() -> None:
    args = build_parser().parse_args(
        [
            "market-data",
            "worker",
            "loop",
            "--interval-seconds",
            "5",
        ]
    )

    assert args.worker_command == "loop"
    assert args.interval_seconds == 5.0
    assert args.max_cycles is None
