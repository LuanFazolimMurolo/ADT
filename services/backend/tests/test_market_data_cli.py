"""Local market-data CLI tests without external network access."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import httpx
import pytest
from pydantic import AnyHttpUrl, SecretStr

from app.cli import EXIT_DOMAIN_FAILURE, EXIT_OK, main
from app.core.config import Settings
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
