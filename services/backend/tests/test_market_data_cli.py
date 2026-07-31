"""Local market-data CLI tests without external network access."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from io import StringIO
from pathlib import Path

import httpx
import pytest
from pydantic import AnyHttpUrl, SecretStr

from app.cli import EXIT_DOMAIN_FAILURE, EXIT_OK, main
from app.core.config import Settings
from app.market_data.domain import DataRange
from app.market_data.jobs import MarketJobCatalog
from app.market_data.locks import DatasetLockManager
from app.market_data.planning import MarketDataPlanner
from app.market_data.timeframes import get_timeframe
from tests.market_data_helpers import binance_kline, exchange_info_payload, utc


def _settings(tmp_path: Path, *, max_fetch_candles: int = 10_000) -> Settings:
    return Settings(
        supabase_url=AnyHttpUrl("https://project.example.test"),
        supabase_publishable_key=SecretStr("public-test"),
        supabase_database_url=SecretStr("postgresql://test@example.test/adt"),
        environment="test",
        data_dir=tmp_path,
        market_http_retries=0,
        market_max_fetch_candles=max_fetch_candles,
    )


def _without_supabase_environment(tmp_path: Path) -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "SUPABASE_URL",
        "SUPABASE_PUBLISHABLE_KEY",
        "SUPABASE_DATABASE_URL",
    ):
        environment.pop(name, None)
    environment["ADT_DATA_DIR"] = str(tmp_path)
    return environment


@pytest.mark.parametrize(
    "arguments",
    (
        ("--help",),
        ("market-data", "--help"),
    ),
)
def test_cli_help_does_not_require_supabase(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", *arguments],
        cwd=Path(__file__).parents[1],
        env=_without_supabase_environment(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "SUPABASE_" not in result.stderr
    assert "market-data" in result.stdout


def test_local_market_data_command_does_not_require_supabase(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli",
            "market-data",
            "backfill",
            "plan",
            "--symbol",
            "BTC/USDT",
            "--timeframe",
            "1h",
            "--start",
            "2026-01-01T00:00:00Z",
            "--end",
            "2026-01-01T01:00:00Z",
        ],
        cwd=Path(__file__).parents[1],
        env=_without_supabase_environment(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout)["expected_candles"] == 1
    assert "SUPABASE_" not in result.stderr


def test_api_runtime_still_requires_supabase_configuration(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=Path(__file__).parents[1],
        env=_without_supabase_environment(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Missing required environment variables" in result.stderr
    assert "SUPABASE_URL" in result.stderr


def test_cli_fetch_dry_run_uses_mock_transport_and_writes_nothing(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("exchangeInfo"):
            return httpx.Response(200, json=exchange_info_payload(), request=request)
        return httpx.Response(200, json=[binance_kline(utc(2026, 1, 1))], request=request)

    output = StringIO()
    code = main(
        [
            "market-data",
            "fetch",
            "--symbol",
            "BTC/USDT",
            "--timeframe",
            "1h",
            "--start",
            "2026-01-01T00:00:00Z",
            "--end",
            "2026-01-01T01:00:00Z",
            "--dry-run",
        ],
        app_settings=_settings(tmp_path),
        transport=httpx.MockTransport(handler),
        stdout=output,
    )
    payload = json.loads(output.getvalue())

    assert code == EXIT_OK
    assert payload["dry_run"] is True
    assert payload["fetched"] == 1
    assert not (tmp_path / "market").exists()


def test_cli_invalid_arguments_return_argparse_exit_code() -> None:
    with pytest.raises(SystemExit) as captured:
        main(["market-data", "fetch", "--symbol", "../../secret"])
    assert captured.value.code == 2


def test_cli_failure_is_sanitized_and_does_not_print_source_payload(tmp_path: Path) -> None:
    secret_marker = "should-never-be-printed"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"code": -1121, "msg": secret_marker},
            request=request,
        )

    output = StringIO()
    errors = StringIO()
    code = main(
        [
            "market-data",
            "fetch",
            "--symbol",
            "BTC/USDT",
            "--timeframe",
            "1h",
            "--start",
            "2026-01-01T00:00:00Z",
            "--end",
            "2026-01-01T01:00:00Z",
        ],
        app_settings=_settings(tmp_path),
        transport=httpx.MockTransport(handler),
        stdout=output,
        stderr=errors,
    )

    assert code == EXIT_DOMAIN_FAILURE
    assert secret_marker not in errors.getvalue()
    assert "unknown_instrument" in errors.getvalue()


def test_cli_rejects_oversized_interval_without_any_http_request(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, request=request)

    errors = StringIO()
    code = main(
        [
            "market-data",
            "fetch",
            "--symbol",
            "BTC/USDT",
            "--timeframe",
            "1h",
            "--start",
            "2026-01-01T00:00:00Z",
            "--end",
            "2026-01-01T03:00:00Z",
        ],
        app_settings=_settings(tmp_path, max_fetch_candles=2),
        transport=httpx.MockTransport(handler),
        stdout=StringIO(),
        stderr=errors,
    )

    assert code == EXIT_DOMAIN_FAILURE
    assert calls == 0
    assert "invalid_data_range" in errors.getvalue()


def test_cli_backfill_plan_is_bounded_and_never_accesses_network(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected HTTP request: {request.method}")

    output = StringIO()
    code = main(
        [
            "market-data",
            "backfill",
            "plan",
            "--symbol",
            "BTC/USDT",
            "--timeframe",
            "1h",
            "--start",
            "2026-01-01T00:00:00Z",
            "--end",
            "2026-01-01T05:00:00Z",
        ],
        app_settings=_settings(tmp_path),
        transport=httpx.MockTransport(handler),
        stdout=output,
    )
    payload = json.loads(output.getvalue())

    assert code == EXIT_OK
    assert payload["expected_candles"] == 5
    assert payload["chunks"] == 1
    assert not (tmp_path / "market").exists()


def test_cli_large_backfill_requires_explicit_confirmation_without_http(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.market_backfill_chunk_candles = 2
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, request=request)

    errors = StringIO()
    code = main(
        [
            "market-data",
            "backfill",
            "run",
            "--symbol",
            "BTC/USDT",
            "--timeframe",
            "1h",
            "--start",
            "2026-01-01T00:00:00Z",
            "--end",
            "2026-01-01T05:00:00Z",
        ],
        app_settings=settings,
        transport=httpx.MockTransport(handler),
        stdout=StringIO(),
        stderr=errors,
    )

    assert code == EXIT_DOMAIN_FAILURE
    assert calls == 0
    assert "--yes" in errors.getvalue()


def test_cli_status_pause_and_cancel_are_local_only(tmp_path: Path) -> None:
    timeframe = get_timeframe("1h")
    plan = MarketDataPlanner(
        adapter_request_limit=1000,
        max_fetch_candles=1000,
        chunk_candles=2,
        max_total_candles=10,
        max_chunks=10,
    ).backfill(
        "binance:spot:BTC/USDT:1h",
        timeframe,
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 2)),
    )
    jobs = MarketJobCatalog(tmp_path)
    jobs.create(plan)
    jobs.start(plan.job_id)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected HTTP request: {request.method}")

    transport = httpx.MockTransport(handler)
    manager = DatasetLockManager(tmp_path, timeout_seconds=0, stale_after_seconds=60)
    with manager.acquire(plan.dataset_key):
        for command, expected in (
            ("status", "RUNNING"),
            ("pause", "PAUSED"),
            ("cancel", "CANCELLED"),
        ):
            output = StringIO()
            code = main(
                ["market-data", "backfill", command, "--job-id", plan.job_id],
                app_settings=_settings(tmp_path),
                transport=transport,
                stdout=output,
            )
            assert code == EXIT_OK
            assert json.loads(output.getvalue())["status"] == expected


def test_cli_initialization_recovers_abandoned_running_job(tmp_path: Path) -> None:
    timeframe = get_timeframe("1h")
    plan = MarketDataPlanner(
        adapter_request_limit=1000,
        max_fetch_candles=1000,
        chunk_candles=2,
        max_total_candles=10,
        max_chunks=10,
    ).backfill(
        "binance:spot:BTC/USDT:1h",
        timeframe,
        DataRange(utc(2026, 1, 1), utc(2026, 1, 1, 2)),
    )
    jobs = MarketJobCatalog(tmp_path)
    jobs.create(plan)
    jobs.start(plan.job_id)

    output = StringIO()
    code = main(
        ["market-data", "backfill", "status", "--job-id", plan.job_id],
        app_settings=_settings(tmp_path),
        transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(
                AssertionError(f"unexpected HTTP request: {request.method}")
            )
        ),
        stdout=output,
    )

    assert code == EXIT_OK
    assert json.loads(output.getvalue())["status"] == "FAILED"
    assert MarketJobCatalog(tmp_path).get(plan.job_id).error_code == "interrupted_job"

    def resume_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("exchangeInfo"):
            return httpx.Response(200, json=exchange_info_payload(), request=request)
        return httpx.Response(
            200,
            json=[
                binance_kline(utc(2026, 1, 1)),
                binance_kline(utc(2026, 1, 1, 1)),
            ],
            request=request,
        )

    resumed_output = StringIO()
    resumed_code = main(
        [
            "market-data",
            "backfill",
            "resume",
            "--job-id",
            plan.job_id,
            "--symbol",
            "BTC/USDT",
        ],
        app_settings=_settings(tmp_path),
        transport=httpx.MockTransport(resume_handler),
        stdout=resumed_output,
    )
    assert resumed_code == EXIT_OK
    assert json.loads(resumed_output.getvalue())["status"] == "COMPLETED"
