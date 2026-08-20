"""Runtime coordination for persistent worker observability."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Final
from uuid import UUID

from app.market_data.operations import MarketOperationState
from app.market_data.worker_observability import (
    WorkerRuntimeActivityState,
    WorkerRuntimeEvent,
    WorkerRuntimeFailureCode,
    WorkerRuntimeSnapshot,
)
from app.market_data.worker_observability_ports import (
    WorkerRuntimeObservabilityRepository,
)

WorkerRuntimeObservabilityClock = Callable[[], datetime]
WorkerRuntimeObservabilitySleeper = Callable[[float], Awaitable[None]]

WORKER_RUNTIME_OBSERVABILITY_HEARTBEAT_INTERVAL_SECONDS: Final = 30.0


def _validate_heartbeat_interval(interval_seconds: float) -> None:
    if not math.isfinite(interval_seconds) or interval_seconds <= 0:
        raise ValueError("worker observability heartbeat interval must be finite and positive")


class WorkerRuntimePresenceSession:
    """Serialize presence writes for one persistent worker runtime epoch."""

    def __init__(
        self,
        *,
        repository: WorkerRuntimeObservabilityRepository,
        runtime_id: UUID,
        clock: WorkerRuntimeObservabilityClock,
        heartbeat_interval_seconds: float = (
            WORKER_RUNTIME_OBSERVABILITY_HEARTBEAT_INTERVAL_SECONDS
        ),
        sleeper: WorkerRuntimeObservabilitySleeper = asyncio.sleep,
    ) -> None:
        _validate_heartbeat_interval(heartbeat_interval_seconds)

        self._repository = repository
        self._runtime_id = runtime_id
        self._clock = clock
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._sleeper = sleeper
        self._lock = asyncio.Lock()
        self._activity_state = WorkerRuntimeActivityState.IDLE
        self._started = False
        self._terminal = False

    @property
    def runtime_id(self) -> UUID:
        """Return the internal runtime epoch identifier."""

        return self._runtime_id

    @property
    def activity_state(self) -> WorkerRuntimeActivityState:
        """Return the latest locally coordinated coarse activity."""

        return self._activity_state

    async def start(self) -> WorkerRuntimeSnapshot:
        """Persist the runtime epoch before background observation begins."""

        async with self._lock:
            if self._started:
                raise RuntimeError("worker runtime presence session already started")

            runtime = await self._repository.start_idempotently(
                runtime_id=self._runtime_id,
                now=self._clock(),
            )

            self._activity_state = runtime.activity_state
            self._started = True
            return runtime

    async def heartbeat_forever(self) -> None:
        """Persist heartbeat independently of worker polls and idle sleeps."""

        self._require_open()

        while True:
            await self._sleeper(self._heartbeat_interval_seconds)

            async with self._lock:
                if self._terminal:
                    return

                self._require_open()

                runtime = await self._repository.heartbeat(
                    runtime_id=self._runtime_id,
                    now=self._clock(),
                    activity_state=self._activity_state,
                )
                self._activity_state = runtime.activity_state

    async def set_activity(
        self,
        activity_state: WorkerRuntimeActivityState,
    ) -> WorkerRuntimeSnapshot:
        """Persist one ACTIVE/IDLE transition with an authoritative heartbeat."""

        if not isinstance(activity_state, WorkerRuntimeActivityState):
            raise TypeError("activity_state must be WorkerRuntimeActivityState")

        async with self._lock:
            self._require_open()

            runtime = await self._repository.heartbeat(
                runtime_id=self._runtime_id,
                now=self._clock(),
                activity_state=activity_state,
            )
            self._activity_state = runtime.activity_state
            return runtime

    async def record_operation_settled(
        self,
        *,
        operation_id: UUID,
        operation_state: MarketOperationState,
    ) -> WorkerRuntimeEvent:
        """Append one sanitized settlement event for this runtime epoch."""

        async with self._lock:
            self._require_open()

            return await self._repository.record_operation_settled(
                runtime_id=self._runtime_id,
                operation_id=operation_id,
                operation_state=operation_state,
                now=self._clock(),
            )

    async def stop(self) -> WorkerRuntimeSnapshot:
        """Persist one confirmed graceful/known runtime termination."""

        async with self._lock:
            self._require_open()

            runtime = await self._repository.stop(
                runtime_id=self._runtime_id,
                now=self._clock(),
            )

            self._activity_state = runtime.activity_state
            self._terminal = True
            return runtime

    async def fail(
        self,
        failure_code: WorkerRuntimeFailureCode,
    ) -> WorkerRuntimeSnapshot:
        """Persist one confirmed sanitized runtime failure."""

        if not isinstance(failure_code, WorkerRuntimeFailureCode):
            raise TypeError("failure_code must be WorkerRuntimeFailureCode")

        async with self._lock:
            self._require_open()

            runtime = await self._repository.fail(
                runtime_id=self._runtime_id,
                now=self._clock(),
                failure_code=failure_code,
            )

            self._activity_state = runtime.activity_state
            self._terminal = True
            return runtime

    def _require_open(self) -> None:
        if not self._started:
            raise RuntimeError("worker runtime presence session is not started")
        if self._terminal:
            raise RuntimeError("worker runtime presence session is terminal")
