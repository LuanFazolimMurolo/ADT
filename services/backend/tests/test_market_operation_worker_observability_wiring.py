"""Focused persistent-worker observability wiring tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

import app.market_data.operation_worker_runtime as runtime_module
from app.core.config import Settings
from app.domain.errors import (
    PersistenceError,
    PersistenceUnavailableError,
)
from app.market_data.errors import (
    MarketDataInconsistencyError,
    MarketDataStorageError,
)
from app.market_data.operation_worker import MarketOperationWorker
from app.market_data.operation_worker_runtime import (
    MarketOperationWorkerRuntime,
)
from app.market_data.operations import (
    MarketOperationSnapshot,
    MarketOperationState,
)
from app.market_data.worker_observability import (
    WorkerRuntimeActivityState,
    WorkerRuntimeFailureCode,
)
from app.market_data.worker_observability_runtime import (
    WorkerRuntimePresenceSession,
)


def _runtime() -> MarketOperationWorkerRuntime:
    return MarketOperationWorkerRuntime(
        cast(Settings, object()),
        owner_id=uuid4(),
    )


async def test_transient_run_once_does_not_touch_runtime_presence() -> None:
    events: list[object] = []

    class FakeWorker:
        async def run_once(self) -> MarketOperationSnapshot | None:
            events.append("worker")
            return None

    class UnexpectedPresence:
        async def set_activity(
            self,
            _state: WorkerRuntimeActivityState,
        ) -> object:
            raise AssertionError("transient run-once touched persistent presence")

    runtime = _runtime()
    runtime._worker = cast(MarketOperationWorker, FakeWorker())
    runtime._presence = cast(
        WorkerRuntimePresenceSession,
        UnexpectedPresence(),
    )

    result = await runtime.run_once()

    assert result is None
    assert events == ["worker"]


async def test_persistent_poll_is_active_and_records_settlement() -> None:
    events: list[object] = []
    operation_id = uuid4()

    operation = cast(
        MarketOperationSnapshot,
        SimpleNamespace(
            operation_id=operation_id,
            state=MarketOperationState.COMPLETED,
        ),
    )

    class FakeWorker:
        async def run_once(self) -> MarketOperationSnapshot | None:
            events.append("worker")
            return operation

    class FakePresence:
        async def set_activity(
            self,
            state: WorkerRuntimeActivityState,
        ) -> object:
            events.append(("activity", state))
            return object()

        async def record_operation_settled(
            self,
            *,
            operation_id: object,
            operation_state: object,
        ) -> object:
            events.append(
                (
                    "settled",
                    operation_id,
                    operation_state,
                )
            )
            return object()

    runtime = _runtime()
    runtime._worker = cast(MarketOperationWorker, FakeWorker())
    runtime._presence = cast(
        WorkerRuntimePresenceSession,
        FakePresence(),
    )
    runtime._persistent_observability = True

    result = await runtime.run_once()

    assert result is operation
    assert events == [
        ("activity", WorkerRuntimeActivityState.ACTIVE),
        "worker",
        (
            "settled",
            operation_id,
            MarketOperationState.COMPLETED,
        ),
        ("activity", WorkerRuntimeActivityState.IDLE),
    ]


async def test_background_presence_is_cancelled_before_confirmed_stop() -> None:
    events: list[str] = []
    heartbeat_started = asyncio.Event()
    heartbeat_cancelled = asyncio.Event()

    class FakeWorker:
        async def run_once(self) -> MarketOperationSnapshot | None:
            return None

    class FakePresence:
        async def start(self) -> object:
            events.append("start")
            return object()

        async def heartbeat_forever(self) -> None:
            events.append("heartbeat-start")
            heartbeat_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                events.append("heartbeat-cancel")
                heartbeat_cancelled.set()
                raise

        async def stop(self) -> object:
            events.append("stop")
            return object()

    runtime = _runtime()
    runtime._worker = cast(MarketOperationWorker, FakeWorker())
    runtime._presence = cast(
        WorkerRuntimePresenceSession,
        FakePresence(),
    )

    await runtime.start_persistent_observability()

    await asyncio.wait_for(
        heartbeat_started.wait(),
        timeout=1,
    )

    assert runtime._persistent_observability

    await runtime.stop_persistent_observability()

    assert heartbeat_cancelled.is_set()
    assert not runtime._persistent_observability
    assert events == [
        "start",
        "heartbeat-start",
        "heartbeat-cancel",
        "stop",
    ]


async def test_background_heartbeat_failure_requests_shutdown_and_refuses_stop() -> None:
    events: list[object] = []
    shutdown_requested = asyncio.Event()

    class FakeWorker:
        async def run_once(self) -> MarketOperationSnapshot | None:
            return None

    class FailingPresence:
        async def start(self) -> object:
            events.append("start")
            return object()

        async def heartbeat_forever(self) -> None:
            events.append("heartbeat-fail")
            raise PersistenceError()

        async def stop(self) -> object:
            events.append("stop")
            return object()

        async def fail(
            self,
            failure_code: WorkerRuntimeFailureCode,
        ) -> object:
            events.append(("fail", failure_code))
            return object()

    runtime = _runtime()
    runtime._worker = cast(MarketOperationWorker, FakeWorker())
    runtime._presence = cast(
        WorkerRuntimePresenceSession,
        FailingPresence(),
    )

    def request_shutdown() -> None:
        events.append("shutdown-requested")
        shutdown_requested.set()

    await runtime.start_persistent_observability(
        on_failure=request_shutdown,
    )

    await asyncio.wait_for(
        shutdown_requested.wait(),
        timeout=1,
    )

    assert runtime._persistent_observability

    with pytest.raises(
        runtime_module.WorkerRuntimePresenceHeartbeatError,
        match="heartbeat failed",
    ):
        await runtime.stop_persistent_observability()

    assert "stop" not in events

    await runtime.fail_persistent_observability(WorkerRuntimeFailureCode.DATABASE_FAILURE)

    assert not runtime._persistent_observability
    assert events == [
        "start",
        "heartbeat-fail",
        "shutdown-requested",
        (
            "fail",
            WorkerRuntimeFailureCode.DATABASE_FAILURE,
        ),
    ]


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (
            PersistenceError(),
            WorkerRuntimeFailureCode.DATABASE_FAILURE,
        ),
        (
            PersistenceUnavailableError(),
            WorkerRuntimeFailureCode.DATABASE_FAILURE,
        ),
        (
            MarketDataInconsistencyError(),
            WorkerRuntimeFailureCode.LOCAL_STATE_FAILURE,
        ),
        (
            MarketDataStorageError(),
            WorkerRuntimeFailureCode.LOCAL_STATE_FAILURE,
        ),
        (
            OSError("synthetic local failure"),
            WorkerRuntimeFailureCode.LOCAL_STATE_FAILURE,
        ),
        (
            RuntimeError("synthetic unexpected failure"),
            WorkerRuntimeFailureCode.UNEXPECTED_FAILURE,
        ),
    ),
)
def test_runtime_failure_classification_is_closed(
    error: Exception,
    expected: WorkerRuntimeFailureCode,
) -> None:
    assert runtime_module._worker_runtime_failure_code(error) is expected


async def test_external_cancellation_never_confirms_terminal_state(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    poll_started = asyncio.Event()

    class CancelRuntime:
        def __init__(
            self,
            *_args: object,
            **_kwargs: object,
        ) -> None:
            events.append("runtime-init")

        async def __aenter__(self) -> CancelRuntime:
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
            events.append(
                (
                    "observability-fail",
                    failure_code,
                )
            )

        async def run_once(self) -> MarketOperationSnapshot | None:
            events.append("poll")
            poll_started.set()
            await asyncio.Event().wait()
            raise AssertionError("poll unexpectedly resumed")

    monkeypatch.setattr(
        runtime_module,
        "MarketOperationWorkerRuntime",
        CancelRuntime,
    )

    settings = cast(
        Settings,
        SimpleNamespace(),
    )

    task = asyncio.create_task(
        runtime_module.run_market_operation_worker_loop(
            settings,
            interval_seconds=1,
        )
    )

    await asyncio.wait_for(
        poll_started.wait(),
        timeout=1,
    )

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert "observability-stop" not in events
    assert not any(
        isinstance(event, tuple) and event[0] == "observability-fail" for event in events
    )
