"""Runtime wiring tests for the separate market-operation worker process."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from pydantic import AnyHttpUrl, SecretStr

import app.market_data.operation_worker_runtime as runtime_module
from app.core.config import Settings
from app.market_data.domain import DataRange, Exchange, MarketType, TradingPair
from app.market_data.operation_worker_runtime import (
    MARKET_OPERATION_HEARTBEAT_INTERVAL_SECONDS,
    MARKET_OPERATION_LEASE_DURATION,
    run_market_operation_worker_once,
)
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
from app.market_data.worker_observability import (
    WorkerRuntimeFailureCode,
)

OWNER_ID = UUID("44444444-4444-4444-8444-444444444444")
REQUESTER_ID = UUID("30000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 8, 12, 20, 0, tzinfo=UTC)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        supabase_url=AnyHttpUrl("https://project.example.test"),
        supabase_publishable_key=SecretStr("public-test"),
        supabase_database_url=SecretStr("postgresql://test@example.test/adt"),
        environment="test",
        data_dir=tmp_path,
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
    )


@dataclass(frozen=True, slots=True)
class FakeAdapterLimits:
    max_candles_per_request: int


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "worker_failure",
    (None, RuntimeError, asyncio.CancelledError),
)
async def test_runtime_wires_and_closes_separate_worker_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_failure: type[BaseException] | None,
) -> None:
    events: list[str] = []
    captured: dict[str, object] = {}
    history = object()

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
            self.limits = FakeAdapterLimits(max_candles_per_request=1_500)

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
        def __init__(
            self,
            data_dir: Path,
            **kwargs: object,
        ) -> None:
            captured["jobs_data_dir"] = data_dir
            captured["jobs_kwargs"] = kwargs
            captured["jobs_instance"] = self

    class FakePlanner:
        def __init__(self, **kwargs: object) -> None:
            captured["planner_kwargs"] = kwargs
            captured["planner_instance"] = self

    class FakeExecutor:
        def __init__(self, **kwargs: object) -> None:
            captured["executor_kwargs"] = kwargs
            captured["executor_instance"] = self

    class FakeRepository:
        def __init__(self, database: object) -> None:
            captured["repository_database"] = database
            captured["repository_instance"] = self

    class FakeSession:
        def __init__(self, **kwargs: object) -> None:
            captured["session_kwargs"] = kwargs
            captured["session_instance"] = self

    class FakeWorker:
        def __init__(self, **kwargs: object) -> None:
            captured["worker_kwargs"] = kwargs
            events.append("worker-init")

        async def run_once(self) -> MarketOperationSnapshot | None:
            events.append("worker-run")
            if worker_failure is not None:
                raise worker_failure("worker poll failed")
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

    if worker_failure is None:
        result = await run_market_operation_worker_once(
            _settings(tmp_path),
            owner_id=OWNER_ID,
        )
        assert result is None
    else:
        with pytest.raises(worker_failure):
            await run_market_operation_worker_once(
                _settings(tmp_path),
                owner_id=OWNER_ID,
            )

    assert events == [
        "database-open",
        "http-enter",
        "worker-init",
        "worker-run",
        "http-exit",
        "database-close",
    ]
    assert captured["dsn"] == "postgresql://test@example.test/adt"

    planner_kwargs = captured["planner_kwargs"]
    assert isinstance(planner_kwargs, dict)
    assert planner_kwargs["adapter_request_limit"] == 1_500

    executor_kwargs = captured["executor_kwargs"]
    assert isinstance(executor_kwargs, dict)
    assert executor_kwargs["history"] is history
    assert executor_kwargs["jobs"] is captured["jobs_instance"]

    session_kwargs = captured["session_kwargs"]
    assert isinstance(session_kwargs, dict)
    assert session_kwargs["repository"] is captured["repository_instance"]
    assert session_kwargs["owner_id"] == OWNER_ID
    assert session_kwargs["lease_duration"] == MARKET_OPERATION_LEASE_DURATION

    worker_kwargs = captured["worker_kwargs"]
    assert isinstance(worker_kwargs, dict)
    assert worker_kwargs["session"] is captured["session_instance"]
    assert worker_kwargs["planner"] is captured["planner_instance"]
    assert worker_kwargs["executor"] is captured["executor_instance"]
    assert worker_kwargs["jobs"] is captured["jobs_instance"]
    assert worker_kwargs["history"] is history
    assert (
        worker_kwargs["heartbeat_interval_seconds"] == MARKET_OPERATION_HEARTBEAT_INTERVAL_SECONDS
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("open_failure", (RuntimeError, asyncio.CancelledError))
async def test_runtime_closes_database_when_open_fails_or_is_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    open_failure: type[BaseException],
) -> None:
    events: list[str] = []

    class FailingDatabase:
        def __init__(self, dsn: str) -> None:
            assert dsn == "postgresql://test@example.test/adt"

        async def open(self) -> None:
            events.append("database-open")
            raise open_failure("database open failed")

        async def close(self) -> None:
            events.append("database-close")

    monkeypatch.setattr(runtime_module, "Database", FailingDatabase)

    with pytest.raises(open_failure):
        async with runtime_module.MarketOperationWorkerRuntime(_settings(tmp_path)):
            raise AssertionError("runtime unexpectedly opened")

    assert events == ["database-open", "database-close"]


def test_runtime_heartbeat_policy_stays_safely_inside_lease() -> None:
    lease_seconds = MARKET_OPERATION_LEASE_DURATION.total_seconds()

    assert lease_seconds > 0
    assert 0 < MARKET_OPERATION_HEARTBEAT_INTERVAL_SECONDS < lease_seconds
    assert MARKET_OPERATION_HEARTBEAT_INTERVAL_SECONDS == lease_seconds / 4


class SequenceRuntime:
    def __init__(
        self,
        operations: list[MarketOperationSnapshot | None],
    ) -> None:
        self.operations = list(operations)
        self.calls = 0

    async def run_once(self) -> MarketOperationSnapshot | None:
        self.calls += 1
        if not self.operations:
            raise AssertionError("unexpected extra worker poll")
        return self.operations.pop(0)


def _operation_marker() -> MarketOperationSnapshot:
    plan_checksum = "a" * 64
    request = MarketOperationRequest(
        operation_type=MarketOperationType.RAW_BACKFILL,
        dataset=MarketDatasetSelector(
            exchange=Exchange.BINANCE,
            market_type=MarketType.SPOT,
            pair=TradingPair("BTC", "USDT"),
            timeframe=get_timeframe("1h"),
        ),
        data_range=DataRange(NOW - timedelta(hours=2), NOW - timedelta(hours=1)),
        plan_checksum=plan_checksum,
        idempotency_key="phase7-01d2b2c-runtime",
        requested_by=REQUESTER_ID,
    )
    plan = OperationPlanSummary(
        checksum=plan_checksum,
        chunks_planned=1,
        estimated_candles=1,
        estimated_requests=1,
        created_at=NOW,
    )
    return MarketOperationSnapshot(
        operation_id=OWNER_ID,
        request=request,
        plan=plan,
        state=MarketOperationState.COMPLETED,
        progress=OperationProgress(
            chunks_planned=1,
            chunks_completed=1,
            chunks_failed=0,
            candles_estimated=1,
            candles_received=1,
            candles_persisted=1,
            requests_completed=1,
            updated_at=NOW,
        ),
        created_at=NOW,
        updated_at=NOW,
        record_version=2,
        result=OperationResult(
            dataset_version="b" * 64,
            dataset_checksum="c" * 64,
            completed_at=NOW,
        ),
        started_at=NOW,
        finished_at=NOW,
    )


@pytest.mark.asyncio
async def test_continuous_runner_sleeps_only_after_idle_poll() -> None:
    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    runtime = SequenceRuntime(
        [
            None,
            _operation_marker(),
            None,
        ]
    )

    runner = runtime_module.MarketOperationContinuousRunner(
        runtime=runtime,
        interval_seconds=2.5,
        sleeper=sleeper,
    )

    result = await runner.run(max_cycles=3)

    assert runtime.calls == 3
    assert sleeps == [2.5]
    assert result.cycles_completed == 3
    assert result.operations_processed == 1
    assert result.idle_cycles == 2
    assert result.last_operation_id == OWNER_ID
    assert result.last_state is not None
    assert result.last_state.value == "COMPLETED"


@pytest.mark.asyncio
async def test_loop_function_uses_one_runtime_context_and_stable_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    owners: list[UUID] = []
    operation = _operation_marker()

    class FakeLongLivedRuntime:
        def __init__(
            self,
            settings: Settings,
            *,
            transport: object = None,
            owner_id: UUID | None = None,
            clock: object = None,
        ) -> None:
            assert settings is fake_settings
            assert transport is fake_transport
            assert clock is None
            self.owner_id = owner_id if owner_id is not None else OWNER_ID
            self.calls = 0
            owners.append(self.owner_id)
            events.append("runtime-init")

        async def __aenter__(self) -> FakeLongLivedRuntime:
            events.append("runtime-enter")
            return self

        async def __aexit__(
            self,
            _exc_type: object,
            _exc: object,
            _traceback: object,
        ) -> None:
            events.append("runtime-exit")

        async def start_persistent_observability(
            self,
            *,
            on_failure: object = None,
        ) -> None:
            assert callable(on_failure)
            events.append("observability-start")

        async def stop_persistent_observability(self) -> None:
            events.append("observability-stop")

        async def run_once(self) -> MarketOperationSnapshot | None:
            self.calls += 1
            events.append(f"poll-{self.calls}")
            return operation if self.calls == 1 else None

    async def no_sleep(_seconds: float) -> None:
        events.append("sleep")

    fake_settings = _settings(tmp_path)
    fake_transport = httpx.MockTransport(lambda request: httpx.Response(500, request=request))

    monkeypatch.setattr(
        runtime_module,
        "MarketOperationWorkerRuntime",
        FakeLongLivedRuntime,
    )

    result = await runtime_module.run_market_operation_worker_loop(
        fake_settings,
        interval_seconds=3,
        max_cycles=2,
        transport=fake_transport,
        owner_id=OWNER_ID,
        sleeper=no_sleep,
    )

    assert owners == [OWNER_ID]
    assert events == [
        "runtime-init",
        "runtime-enter",
        "observability-start",
        "poll-1",
        "poll-2",
        "observability-stop",
        "runtime-exit",
    ]
    assert result.cycles_completed == 2
    assert result.operations_processed == 1
    assert result.idle_cycles == 1


@pytest.mark.asyncio
async def test_continuous_runner_rejects_invalid_cycle_policy() -> None:
    runtime = SequenceRuntime([None])

    for invalid_interval in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="interval_seconds"):
            runtime_module.MarketOperationContinuousRunner(
                runtime=runtime,
                interval_seconds=invalid_interval,
            )

    runner = runtime_module.MarketOperationContinuousRunner(
        runtime=runtime,
        interval_seconds=1,
    )

    for invalid_max_cycles in (0, -1):
        with pytest.raises(ValueError, match="max_cycles"):
            await runner.run(max_cycles=invalid_max_cycles)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("interval_seconds", "max_cycles", "error_match"),
    (
        (0.0, None, "interval_seconds"),
        (float("nan"), None, "interval_seconds"),
        (1.0, 0, "max_cycles"),
    ),
)
async def test_loop_policy_is_validated_before_runtime_resources_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interval_seconds: float,
    max_cycles: int | None,
    error_match: str,
) -> None:
    runtime_constructed = False

    class UnexpectedRuntime:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            nonlocal runtime_constructed
            runtime_constructed = True

    monkeypatch.setattr(
        runtime_module,
        "MarketOperationWorkerRuntime",
        UnexpectedRuntime,
    )

    with pytest.raises(ValueError, match=error_match):
        await runtime_module.run_market_operation_worker_loop(
            _settings(tmp_path),
            interval_seconds=interval_seconds,
            max_cycles=max_cycles,
        )

    assert runtime_constructed is False


@pytest.mark.asyncio
async def test_continuous_runner_stop_wakes_idle_sleep_without_another_poll() -> None:
    sleep_started = asyncio.Event()
    sleep_cancelled = asyncio.Event()

    async def sleeper(_seconds: float) -> None:
        sleep_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            sleep_cancelled.set()

    runtime = SequenceRuntime([None, _operation_marker()])
    runner = runtime_module.MarketOperationContinuousRunner(
        runtime=runtime,
        interval_seconds=60,
        sleeper=sleeper,
    )
    parent = asyncio.create_task(runner.run())

    await asyncio.wait_for(sleep_started.wait(), timeout=1)
    runner.request_stop()
    runner.request_stop()
    result = await asyncio.wait_for(parent, timeout=1)

    assert sleep_cancelled.is_set()
    assert runtime.calls == 1
    assert result.cycles_completed == 1
    assert result.idle_cycles == 1
    assert result.operations_processed == 0
    assert not any(
        task is not asyncio.current_task() and not task.done()
        for task in asyncio.all_tasks()
        if getattr(
            task.get_coro(),
            "__qualname__",
            "",
        ).endswith("wait_for_stop")
    )


@pytest.mark.asyncio
async def test_continuous_runner_propagates_unexpected_sleeper_error() -> None:
    async def failing_sleeper(_seconds: float) -> None:
        raise RuntimeError("synthetic sleeper failure")

    runner = runtime_module.MarketOperationContinuousRunner(
        runtime=SequenceRuntime([None]),
        interval_seconds=1,
        sleeper=failing_sleeper,
    )

    with pytest.raises(RuntimeError, match="synthetic sleeper failure"):
        await runner.run()


@pytest.mark.asyncio
async def test_continuous_runner_stop_cancels_and_joins_active_poll() -> None:
    poll_started = asyncio.Event()
    poll_cancelled = asyncio.Event()
    poll_joined = asyncio.Event()

    class BlockingRuntime:
        def __init__(self) -> None:
            self.calls = 0

        async def run_once(self) -> MarketOperationSnapshot | None:
            self.calls += 1
            poll_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                poll_cancelled.set()
                await asyncio.sleep(0)
                poll_joined.set()
                raise

            raise AssertionError("blocking poll unexpectedly resumed")

    runtime = BlockingRuntime()
    runner = runtime_module.MarketOperationContinuousRunner(
        runtime=runtime,
        interval_seconds=1,
    )
    parent = asyncio.create_task(runner.run())

    await asyncio.wait_for(poll_started.wait(), timeout=1)
    runner.request_stop()
    runner.request_stop()
    result = await asyncio.wait_for(parent, timeout=1)

    assert poll_cancelled.is_set()
    assert poll_joined.is_set()
    assert runtime.calls == 1
    assert result.cycles_completed == 0
    assert result.operations_processed == 0


@pytest.mark.asyncio
async def test_continuous_runner_stop_after_result_prevents_second_poll() -> None:
    holder: dict[str, runtime_module.MarketOperationContinuousRunner] = {}

    class StopAfterResultRuntime:
        def __init__(self) -> None:
            self.calls = 0

        async def run_once(self) -> MarketOperationSnapshot | None:
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("stop allowed an unexpected second poll")
            asyncio.get_running_loop().call_soon(holder["runner"].request_stop)
            return _operation_marker()

    runtime = StopAfterResultRuntime()
    runner = runtime_module.MarketOperationContinuousRunner(
        runtime=runtime,
        interval_seconds=1,
    )
    holder["runner"] = runner

    result = await runner.run()

    assert runtime.calls == 1
    assert result.cycles_completed == 1
    assert result.operations_processed == 1
    assert result.last_operation_id == OWNER_ID


def test_shutdown_signal_scope_requests_stop_and_restores_handlers() -> None:
    class FakeSignalLoop:
        def __init__(self) -> None:
            self.callbacks: list[object] = []

        def call_soon_threadsafe(
            self,
            callback: object,
        ) -> object:
            self.callbacks.append(callback)
            assert callable(callback)
            callback()
            return object()

    def previous_term(_signum: int, _frame: object) -> None:
        return None

    def previous_int(_signum: int, _frame: object) -> None:
        return None

    signal_loop = FakeSignalLoop()
    runner = runtime_module.MarketOperationContinuousRunner(
        runtime=SequenceRuntime([None]),
        interval_seconds=1,
    )
    original_term = signal.signal(signal.SIGTERM, previous_term)
    original_int = signal.signal(signal.SIGINT, previous_int)

    try:
        with runtime_module._market_operation_shutdown_signals(
            runner,
            loop=signal_loop,
        ):
            term_callback = signal.getsignal(signal.SIGTERM)
            int_callback = signal.getsignal(signal.SIGINT)
            assert callable(term_callback)
            assert callable(int_callback)
            term_callback(signal.SIGTERM, None)
            int_callback(signal.SIGINT, None)

        assert signal_loop.callbacks == [runner.request_stop, runner.request_stop]
        assert signal.getsignal(signal.SIGTERM) is previous_term
        assert signal.getsignal(signal.SIGINT) is previous_int
    finally:
        signal.signal(signal.SIGTERM, original_term)
        signal.signal(signal.SIGINT, original_int)


def test_shutdown_signal_scope_preserves_existing_event_loop_handler() -> None:
    signal_loop = asyncio.new_event_loop()
    runner = runtime_module.MarketOperationContinuousRunner(
        runtime=SequenceRuntime([None]),
        interval_seconds=1,
    )

    try:
        signal_loop.add_signal_handler(signal.SIGTERM, lambda: None)

        with runtime_module._market_operation_shutdown_signals(
            runner,
            loop=signal_loop,
        ):
            pass

        assert signal_loop.remove_signal_handler(signal.SIGTERM)
    finally:
        signal_loop.close()


@pytest.mark.asyncio
async def test_runtime_resources_close_only_after_active_poll_is_joined() -> None:
    events: list[str] = []
    poll_started = asyncio.Event()

    class ResourceRuntime:
        async def __aenter__(self) -> ResourceRuntime:
            events.append("runtime-enter")
            return self

        async def __aexit__(
            self,
            _exc_type: object,
            _exc: object,
            _traceback: object,
        ) -> None:
            events.append("runtime-exit")

        async def run_once(self) -> MarketOperationSnapshot | None:
            events.append("poll-start")
            poll_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                events.append("poll-cancel")
                await asyncio.sleep(0)
                events.append("poll-joined")
                raise

            raise AssertionError("resource poll unexpectedly resumed")

    runtime = ResourceRuntime()
    runner = runtime_module.MarketOperationContinuousRunner(
        runtime=runtime,
        interval_seconds=1,
    )

    async with runtime:
        parent = asyncio.create_task(runner.run())
        await asyncio.wait_for(poll_started.wait(), timeout=1)
        runner.request_stop()
        await asyncio.wait_for(parent, timeout=1)

    assert events == [
        "runtime-enter",
        "poll-start",
        "poll-cancel",
        "poll-joined",
        "runtime-exit",
    ]


@pytest.mark.asyncio
async def test_loop_process_boundary_propagates_unexpected_application_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FailingRuntime:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            events.append("runtime-init")

        async def __aenter__(self) -> FailingRuntime:
            events.append("runtime-enter")
            return self

        async def __aexit__(
            self,
            _exc_type: object,
            _exc: object,
            _traceback: object,
        ) -> None:
            events.append("runtime-exit")

        async def start_persistent_observability(
            self,
            *,
            on_failure: object = None,
        ) -> None:
            assert callable(on_failure)
            events.append("observability-start")

        async def stop_persistent_observability(self) -> None:
            events.append("observability-stop")

        async def fail_persistent_observability(
            self,
            failure_code: WorkerRuntimeFailureCode,
        ) -> None:
            events.append(f"observability-fail:{failure_code.value}")

        async def run_once(self) -> MarketOperationSnapshot | None:
            events.append("poll")
            raise RuntimeError("synthetic worker failure")

    @contextmanager
    def fake_signals(
        _runner: runtime_module.MarketOperationContinuousRunner,
    ) -> Iterator[None]:
        events.append("signals-enter")
        try:
            yield
        finally:
            events.append("signals-exit")

    monkeypatch.setattr(runtime_module, "MarketOperationWorkerRuntime", FailingRuntime)
    monkeypatch.setattr(runtime_module, "_market_operation_shutdown_signals", fake_signals)

    with pytest.raises(RuntimeError, match="synthetic worker failure"):
        await runtime_module.run_market_operation_worker_loop(
            _settings(tmp_path),
            interval_seconds=1,
        )

    assert events == [
        "runtime-init",
        "signals-enter",
        "runtime-enter",
        "observability-start",
        "poll",
        "observability-fail:UNEXPECTED_FAILURE",
        "runtime-exit",
        "signals-exit",
    ]
