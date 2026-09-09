"""Gate 2E fenced worker tests for operational market-data collectors."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

import app.operational_market_data_collectors as collectors
from app.database import Database
from app.market_data.errors import MarketJobLockTimeoutError
from app.repositories.operational_market_data_collectors import (
    PostgresOperationalMarketDataCollectorRepository,
)
from app.services.operational_market_data_collector_worker import (
    CycleStartupHook,
    OperationalMarketDataCollectorCycleExecutor,
    OperationalMarketDataCollectorWorker,
    OperationalMarketDataCollectorWorkerPolicy,
)

NOW = datetime(2026, 9, 9, 18, 0, tzinfo=UTC)


def _specification() -> collectors.OperationalMarketDataCollectorSpecification:
    return collectors.OperationalMarketDataCollectorSpecification(
        schema_version=1,
        collector_contract_version=1,
        scope=collectors.OperationalMarketDataCollectorScope.BINANCE_SPOT_RAW,
        targets=(
            collectors.OperationalMarketDataCollectorTarget(
                symbol="BTC/USDT",
                timeframe="1m",
                bootstrap_candles=500,
            ),
            collectors.OperationalMarketDataCollectorTarget(
                symbol="ETH/USDT",
                timeframe="5m",
                bootstrap_candles=300,
            ),
        ),
        interval_seconds=60,
        overlap_candles=2,
    )


async def _start(
    repository: PostgresOperationalMarketDataCollectorRepository,
    actor: UUID,
    *,
    key: str,
    now: datetime = NOW,
) -> collectors.OperationalMarketDataCollectorEpoch:
    return await repository.start(
        _specification(),
        actor_id=actor,
        idempotency_key=key,
        now=now,
    )


def _command(
    epoch: collectors.OperationalMarketDataCollectorEpoch,
    command_type: collectors.OperationalMarketDataCollectorCommandType,
) -> collectors.OperationalMarketDataCollectorCommandIntent:
    return collectors.OperationalMarketDataCollectorCommandIntent(
        epoch_id=epoch.epoch_id,
        epoch_checksum=epoch.epoch_checksum,
        command_type=command_type,
        expected_record_version=epoch.record_version,
    )


@dataclass
class _ImmediateExecutor(OperationalMarketDataCollectorCycleExecutor):
    calls: int = 0
    hooks: int = 0
    executed: bool = True

    async def execute_cycle(
        self,
        specification: collectors.OperationalMarketDataCollectorSpecification,
        *,
        startup_hook: CycleStartupHook,
    ) -> bool:
        assert specification == _specification()
        self.calls += 1
        await startup_hook()
        self.hooks += 1
        return self.executed


class _BlockingExecutor:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def execute_cycle(
        self,
        specification: collectors.OperationalMarketDataCollectorSpecification,
        *,
        startup_hook: CycleStartupHook,
    ) -> bool:
        assert specification == _specification()
        self.calls += 1
        await startup_hook()
        self.entered.set()
        await self.release.wait()
        return True


class _BusyExecutor:
    async def execute_cycle(
        self,
        specification: collectors.OperationalMarketDataCollectorSpecification,
        *,
        startup_hook: CycleStartupHook,
    ) -> bool:
        await startup_hook()
        raise MarketJobLockTimeoutError()


class _HookBoundaryExecutor:
    def __init__(
        self,
        repository: PostgresOperationalMarketDataCollectorRepository,
        epoch_id: UUID,
        actor: UUID,
    ) -> None:
        self._repository = repository
        self._epoch_id = epoch_id
        self._actor = actor
        self.physical_started = False

    async def execute_cycle(
        self,
        specification: collectors.OperationalMarketDataCollectorSpecification,
        *,
        startup_hook: CycleStartupHook,
    ) -> bool:
        current = await self._repository.get(self._epoch_id)
        assert current is not None

        await self._repository.request_command(
            _command(
                current,
                collectors.OperationalMarketDataCollectorCommandType.PAUSE,
            ),
            actor_id=self._actor,
            idempotency_key="worker:hook-boundary:pause",
            now=NOW + timedelta(seconds=1),
        )

        await startup_hook()
        self.physical_started = True
        return True


@pytest.fixture
def repository(
    database: Database,
) -> PostgresOperationalMarketDataCollectorRepository:
    return PostgresOperationalMarketDataCollectorRepository(database)


@pytest.mark.asyncio
async def test_real_repository_claim_starting_running_and_one_cycle(
    repository: PostgresOperationalMarketDataCollectorRepository,
    auth_user_id: UUID,
) -> None:
    epoch = await _start(
        repository,
        auth_user_id,
        key="worker:real",
    )
    executor = _ImmediateExecutor()
    worker_id = uuid4()

    worker = OperationalMarketDataCollectorWorker(
        repository,
        executor,
        worker_id=worker_id,
        clock=lambda: NOW + timedelta(seconds=1),
    )

    result = await worker.run_epoch(
        epoch.epoch_id,
        max_cycles=1,
    )

    assert result.cycles_completed == 1
    assert result.exit_code is None
    assert (
        result.epoch.desired_state is collectors.OperationalMarketDataCollectorDesiredState.RUNNING
    )
    assert (
        result.epoch.observed_state
        is collectors.OperationalMarketDataCollectorObservedState.RUNNING
    )
    assert result.epoch.worker_claim is not None
    assert result.epoch.worker_claim.worker_id == worker_id
    assert result.epoch.worker_claim.fencing_token == 1
    assert executor.calls == 1
    assert executor.hooks == 1


@pytest.mark.asyncio
async def test_unclaimed_pause_settles_without_physical_cycle(
    repository: PostgresOperationalMarketDataCollectorRepository,
    auth_user_id: UUID,
) -> None:
    epoch = await _start(
        repository,
        auth_user_id,
        key="worker:unclaimed-pause",
    )

    await repository.request_command(
        _command(
            epoch,
            collectors.OperationalMarketDataCollectorCommandType.PAUSE,
        ),
        actor_id=auth_user_id,
        idempotency_key="worker:unclaimed-pause:command",
        now=NOW + timedelta(seconds=1),
    )

    executor = _ImmediateExecutor()

    result = await OperationalMarketDataCollectorWorker(
        repository,
        executor,
        clock=lambda: NOW + timedelta(seconds=2),
    ).run_epoch(epoch.epoch_id, max_cycles=1)

    assert result.cycles_completed == 0
    assert (
        result.epoch.desired_state is collectors.OperationalMarketDataCollectorDesiredState.PAUSED
    )
    assert (
        result.epoch.observed_state is collectors.OperationalMarketDataCollectorObservedState.PAUSED
    )
    assert result.epoch.worker_claim is None
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_expired_foreign_claim_recovers_with_higher_fence(
    repository: PostgresOperationalMarketDataCollectorRepository,
    auth_user_id: UUID,
) -> None:
    epoch = await _start(
        repository,
        auth_user_id,
        key="worker:recover",
    )

    foreign = uuid4()

    claimed = await repository.claim(
        epoch.epoch_id,
        expected_record_version=epoch.record_version,
        worker_id=foreign,
        lease_expires_at=NOW + timedelta(seconds=1),
        now=NOW,
    )

    assert claimed.worker_claim is not None
    assert claimed.worker_claim.fencing_token == 1

    executor = _ImmediateExecutor()
    worker_id = uuid4()

    result = await OperationalMarketDataCollectorWorker(
        repository,
        executor,
        worker_id=worker_id,
        clock=lambda: NOW + timedelta(seconds=2),
    ).run_epoch(epoch.epoch_id, max_cycles=1)

    assert result.exit_code is None
    assert result.cycles_completed == 1
    assert result.epoch.worker_claim is not None
    assert result.epoch.worker_claim.worker_id == worker_id
    assert result.epoch.worker_claim.fencing_token == 2
    assert (
        result.epoch.observed_state
        is collectors.OperationalMarketDataCollectorObservedState.RUNNING
    )


@pytest.mark.asyncio
async def test_expired_same_worker_identity_is_lease_lost_not_recovered(
    repository: PostgresOperationalMarketDataCollectorRepository,
    auth_user_id: UUID,
) -> None:
    epoch = await _start(
        repository,
        auth_user_id,
        key="worker:same-expired",
    )

    worker_id = uuid4()

    claimed = await repository.claim(
        epoch.epoch_id,
        expected_record_version=epoch.record_version,
        worker_id=worker_id,
        lease_expires_at=NOW + timedelta(seconds=1),
        now=NOW,
    )

    executor = _ImmediateExecutor()

    result = await OperationalMarketDataCollectorWorker(
        repository,
        executor,
        worker_id=worker_id,
        clock=lambda: NOW + timedelta(seconds=2),
    ).run_epoch(epoch.epoch_id, max_cycles=1)

    assert result.cycles_completed == 0
    assert result.exit_code is collectors.OperationalMarketDataCollectorFailureCode.LEASE_LOST
    assert result.epoch.worker_claim == claimed.worker_claim
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_pause_requested_during_cycle_settles_only_after_boundary(
    repository: PostgresOperationalMarketDataCollectorRepository,
    auth_user_id: UUID,
) -> None:
    epoch = await _start(
        repository,
        auth_user_id,
        key="worker:pause-cycle",
    )

    executor = _BlockingExecutor()
    worker = OperationalMarketDataCollectorWorker(
        repository,
        executor,
        clock=lambda: NOW + timedelta(seconds=2),
    )

    task = asyncio.create_task(
        worker.run_epoch(
            epoch.epoch_id,
            max_cycles=1,
        )
    )

    await asyncio.wait_for(
        executor.entered.wait(),
        timeout=2,
    )

    current = await repository.get(epoch.epoch_id)
    assert current is not None
    assert current.observed_state is collectors.OperationalMarketDataCollectorObservedState.RUNNING

    await repository.request_command(
        _command(
            current,
            collectors.OperationalMarketDataCollectorCommandType.PAUSE,
        ),
        actor_id=auth_user_id,
        idempotency_key="worker:pause-cycle:pause",
        now=NOW + timedelta(seconds=3),
    )

    executor.release.set()

    result = await asyncio.wait_for(
        task,
        timeout=2,
    )

    assert result.cycles_completed == 1
    assert (
        result.epoch.desired_state is collectors.OperationalMarketDataCollectorDesiredState.PAUSED
    )
    assert (
        result.epoch.observed_state is collectors.OperationalMarketDataCollectorObservedState.PAUSED
    )


@pytest.mark.asyncio
async def test_stop_requested_during_cycle_stops_only_after_boundary(
    repository: PostgresOperationalMarketDataCollectorRepository,
    auth_user_id: UUID,
) -> None:
    epoch = await _start(
        repository,
        auth_user_id,
        key="worker:stop-cycle",
    )

    executor = _BlockingExecutor()
    worker = OperationalMarketDataCollectorWorker(
        repository,
        executor,
        clock=lambda: NOW + timedelta(seconds=2),
    )

    task = asyncio.create_task(
        worker.run_epoch(
            epoch.epoch_id,
            max_cycles=1,
        )
    )

    await asyncio.wait_for(
        executor.entered.wait(),
        timeout=2,
    )

    current = await repository.get(epoch.epoch_id)
    assert current is not None

    await repository.request_command(
        _command(
            current,
            collectors.OperationalMarketDataCollectorCommandType.STOP,
        ),
        actor_id=auth_user_id,
        idempotency_key="worker:stop-cycle:stop",
        now=NOW + timedelta(seconds=3),
    )

    executor.release.set()

    result = await asyncio.wait_for(
        task,
        timeout=2,
    )

    assert result.cycles_completed == 1
    assert (
        result.epoch.desired_state is collectors.OperationalMarketDataCollectorDesiredState.STOPPED
    )
    assert (
        result.epoch.observed_state
        is collectors.OperationalMarketDataCollectorObservedState.STOPPED
    )
    assert result.epoch.worker_claim is None


@pytest.mark.asyncio
async def test_under_lock_hook_rechecks_desired_state_before_physical_work(
    repository: PostgresOperationalMarketDataCollectorRepository,
    auth_user_id: UUID,
) -> None:
    epoch = await _start(
        repository,
        auth_user_id,
        key="worker:hook-boundary",
    )

    executor = _HookBoundaryExecutor(
        repository,
        epoch.epoch_id,
        auth_user_id,
    )

    result = await OperationalMarketDataCollectorWorker(
        repository,
        executor,
        clock=lambda: NOW + timedelta(seconds=2),
    ).run_epoch(epoch.epoch_id, max_cycles=1)

    assert result.cycles_completed == 0
    assert executor.physical_started is False
    assert (
        result.epoch.desired_state is collectors.OperationalMarketDataCollectorDesiredState.PAUSED
    )
    assert (
        result.epoch.observed_state is collectors.OperationalMarketDataCollectorObservedState.PAUSED
    )


@pytest.mark.asyncio
async def test_local_collector_busy_fails_owned_epoch_with_closed_code(
    repository: PostgresOperationalMarketDataCollectorRepository,
    auth_user_id: UUID,
) -> None:
    epoch = await _start(
        repository,
        auth_user_id,
        key="worker:busy",
    )

    result = await OperationalMarketDataCollectorWorker(
        repository,
        _BusyExecutor(),
        clock=lambda: NOW + timedelta(seconds=1),
    ).run_epoch(epoch.epoch_id, max_cycles=1)

    assert result.cycles_completed == 0
    assert (
        result.exit_code
        is collectors.OperationalMarketDataCollectorFailureCode.LOCAL_COLLECTOR_BUSY
    )
    assert (
        result.epoch.observed_state is collectors.OperationalMarketDataCollectorObservedState.FAILED
    )
    assert result.epoch.failure is not None
    assert (
        result.epoch.failure.code
        is collectors.OperationalMarketDataCollectorFailureCode.LOCAL_COLLECTOR_BUSY
    )


@pytest.mark.asyncio
async def test_not_due_executor_returns_without_busy_spin_and_renews_lease(
    repository: PostgresOperationalMarketDataCollectorRepository,
    auth_user_id: UUID,
) -> None:
    epoch = await _start(
        repository,
        auth_user_id,
        key="worker:not-due",
    )

    executor = _ImmediateExecutor(
        executed=False,
    )

    tick = 0

    def clock() -> datetime:
        nonlocal tick
        tick += 1
        return NOW + timedelta(milliseconds=tick)

    result = await OperationalMarketDataCollectorWorker(
        repository,
        executor,
        clock=clock,
    ).run_epoch(epoch.epoch_id)

    assert result.cycles_completed == 0
    assert result.exit_code is None
    assert executor.calls == 1
    assert (
        result.epoch.desired_state is collectors.OperationalMarketDataCollectorDesiredState.RUNNING
    )
    assert (
        result.epoch.observed_state
        is collectors.OperationalMarketDataCollectorObservedState.RUNNING
    )
    assert result.epoch.worker_claim is not None
    assert result.epoch.worker_claim.heartbeat_at > result.epoch.worker_claim.claimed_at


@pytest.mark.asyncio
async def test_local_shutdown_does_not_forge_administrative_stop(
    repository: PostgresOperationalMarketDataCollectorRepository,
    auth_user_id: UUID,
) -> None:
    epoch = await _start(
        repository,
        auth_user_id,
        key="worker:local-stop",
    )

    executor = _ImmediateExecutor()
    worker = OperationalMarketDataCollectorWorker(
        repository,
        executor,
        clock=lambda: NOW + timedelta(seconds=1),
    )
    worker.request_stop()

    result = await worker.run_epoch(
        epoch.epoch_id,
        max_cycles=1,
    )

    latest = await repository.get(epoch.epoch_id)
    assert latest is not None

    assert result.cycles_completed == 0
    assert latest.desired_state is collectors.OperationalMarketDataCollectorDesiredState.RUNNING
    assert latest.observed_state is collectors.OperationalMarketDataCollectorObservedState.RUNNING
    assert latest.worker_claim is not None
    assert executor.calls == 0


@pytest.mark.parametrize(
    "lease,heartbeat",
    (
        (0.0, 1.0),
        (30.0, 0.0),
        (30.0, 30.0),
        (float("nan"), 1.0),
        (30.0, float("inf")),
        (True, 1.0),
    ),
)
def test_worker_policy_rejects_invalid_bounds(
    lease: float,
    heartbeat: float,
) -> None:
    with pytest.raises(ValueError):
        OperationalMarketDataCollectorWorkerPolicy(
            lease_duration_seconds=lease,
            heartbeat_interval_seconds=heartbeat,
        )


@pytest.mark.asyncio
async def test_run_rejects_invalid_cycle_bound(
    repository: PostgresOperationalMarketDataCollectorRepository,
) -> None:
    worker = OperationalMarketDataCollectorWorker(
        repository,
        _ImmediateExecutor(),
    )

    for invalid in (
        0,
        -1,
        True,
    ):
        with pytest.raises(ValueError):
            await worker.run_epoch(
                uuid4(),
                max_cycles=invalid,
            )


@pytest.mark.asyncio
async def test_heartbeat_renews_claim_during_slow_cycle(
    repository: PostgresOperationalMarketDataCollectorRepository,
    auth_user_id: UUID,
) -> None:
    epoch = await _start(
        repository,
        auth_user_id,
        key="worker:heartbeat",
    )

    executor = _BlockingExecutor()

    current_time = [NOW + timedelta(seconds=1)]

    heartbeat_waiting = asyncio.Event()
    release_heartbeat = asyncio.Event()
    sleep_calls = 0

    async def sleeper(delay: float) -> None:
        nonlocal sleep_calls

        assert delay == 10.0

        sleep_calls += 1

        if sleep_calls == 1:
            heartbeat_waiting.set()
            await release_heartbeat.wait()
            return

        await asyncio.Event().wait()

    worker = OperationalMarketDataCollectorWorker(
        repository,
        executor,
        clock=lambda: current_time[0],
        sleeper=sleeper,
    )

    task = asyncio.create_task(
        worker.run_epoch(
            epoch.epoch_id,
            max_cycles=1,
        )
    )

    await asyncio.wait_for(
        executor.entered.wait(),
        timeout=2,
    )

    await asyncio.wait_for(
        heartbeat_waiting.wait(),
        timeout=2,
    )

    before = await repository.get(epoch.epoch_id)
    assert before is not None
    assert before.worker_claim is not None

    previous_heartbeat = before.worker_claim.heartbeat_at
    previous_expiry = before.worker_claim.lease_expires_at

    current_time[0] = NOW + timedelta(seconds=5)
    release_heartbeat.set()

    renewed = None

    for _attempt in range(100):
        await asyncio.sleep(0.01)

        candidate = await repository.get(epoch.epoch_id)

        assert candidate is not None
        assert candidate.worker_claim is not None

        if candidate.worker_claim.heartbeat_at > previous_heartbeat:
            renewed = candidate
            break

    assert renewed is not None
    assert renewed.worker_claim is not None
    assert renewed.worker_claim.heartbeat_at == current_time[0]
    assert renewed.worker_claim.lease_expires_at > previous_expiry

    executor.release.set()

    result = await asyncio.wait_for(
        task,
        timeout=2,
    )

    assert result.cycles_completed == 1
    assert result.exit_code is None


@pytest.mark.asyncio
async def test_fence_loss_inside_startup_hook_prevents_physical_start(
    repository: PostgresOperationalMarketDataCollectorRepository,
    auth_user_id: UUID,
) -> None:
    epoch = await _start(
        repository,
        auth_user_id,
        key="worker:under-lock-fence-loss",
    )

    foreign_worker_id = uuid4()

    class FenceLossExecutor:
        def __init__(self) -> None:
            self.physical_started = False
            self.recovered_fence: int | None = None

        async def execute_cycle(
            self,
            specification: collectors.OperationalMarketDataCollectorSpecification,
            *,
            startup_hook: CycleStartupHook,
        ) -> bool:
            assert specification == _specification()

            current = await repository.get(epoch.epoch_id)

            assert current is not None
            assert current.worker_claim is not None

            old_fence = current.worker_claim.fencing_token

            recovered = await repository.recover(
                epoch.epoch_id,
                expected_record_version=current.record_version,
                worker_id=foreign_worker_id,
                lease_expires_at=NOW + timedelta(seconds=40),
                now=NOW + timedelta(seconds=2),
            )

            assert recovered.worker_claim is not None
            assert recovered.worker_claim.fencing_token > old_fence

            self.recovered_fence = recovered.worker_claim.fencing_token

            # This is the hook that the real runtime adapter
            # executes after acquiring the local collection flock.
            # It must reject the now-stale original worker.
            await startup_hook()

            self.physical_started = True
            return True

    executor = FenceLossExecutor()

    worker = OperationalMarketDataCollectorWorker(
        repository,
        executor,
        policy=OperationalMarketDataCollectorWorkerPolicy(
            lease_duration_seconds=1.0,
            heartbeat_interval_seconds=0.5,
        ),
        clock=lambda: NOW,
    )

    result = await worker.run_epoch(
        epoch.epoch_id,
        max_cycles=1,
    )

    assert result.cycles_completed == 0
    assert result.exit_code is collectors.OperationalMarketDataCollectorFailureCode.LEASE_LOST
    assert executor.physical_started is False

    latest = await repository.get(epoch.epoch_id)

    assert latest is not None
    assert latest.worker_claim is not None
    assert latest.worker_claim.worker_id == foreign_worker_id
    assert latest.worker_claim.fencing_token == executor.recovered_fence


@pytest.mark.asyncio
async def test_repeated_not_due_polls_renew_same_claim_without_new_fence(
    repository: PostgresOperationalMarketDataCollectorRepository,
    auth_user_id: UUID,
) -> None:
    epoch = await _start(
        repository,
        auth_user_id,
        key="worker:not-due-repeat",
    )

    executor = _ImmediateExecutor(
        executed=False,
    )

    tick = 0

    def clock() -> datetime:
        nonlocal tick
        tick += 1
        return NOW + timedelta(milliseconds=tick)

    worker = OperationalMarketDataCollectorWorker(
        repository,
        executor,
        clock=clock,
    )

    first = await worker.run_epoch(
        epoch.epoch_id,
        max_cycles=1,
    )

    assert first.epoch.worker_claim is not None

    first_claim = first.epoch.worker_claim

    second = await worker.run_epoch(
        epoch.epoch_id,
        max_cycles=1,
    )

    assert second.epoch.worker_claim is not None

    second_claim = second.epoch.worker_claim

    assert first.cycles_completed == 0
    assert second.cycles_completed == 0
    assert first.exit_code is None
    assert second.exit_code is None

    assert second_claim.worker_id == first_claim.worker_id
    assert second_claim.fencing_token == first_claim.fencing_token
    assert second_claim.heartbeat_at > first_claim.heartbeat_at
    assert second_claim.lease_expires_at > first_claim.lease_expires_at

    assert executor.calls == 2
