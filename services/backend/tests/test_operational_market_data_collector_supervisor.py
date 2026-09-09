"""Gate 2E supervisor tests for operational market-data collectors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest

import app.operational_market_data_collectors as collectors
from app.database import Database
from app.repositories.operational_market_data_collectors import (
    PostgresOperationalMarketDataCollectorRepository,
)
from app.services.operational_market_data_collector_supervisor import (
    OperationalMarketDataCollectorSupervisor,
    OperationalMarketDataCollectorSupervisorPolicy,
)
from app.services.operational_market_data_collector_worker import (
    CycleStartupHook,
    OperationalMarketDataCollectorCycleExecutor,
    OperationalMarketDataCollectorWorker,
    OperationalMarketDataCollectorWorkerResult,
)

NOW = datetime(
    2026,
    9,
    9,
    19,
    0,
    tzinfo=UTC,
)


@dataclass(frozen=True)
class _EpochReference:
    epoch_id: UUID


class _FakeRepository:
    def __init__(
        self,
        pages: dict[
            int,
            list[_EpochReference],
        ],
    ) -> None:
        self.pages = pages
        self.calls: list[tuple[int, int]] = []

    async def list_nonterminal(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[collectors.OperationalMarketDataCollectorEpoch]:
        self.calls.append(
            (
                limit,
                offset,
            )
        )

        return cast(
            list[collectors.OperationalMarketDataCollectorEpoch],
            list(
                self.pages.get(
                    offset,
                    [],
                )
            ),
        )


class _FakeWorker:
    def __init__(
        self,
        *,
        cycles_per_call: int = 1,
    ) -> None:
        self.cycles_per_call = cycles_per_call
        self.calls: list[tuple[UUID, int | None]] = []
        self.stop_calls = 0

    def request_stop(self) -> None:
        self.stop_calls += 1

    async def run_epoch(
        self,
        epoch_id: UUID,
        *,
        max_cycles: int | None = None,
    ) -> OperationalMarketDataCollectorWorkerResult:
        self.calls.append(
            (
                epoch_id,
                max_cycles,
            )
        )

        epoch = cast(
            collectors.OperationalMarketDataCollectorEpoch,
            _EpochReference(epoch_id),
        )

        return OperationalMarketDataCollectorWorkerResult(
            epoch=epoch,
            cycles_completed=self.cycles_per_call,
        )


def _supervisor(
    repository: _FakeRepository,
    worker: _FakeWorker,
    *,
    page_size: int = 100,
    sleeper=None,
) -> OperationalMarketDataCollectorSupervisor:
    kwargs = {}

    if sleeper is not None:
        kwargs["sleeper"] = sleeper

    return OperationalMarketDataCollectorSupervisor(
        cast(
            PostgresOperationalMarketDataCollectorRepository,
            repository,
        ),
        cast(
            OperationalMarketDataCollectorWorker,
            worker,
        ),
        policy=OperationalMarketDataCollectorSupervisorPolicy(
            poll_interval_seconds=0.01,
            page_size=page_size,
        ),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_poll_once_processes_each_epoch_with_one_cycle_bound() -> None:
    first = _EpochReference(uuid4())
    second = _EpochReference(uuid4())

    repository = _FakeRepository(
        {
            0: [
                first,
                second,
            ],
        }
    )
    worker = _FakeWorker()

    supervisor = _supervisor(
        repository,
        worker,
    )

    result = await supervisor.poll_once()

    assert repository.calls == [
        (
            100,
            0,
        )
    ]

    assert worker.calls == [
        (
            first.epoch_id,
            1,
        ),
        (
            second.epoch_id,
            1,
        ),
    ]

    assert result.epochs_discovered == 2
    assert result.epochs_processed == 2
    assert result.cycles_completed == 2
    assert result.next_offset == 0
    assert result.last_worker_result is not None
    assert result.last_worker_result.epoch.epoch_id == second.epoch_id


@pytest.mark.asyncio
async def test_supervisor_round_robin_wraps_after_short_page() -> None:
    first = _EpochReference(uuid4())
    second = _EpochReference(uuid4())
    third = _EpochReference(uuid4())

    repository = _FakeRepository(
        {
            0: [
                first,
                second,
            ],
            2: [
                third,
            ],
        }
    )
    worker = _FakeWorker()

    supervisor = _supervisor(
        repository,
        worker,
        page_size=2,
    )

    first_poll = await supervisor.poll_once()
    second_poll = await supervisor.poll_once()
    third_poll = await supervisor.poll_once()

    assert first_poll.next_offset == 2
    assert second_poll.next_offset == 0
    assert third_poll.next_offset == 2

    assert repository.calls == [
        (
            2,
            0,
        ),
        (
            2,
            2,
        ),
        (
            2,
            0,
        ),
    ]

    assert worker.calls == [
        (
            first.epoch_id,
            1,
        ),
        (
            second.epoch_id,
            1,
        ),
        (
            third.epoch_id,
            1,
        ),
        (
            first.epoch_id,
            1,
        ),
        (
            second.epoch_id,
            1,
        ),
    ]


@pytest.mark.asyncio
async def test_empty_page_wraps_to_zero() -> None:
    first = _EpochReference(uuid4())
    second = _EpochReference(uuid4())

    repository = _FakeRepository(
        {
            0: [
                first,
                second,
            ],
            2: [],
        }
    )
    worker = _FakeWorker()

    supervisor = _supervisor(
        repository,
        worker,
        page_size=2,
    )

    first_poll = await supervisor.poll_once()
    empty_poll = await supervisor.poll_once()
    wrapped_poll = await supervisor.poll_once()

    assert first_poll.next_offset == 2
    assert empty_poll.epochs_discovered == 0
    assert empty_poll.epochs_processed == 0
    assert empty_poll.next_offset == 0
    assert wrapped_poll.next_offset == 2

    assert repository.calls == [
        (
            2,
            0,
        ),
        (
            2,
            2,
        ),
        (
            2,
            0,
        ),
    ]


@pytest.mark.asyncio
async def test_run_is_bounded_and_sleeps_between_polls() -> None:
    repository = _FakeRepository(
        {
            0: [],
        }
    )
    worker = _FakeWorker()
    sleeps: list[float] = []

    async def sleeper(
        delay: float,
    ) -> None:
        sleeps.append(delay)

    supervisor = _supervisor(
        repository,
        worker,
        sleeper=sleeper,
    )

    result = await supervisor.run(
        max_polls=3,
    )

    assert result.polls_completed == 3
    assert result.epochs_processed == 0
    assert result.cycles_completed == 0
    assert len(repository.calls) == 3
    assert sleeps == [
        0.01,
        0.01,
    ]


@pytest.mark.asyncio
async def test_request_stop_propagates_and_prevents_new_poll() -> None:
    repository = _FakeRepository(
        {
            0: [
                _EpochReference(uuid4()),
            ],
        }
    )
    worker = _FakeWorker()

    supervisor = _supervisor(
        repository,
        worker,
    )

    supervisor.request_stop()

    result = await supervisor.run(
        max_polls=1,
    )

    assert worker.stop_calls == 1
    assert worker.calls == []
    assert repository.calls == []

    assert result.polls_completed == 0
    assert result.epochs_processed == 0
    assert result.cycles_completed == 0


@pytest.mark.parametrize(
    (
        "poll_interval_seconds",
        "page_size",
    ),
    (
        (
            0.0,
            100,
        ),
        (
            -1.0,
            100,
        ),
        (
            float("nan"),
            100,
        ),
        (
            float("inf"),
            100,
        ),
        (
            True,
            100,
        ),
        (
            1.0,
            0,
        ),
        (
            1.0,
            101,
        ),
        (
            1.0,
            True,
        ),
    ),
)
def test_supervisor_policy_is_bounded(
    poll_interval_seconds: float,
    page_size: int,
) -> None:
    with pytest.raises(ValueError):
        OperationalMarketDataCollectorSupervisorPolicy(
            poll_interval_seconds=poll_interval_seconds,
            page_size=page_size,
        )


@pytest.mark.asyncio
async def test_run_rejects_invalid_max_polls() -> None:
    repository = _FakeRepository({})
    worker = _FakeWorker()

    supervisor = _supervisor(
        repository,
        worker,
    )

    for invalid in (
        0,
        -1,
        True,
    ):
        with pytest.raises(ValueError):
            await supervisor.run(
                max_polls=invalid,
            )


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
        ),
        interval_seconds=60,
        overlap_candles=2,
    )


class _RealCycleExecutor(OperationalMarketDataCollectorCycleExecutor):
    def __init__(self) -> None:
        self.calls = 0

    async def execute_cycle(
        self,
        specification: (collectors.OperationalMarketDataCollectorSpecification),
        *,
        startup_hook: CycleStartupHook,
    ) -> bool:
        assert specification == _specification()

        await startup_hook()

        self.calls += 1

        return True


@pytest.mark.asyncio
async def test_real_postgres_discovery_delivers_pending_epoch_once(
    database: Database,
    auth_user_id: UUID,
) -> None:
    repository = PostgresOperationalMarketDataCollectorRepository(database)

    epoch = await repository.start(
        _specification(),
        actor_id=auth_user_id,
        idempotency_key=("collector-supervisor:real-discovery"),
        now=NOW,
    )

    cycle_executor = _RealCycleExecutor()

    worker = OperationalMarketDataCollectorWorker(
        repository,
        cycle_executor,
        worker_id=uuid4(),
        clock=lambda: NOW + timedelta(seconds=1),
    )

    supervisor = OperationalMarketDataCollectorSupervisor(
        repository,
        worker,
        policy=OperationalMarketDataCollectorSupervisorPolicy(
            poll_interval_seconds=0.01,
            page_size=100,
        ),
    )

    result = await supervisor.poll_once()

    assert result.epochs_discovered == 1
    assert result.epochs_processed == 1
    assert result.cycles_completed == 1

    assert result.last_worker_result is not None
    assert result.last_worker_result.epoch.epoch_id == epoch.epoch_id

    assert cycle_executor.calls == 1

    latest = await repository.get(epoch.epoch_id)

    assert latest is not None
    assert latest.observed_state is collectors.OperationalMarketDataCollectorObservedState.RUNNING
    assert latest.worker_claim is not None
