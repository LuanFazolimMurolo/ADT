"""Runtime coordination tests for persistent worker observability."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.market_data.operations import MarketOperationState
from app.market_data.worker_observability import (
    WorkerRuntimeActivityState,
    WorkerRuntimeEvent,
    WorkerRuntimeEventType,
    WorkerRuntimeFailureCode,
    WorkerRuntimeLifecycleState,
    WorkerRuntimeSnapshot,
)
from app.market_data.worker_observability_runtime import (
    WORKER_RUNTIME_OBSERVABILITY_HEARTBEAT_INTERVAL_SECONDS,
    WorkerRuntimePresenceSession,
)

BASE_TIME = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


@dataclass(slots=True)
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class ControlledSleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.entered.set()
        await self.release.wait()
        self.release.clear()


class SpyRepository:
    def __init__(self) -> None:
        self.runtime: WorkerRuntimeSnapshot | None = None
        self.events: list[WorkerRuntimeEvent] = []
        self.heartbeats: list[tuple[datetime, WorkerRuntimeActivityState]] = []

    async def start_idempotently(
        self,
        *,
        runtime_id: UUID,
        now: datetime,
    ) -> WorkerRuntimeSnapshot:
        if self.runtime is None:
            self.runtime = WorkerRuntimeSnapshot(
                runtime_id=runtime_id,
                lifecycle_state=WorkerRuntimeLifecycleState.RUNNING,
                activity_state=WorkerRuntimeActivityState.IDLE,
                started_at=now,
                heartbeat_at=now,
            )
            self.events.append(
                WorkerRuntimeEvent(
                    event_id=len(self.events) + 1,
                    runtime_id=runtime_id,
                    event_type=WorkerRuntimeEventType.RUNTIME_STARTED,
                    occurred_at=now,
                )
            )
        return self.runtime

    async def get(
        self,
        runtime_id: UUID,
    ) -> WorkerRuntimeSnapshot | None:
        if self.runtime is None or self.runtime.runtime_id != runtime_id:
            return None
        return self.runtime

    async def heartbeat(
        self,
        *,
        runtime_id: UUID,
        now: datetime,
        activity_state: WorkerRuntimeActivityState,
    ) -> WorkerRuntimeSnapshot:
        current = self._runtime(runtime_id)
        self.heartbeats.append((now, activity_state))
        self.runtime = replace(
            current,
            activity_state=activity_state,
            heartbeat_at=now,
        )
        return self.runtime

    async def stop(
        self,
        *,
        runtime_id: UUID,
        now: datetime,
    ) -> WorkerRuntimeSnapshot:
        current = self._runtime(runtime_id)
        self.runtime = replace(
            current,
            lifecycle_state=WorkerRuntimeLifecycleState.STOPPED,
            activity_state=WorkerRuntimeActivityState.IDLE,
            heartbeat_at=now,
            stopped_at=now,
        )
        self.events.append(
            WorkerRuntimeEvent(
                event_id=len(self.events) + 1,
                runtime_id=runtime_id,
                event_type=WorkerRuntimeEventType.RUNTIME_STOPPED,
                occurred_at=now,
            )
        )
        return self.runtime

    async def fail(
        self,
        *,
        runtime_id: UUID,
        now: datetime,
        failure_code: WorkerRuntimeFailureCode,
    ) -> WorkerRuntimeSnapshot:
        current = self._runtime(runtime_id)
        self.runtime = replace(
            current,
            lifecycle_state=WorkerRuntimeLifecycleState.FAILED,
            activity_state=WorkerRuntimeActivityState.IDLE,
            heartbeat_at=now,
            stopped_at=now,
            failure_code=failure_code,
        )
        self.events.append(
            WorkerRuntimeEvent(
                event_id=len(self.events) + 1,
                runtime_id=runtime_id,
                event_type=WorkerRuntimeEventType.RUNTIME_FAILED,
                occurred_at=now,
            )
        )
        return self.runtime

    async def record_operation_settled(
        self,
        *,
        runtime_id: UUID,
        operation_id: UUID,
        operation_state: MarketOperationState,
        now: datetime,
    ) -> WorkerRuntimeEvent:
        self._runtime(runtime_id)
        event = WorkerRuntimeEvent(
            event_id=len(self.events) + 1,
            runtime_id=runtime_id,
            operation_id=operation_id,
            event_type=WorkerRuntimeEventType.OPERATION_SETTLED,
            operation_state=operation_state,
            occurred_at=now,
        )
        self.events.append(event)
        return event

    async def list_recent_runtimes(
        self,
        *,
        limit: int,
    ) -> tuple[WorkerRuntimeSnapshot, ...]:
        if self.runtime is None or limit <= 0:
            return ()
        return (self.runtime,)

    async def list_recent_events(
        self,
        *,
        limit: int,
    ) -> tuple[WorkerRuntimeEvent, ...]:
        if limit <= 0:
            return ()
        return tuple(reversed(self.events[-limit:]))

    def _runtime(self, runtime_id: UUID) -> WorkerRuntimeSnapshot:
        runtime = self.runtime
        if runtime is None or runtime.runtime_id != runtime_id:
            raise AssertionError("runtime was not started")
        return runtime


async def _wait_until(predicate: Callable[[], bool]) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached")


async def test_start_and_activity_transitions_are_serialized() -> None:
    repository = SpyRepository()
    clock = MutableClock(BASE_TIME)
    runtime_id = uuid4()

    session = WorkerRuntimePresenceSession(
        repository=repository,
        runtime_id=runtime_id,
        clock=clock,
    )

    started = await session.start()

    assert started.runtime_id == runtime_id
    assert started.activity_state is WorkerRuntimeActivityState.IDLE

    clock.advance(timedelta(seconds=1))
    active = await session.set_activity(WorkerRuntimeActivityState.ACTIVE)

    assert active.activity_state is WorkerRuntimeActivityState.ACTIVE
    assert active.heartbeat_at == clock.value
    assert session.activity_state is WorkerRuntimeActivityState.ACTIVE

    clock.advance(timedelta(seconds=1))
    idle = await session.set_activity(WorkerRuntimeActivityState.IDLE)

    assert idle.activity_state is WorkerRuntimeActivityState.IDLE
    assert idle.heartbeat_at == clock.value

    with pytest.raises(RuntimeError, match="already started"):
        await session.start()


async def test_background_heartbeat_is_independent_of_poll_activity() -> None:
    repository = SpyRepository()
    clock = MutableClock(BASE_TIME)
    sleeper = ControlledSleeper()

    session = WorkerRuntimePresenceSession(
        repository=repository,
        runtime_id=uuid4(),
        clock=clock,
        sleeper=sleeper,
    )

    await session.start()

    heartbeat_task = asyncio.create_task(session.heartbeat_forever())

    await asyncio.wait_for(sleeper.entered.wait(), timeout=1)

    clock.advance(timedelta(seconds=1))
    await session.set_activity(WorkerRuntimeActivityState.ACTIVE)

    clock.advance(timedelta(seconds=30))
    sleeper.release.set()

    await asyncio.wait_for(
        _wait_until(lambda: len(repository.heartbeats) >= 2),
        timeout=1,
    )

    assert sleeper.calls[0] == (WORKER_RUNTIME_OBSERVABILITY_HEARTBEAT_INTERVAL_SECONDS)
    assert repository.heartbeats[-1] == (
        clock.value,
        WorkerRuntimeActivityState.ACTIVE,
    )

    assert [event.event_type for event in repository.events] == [
        WorkerRuntimeEventType.RUNTIME_STARTED
    ]

    heartbeat_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await heartbeat_task


async def test_operation_settlement_uses_closed_runtime_context() -> None:
    repository = SpyRepository()
    clock = MutableClock(BASE_TIME)
    runtime_id = uuid4()
    operation_id = uuid4()

    session = WorkerRuntimePresenceSession(
        repository=repository,
        runtime_id=runtime_id,
        clock=clock,
    )

    await session.start()
    clock.advance(timedelta(seconds=5))

    event = await session.record_operation_settled(
        operation_id=operation_id,
        operation_state=MarketOperationState.COMPLETED,
    )

    assert event.runtime_id == runtime_id
    assert event.operation_id == operation_id
    assert event.operation_state is MarketOperationState.COMPLETED
    assert event.occurred_at == clock.value


async def test_stop_marks_confirmed_graceful_terminal_state() -> None:
    repository = SpyRepository()
    clock = MutableClock(BASE_TIME)

    session = WorkerRuntimePresenceSession(
        repository=repository,
        runtime_id=uuid4(),
        clock=clock,
    )

    await session.start()
    clock.advance(timedelta(seconds=10))

    stopped = await session.stop()

    assert stopped.lifecycle_state is WorkerRuntimeLifecycleState.STOPPED
    assert stopped.activity_state is WorkerRuntimeActivityState.IDLE
    assert stopped.stopped_at == clock.value

    with pytest.raises(RuntimeError, match="terminal"):
        await session.set_activity(WorkerRuntimeActivityState.ACTIVE)


async def test_fail_marks_confirmed_sanitized_terminal_failure() -> None:
    repository = SpyRepository()
    clock = MutableClock(BASE_TIME)

    session = WorkerRuntimePresenceSession(
        repository=repository,
        runtime_id=uuid4(),
        clock=clock,
    )

    await session.start()
    clock.advance(timedelta(seconds=10))

    failed = await session.fail(WorkerRuntimeFailureCode.LOCAL_STATE_FAILURE)

    assert failed.lifecycle_state is WorkerRuntimeLifecycleState.FAILED
    assert failed.failure_code is WorkerRuntimeFailureCode.LOCAL_STATE_FAILURE
    assert failed.stopped_at == clock.value


@pytest.mark.parametrize(
    "interval_seconds",
    (
        0.0,
        -1.0,
        float("nan"),
        float("inf"),
    ),
)
def test_presence_session_rejects_invalid_heartbeat_interval(
    interval_seconds: float,
) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        WorkerRuntimePresenceSession(
            repository=SpyRepository(),
            runtime_id=uuid4(),
            clock=MutableClock(BASE_TIME),
            heartbeat_interval_seconds=interval_seconds,
        )
