"""Read-service tests for worker runtime observability."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.market_data.errors import (
    InvalidWorkerRuntimeObservabilityError,
)
from app.market_data.operations import MarketOperationState
from app.market_data.worker_observability import (
    WorkerRuntimeActivityState,
    WorkerRuntimeEvent,
    WorkerRuntimeEventType,
    WorkerRuntimeFailureCode,
    WorkerRuntimeLifecycleState,
    WorkerRuntimeSnapshot,
)
from app.services.worker_observability import (
    WORKER_RUNTIME_STALE_AFTER_SECONDS,
    WorkerRuntimeHealthState,
    WorkerRuntimeObservabilityService,
)

BASE_TIME = datetime(2026, 8, 20, 20, 0, tzinfo=UTC)


class SpyRepository:
    def __init__(
        self,
        *,
        runtimes: tuple[WorkerRuntimeSnapshot, ...] = (),
        events: tuple[WorkerRuntimeEvent, ...] = (),
    ) -> None:
        self.runtimes = runtimes
        self.events = events
        self.runtime_limits: list[int] = []
        self.event_limits: list[int] = []

    async def start_idempotently(
        self,
        *,
        runtime_id: UUID,
        now: datetime,
    ) -> WorkerRuntimeSnapshot:
        raise AssertionError("read service attempted to start a runtime")

    async def get(
        self,
        runtime_id: UUID,
    ) -> WorkerRuntimeSnapshot | None:
        raise AssertionError("read service attempted an individual runtime lookup")

    async def heartbeat(
        self,
        *,
        runtime_id: UUID,
        now: datetime,
        activity_state: WorkerRuntimeActivityState,
    ) -> WorkerRuntimeSnapshot:
        raise AssertionError("read service attempted a heartbeat")

    async def stop(
        self,
        *,
        runtime_id: UUID,
        now: datetime,
    ) -> WorkerRuntimeSnapshot:
        raise AssertionError("read service attempted to stop a runtime")

    async def fail(
        self,
        *,
        runtime_id: UUID,
        now: datetime,
        failure_code: WorkerRuntimeFailureCode,
    ) -> WorkerRuntimeSnapshot:
        raise AssertionError("read service attempted to fail a runtime")

    async def record_operation_settled(
        self,
        *,
        runtime_id: UUID,
        operation_id: UUID,
        operation_state: MarketOperationState,
        now: datetime,
    ) -> WorkerRuntimeEvent:
        raise AssertionError("read service attempted to append an event")

    async def list_recent_runtimes(
        self,
        *,
        limit: int,
    ) -> tuple[WorkerRuntimeSnapshot, ...]:
        self.runtime_limits.append(limit)
        return self.runtimes

    async def list_recent_events(
        self,
        *,
        limit: int,
    ) -> tuple[WorkerRuntimeEvent, ...]:
        self.event_limits.append(limit)
        return self.events


def _running(
    *,
    heartbeat_at: datetime,
    activity_state: WorkerRuntimeActivityState = (WorkerRuntimeActivityState.IDLE),
) -> WorkerRuntimeSnapshot:
    return WorkerRuntimeSnapshot(
        runtime_id=uuid4(),
        lifecycle_state=WorkerRuntimeLifecycleState.RUNNING,
        activity_state=activity_state,
        started_at=heartbeat_at - timedelta(minutes=10),
        heartbeat_at=heartbeat_at,
    )


def _stopped() -> WorkerRuntimeSnapshot:
    heartbeat_at = BASE_TIME - timedelta(hours=2)
    stopped_at = heartbeat_at + timedelta(seconds=10)

    return WorkerRuntimeSnapshot(
        runtime_id=uuid4(),
        lifecycle_state=WorkerRuntimeLifecycleState.STOPPED,
        activity_state=WorkerRuntimeActivityState.IDLE,
        started_at=heartbeat_at - timedelta(hours=1),
        heartbeat_at=heartbeat_at,
        stopped_at=stopped_at,
    )


def _failed() -> WorkerRuntimeSnapshot:
    heartbeat_at = BASE_TIME - timedelta(hours=3)
    stopped_at = heartbeat_at + timedelta(seconds=15)

    return WorkerRuntimeSnapshot(
        runtime_id=uuid4(),
        lifecycle_state=WorkerRuntimeLifecycleState.FAILED,
        activity_state=WorkerRuntimeActivityState.IDLE,
        started_at=heartbeat_at - timedelta(hours=1),
        heartbeat_at=heartbeat_at,
        stopped_at=stopped_at,
        failure_code=WorkerRuntimeFailureCode.LOCAL_STATE_FAILURE,
    )


async def test_service_derives_health_without_collapsing_running_epochs() -> None:
    boundary = BASE_TIME - timedelta(seconds=WORKER_RUNTIME_STALE_AFTER_SECONDS)
    stale = boundary - timedelta(microseconds=1)

    repository = SpyRepository(
        runtimes=(
            _running(
                heartbeat_at=boundary,
                activity_state=WorkerRuntimeActivityState.ACTIVE,
            ),
            _running(heartbeat_at=stale),
            _stopped(),
            _failed(),
        )
    )
    service = WorkerRuntimeObservabilityService(
        repository=repository,
        clock=lambda: BASE_TIME,
    )

    result = await service.list_runtimes(limit=4)

    assert repository.runtime_limits == [4]
    assert result.observed_at == BASE_TIME
    assert result.stale_after_seconds == WORKER_RUNTIME_STALE_AFTER_SECONDS
    assert len(result.items) == 4

    assert result.items[0].health_state is (WorkerRuntimeHealthState.HEALTHY)
    assert result.items[0].lifecycle_state is (WorkerRuntimeLifecycleState.RUNNING)
    assert result.items[0].activity_state is (WorkerRuntimeActivityState.ACTIVE)

    assert result.items[1].health_state is (WorkerRuntimeHealthState.STALE)
    assert result.items[1].lifecycle_state is (WorkerRuntimeLifecycleState.RUNNING)

    assert result.items[2].health_state is (WorkerRuntimeHealthState.STOPPED)
    assert result.items[3].health_state is (WorkerRuntimeHealthState.FAILED)
    assert result.items[3].failure_code is (WorkerRuntimeFailureCode.LOCAL_STATE_FAILURE)

    assert all(not hasattr(item, "runtime_id") for item in result.items)


async def test_future_heartbeat_is_not_falsely_marked_stale() -> None:
    repository = SpyRepository(runtimes=(_running(heartbeat_at=BASE_TIME + timedelta(seconds=1)),))
    service = WorkerRuntimeObservabilityService(
        repository=repository,
        clock=lambda: BASE_TIME,
    )

    result = await service.list_runtimes(limit=1)

    assert result.items[0].health_state is (WorkerRuntimeHealthState.HEALTHY)


async def test_event_view_omits_internal_runtime_identity() -> None:
    runtime_id = uuid4()
    operation_id = uuid4()

    events = (
        WorkerRuntimeEvent(
            event_id=12,
            runtime_id=runtime_id,
            event_type=WorkerRuntimeEventType.OPERATION_SETTLED,
            occurred_at=BASE_TIME - timedelta(seconds=5),
            operation_id=operation_id,
            operation_state=MarketOperationState.COMPLETED,
        ),
        WorkerRuntimeEvent(
            event_id=11,
            runtime_id=runtime_id,
            event_type=WorkerRuntimeEventType.RUNTIME_STARTED,
            occurred_at=BASE_TIME - timedelta(minutes=1),
        ),
    )

    repository = SpyRepository(events=events)
    service = WorkerRuntimeObservabilityService(
        repository=repository,
        clock=lambda: BASE_TIME,
    )

    result = await service.list_events(limit=2)

    assert repository.event_limits == [2]
    assert result.observed_at == BASE_TIME
    assert len(result.items) == 2

    assert result.items[0].event_id == 12
    assert result.items[0].event_type is (WorkerRuntimeEventType.OPERATION_SETTLED)
    assert result.items[0].operation_id == operation_id
    assert result.items[0].operation_state is (MarketOperationState.COMPLETED)

    assert result.items[1].event_type is (WorkerRuntimeEventType.RUNTIME_STARTED)
    assert result.items[1].operation_id is None
    assert result.items[1].operation_state is None

    assert all(not hasattr(item, "runtime_id") for item in result.items)


@pytest.mark.parametrize("limit", [0, 101, True])
async def test_runtime_read_limit_is_bounded(
    limit: int,
) -> None:
    repository = SpyRepository()
    service = WorkerRuntimeObservabilityService(
        repository=repository,
        clock=lambda: BASE_TIME,
    )

    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        await service.list_runtimes(limit=limit)

    assert repository.runtime_limits == []


@pytest.mark.parametrize("limit", [0, 101, True])
async def test_event_read_limit_is_bounded(
    limit: int,
) -> None:
    repository = SpyRepository()
    service = WorkerRuntimeObservabilityService(
        repository=repository,
        clock=lambda: BASE_TIME,
    )

    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        await service.list_events(limit=limit)

    assert repository.event_limits == []


async def test_service_rejects_non_utc_observation_clock() -> None:
    repository = SpyRepository()

    service = WorkerRuntimeObservabilityService(
        repository=repository,
        clock=lambda: datetime(2026, 8, 20, 20, 0),
    )

    with pytest.raises(InvalidWorkerRuntimeObservabilityError):
        await service.list_runtimes(limit=1)

    assert repository.runtime_limits == [1]
