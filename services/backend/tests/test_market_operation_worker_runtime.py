"""Runtime wiring tests for the separate market-operation worker process."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest

import app.market_data.operation_worker_runtime as runtime_module
from app.core.config import Settings
from app.market_data.operation_worker_runtime import (
    MARKET_OPERATION_HEARTBEAT_INTERVAL_SECONDS,
    MARKET_OPERATION_LEASE_DURATION,
    run_market_operation_worker_once,
)

OWNER_ID = UUID("44444444-4444-4444-8444-444444444444")


class FakeSecret:
    def get_secret_value(self) -> str:
        return "test-database-dsn"


def _settings(tmp_path: Path) -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            data_dir=tmp_path,
            supabase_database_url=FakeSecret(),
            market_user_agent="ADT-MarketData-Test/1.0",
            market_http_timeout=5.0,
            market_http_max_connections=4,
            market_http_retries=0,
            market_http_max_retry_after=1.0,
            market_allow_open_candles=False,
            market_max_fetch_candles=10_000,
            market_job_lock_timeout=10.0,
            market_job_stale_after=3_600.0,
            market_backfill_chunk_candles=1_000,
            market_backfill_max_total_candles=100_000,
            market_job_max_chunks=1_000,
        ),
    )


@pytest.mark.asyncio
async def test_runtime_wires_and_closes_separate_worker_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    captured: dict[str, object] = {}
    history = object()
    jobs_instance = object()
    planner_instance = object()
    executor_instance = object()
    repository_instance = object()
    session_instance = object()

    class FakeDatabase:
        def __init__(self, dsn: str) -> None:
            captured["dsn"] = dsn

        async def open(self) -> None:
            events.append("database-open")

        async def close(self) -> None:
            events.append("database-close")

    class FakeHttpClient:
        def __init__(self, **kwargs: object) -> None:
            captured["http"] = kwargs

        async def __aenter__(self) -> FakeHttpClient:
            events.append("http-enter")
            return self

        async def __aexit__(
            self,
            _exc_type: object,
            _exc: object,
            _traceback: object,
        ) -> None:
            events.append("http-exit")

    class FakeAdapter:
        def __init__(
            self,
            client: object,
            *,
            allow_open_candles: bool,
            now: object,
        ) -> None:
            captured["adapter_client"] = client
            captured["allow_open_candles"] = allow_open_candles
            captured["adapter_clock"] = now
            self.limits = SimpleNamespace(max_candles_per_request=1_500)

    def fake_default_local_services(
        data_dir: Path,
        adapter: object,
        **kwargs: object,
    ) -> tuple[object, object]:
        captured["local_data_dir"] = data_dir
        captured["local_adapter"] = adapter
        captured["local_kwargs"] = kwargs
        return object(), history

    class FakeJobs:
        def __new__(
            cls,
            data_dir: Path,
            **kwargs: object,
        ) -> object:
            captured["jobs_data_dir"] = data_dir
            captured["jobs_kwargs"] = kwargs
            return jobs_instance

    class FakePlanner:
        def __new__(cls, **kwargs: object) -> object:
            captured["planner_kwargs"] = kwargs
            return planner_instance

    class FakeExecutor:
        def __new__(cls, **kwargs: object) -> object:
            captured["executor_kwargs"] = kwargs
            return executor_instance

    class FakeRepository:
        def __new__(cls, database: object) -> object:
            captured["repository_database"] = database
            return repository_instance

    class FakeSession:
        def __new__(cls, **kwargs: object) -> object:
            captured["session_kwargs"] = kwargs
            return session_instance

    class FakeWorker:
        def __init__(self, **kwargs: object) -> None:
            captured["worker_kwargs"] = kwargs

        async def run_once(self) -> None:
            events.append("worker-run")
            return None

    monkeypatch.setattr(runtime_module, "Database", FakeDatabase)
    monkeypatch.setattr(runtime_module, "PublicMarketHttpClient", FakeHttpClient)
    monkeypatch.setattr(runtime_module, "BinanceSpotAdapter", FakeAdapter)
    monkeypatch.setattr(
        runtime_module,
        "default_local_services",
        fake_default_local_services,
    )
    monkeypatch.setattr(runtime_module, "MarketJobCatalog", FakeJobs)
    monkeypatch.setattr(runtime_module, "MarketDataPlanner", FakePlanner)
    monkeypatch.setattr(runtime_module, "BackfillExecutor", FakeExecutor)
    monkeypatch.setattr(
        runtime_module,
        "PostgresMarketOperationRepository",
        FakeRepository,
    )
    monkeypatch.setattr(runtime_module, "MarketOperationWorkerSession", FakeSession)
    monkeypatch.setattr(runtime_module, "MarketOperationWorker", FakeWorker)

    result = await run_market_operation_worker_once(
        _settings(tmp_path),
        owner_id=OWNER_ID,
    )

    assert result is None
    assert events == [
        "database-open",
        "http-enter",
        "worker-run",
        "http-exit",
        "database-close",
    ]
    assert captured["dsn"] == "test-database-dsn"

    planner_kwargs = cast(dict[str, object], captured["planner_kwargs"])
    assert planner_kwargs["adapter_request_limit"] == 1_500

    executor_kwargs = cast(dict[str, object], captured["executor_kwargs"])
    assert executor_kwargs["history"] is history
    assert executor_kwargs["jobs"] is jobs_instance

    session_kwargs = cast(dict[str, object], captured["session_kwargs"])
    assert session_kwargs["repository"] is repository_instance
    assert session_kwargs["owner_id"] == OWNER_ID
    assert session_kwargs["lease_duration"] == MARKET_OPERATION_LEASE_DURATION

    worker_kwargs = cast(dict[str, object], captured["worker_kwargs"])
    assert worker_kwargs["session"] is session_instance
    assert worker_kwargs["planner"] is planner_instance
    assert worker_kwargs["executor"] is executor_instance
    assert worker_kwargs["jobs"] is jobs_instance
    assert worker_kwargs["history"] is history
    assert (
        worker_kwargs["heartbeat_interval_seconds"] == MARKET_OPERATION_HEARTBEAT_INTERVAL_SECONDS
    )


def test_runtime_heartbeat_policy_stays_safely_inside_lease() -> None:
    lease_seconds = MARKET_OPERATION_LEASE_DURATION.total_seconds()

    assert lease_seconds > 0
    assert 0 < MARKET_OPERATION_HEARTBEAT_INTERVAL_SECONDS < lease_seconds
    assert MARKET_OPERATION_HEARTBEAT_INTERVAL_SECONDS == lease_seconds / 4
