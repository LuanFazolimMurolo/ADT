"""Gate 2E persistent supervisor behavior."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast
from uuid import UUID, uuid4

import psycopg
import pytest

import app.operational_paper_session_runs as runs
from app.database import Database
from app.repositories.operational_paper_session_runs import (
    PostgresOperationalPaperSessionRunRepository,
)
from app.services.operational_paper_session_run_supervisor import (
    OperationalPaperSessionRunSupervisor,
    OperationalPaperSessionRunSupervisorPolicy,
)
from app.services.operational_paper_session_run_worker import (
    OperationalPaperSessionRunWorker,
    OperationalPaperSessionRunWorkerResult,
)
from tests.test_operational_paper_session_activations_migration import (
    _insert as _insert_activation,
)
from tests.test_operational_paper_session_activations_migration import (
    _row as _activation_row,
)
from tests.test_operational_paper_session_activations_migration import (
    _valid_activation,
)
from tests.test_operational_paper_session_runs_migration import START_AT


@dataclass(frozen=True)
class _Epoch:
    epoch_id: UUID


@dataclass(frozen=True)
class _WorkerEpoch:
    epoch_id: UUID


class _Repository:
    def __init__(
        self,
        pages: dict[int, list[_Epoch]],
    ) -> None:
        self.pages = pages
        self.calls: list[tuple[int, int]] = []

    async def list_nonterminal(
        self,
        *,
        limit: int,
        offset: int,
    ) -> list[_Epoch]:
        self.calls.append((limit, offset))
        return list(self.pages.get(offset, []))


class _Worker:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, int | None]] = []
        self.stop_requested = False

    async def run_epoch(
        self,
        epoch_id: UUID,
        *,
        max_cycles: int | None = None,
    ) -> OperationalPaperSessionRunWorkerResult:
        self.calls.append((epoch_id, max_cycles))

        return OperationalPaperSessionRunWorkerResult(
            cast(
                runs.OperationalPaperSessionRunEpoch,
                _WorkerEpoch(epoch_id),
            ),
            cycles_completed=1,
        )

    def request_stop(self) -> None:
        self.stop_requested = True


class _BlockingWorker(_Worker):
    def __init__(
        self,
        *,
        expected_starts: int,
    ) -> None:
        super().__init__()
        self._expected_starts = expected_starts
        self.started: list[UUID] = []
        self.all_started = asyncio.Event()
        self.release = asyncio.Event()

    async def run_epoch(
        self,
        epoch_id: UUID,
        *,
        max_cycles: int | None = None,
    ) -> OperationalPaperSessionRunWorkerResult:
        self.calls.append(
            (
                epoch_id,
                max_cycles,
            )
        )
        self.started.append(epoch_id)

        if len(self.started) == self._expected_starts:
            self.all_started.set()

        await self.release.wait()

        return OperationalPaperSessionRunWorkerResult(
            cast(
                runs.OperationalPaperSessionRunEpoch,
                _WorkerEpoch(epoch_id),
            ),
            cycles_completed=1,
        )


class _UnevenWorker(_Worker):
    def __init__(
        self,
        *,
        fast_epoch_id: UUID,
        slow_epoch_id: UUID,
    ) -> None:
        super().__init__()
        self.fast_epoch_id = fast_epoch_id
        self.slow_epoch_id = slow_epoch_id
        self.fast_calls = 0
        self.slow_calls = 0
        self.slow_started = asyncio.Event()
        self.fast_repeated = asyncio.Event()
        self.release_slow = asyncio.Event()

    async def run_epoch(
        self,
        epoch_id: UUID,
        *,
        max_cycles: int | None = None,
    ) -> OperationalPaperSessionRunWorkerResult:
        self.calls.append(
            (
                epoch_id,
                max_cycles,
            )
        )

        if epoch_id == self.slow_epoch_id:
            self.slow_calls += 1
            self.slow_started.set()
            await self.release_slow.wait()

        elif epoch_id == self.fast_epoch_id:
            self.fast_calls += 1

            if self.fast_calls >= 2:
                self.fast_repeated.set()

        else:
            raise AssertionError(
                "Epoch inesperado no worker de teste."
            )

        return OperationalPaperSessionRunWorkerResult(
            cast(
                runs.OperationalPaperSessionRunEpoch,
                _WorkerEpoch(epoch_id),
            ),
            cycles_completed=1,
        )


def _supervisor(
    repository: _Repository,
    worker: _Worker,
    *,
    page_size: int = 2,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> OperationalPaperSessionRunSupervisor:
    return OperationalPaperSessionRunSupervisor(
        cast(
            PostgresOperationalPaperSessionRunRepository,
            repository,
        ),
        cast(
            OperationalPaperSessionRunWorker,
            worker,
        ),
        policy=OperationalPaperSessionRunSupervisorPolicy(
            poll_interval_seconds=0.01,
            page_size=page_size,
        ),
        sleeper=sleeper,
    )


@pytest.mark.asyncio
async def test_poll_once_processes_each_epoch_with_one_cycle_bound() -> None:
    first = _Epoch(uuid4())
    second = _Epoch(uuid4())

    repository = _Repository(
        {
            0: [first, second],
        }
    )
    worker = _Worker()

    supervisor = _supervisor(
        repository,
        worker,
        page_size=2,
    )

    result = await supervisor.poll_once()

    assert repository.calls == [(2, 0)]
    assert worker.calls == [
        (first.epoch_id, 1),
        (second.epoch_id, 1),
    ]
    assert result.epochs_discovered == 2
    assert result.epochs_processed == 2
    assert result.cycles_completed == 2
    assert result.next_offset == 2


@pytest.mark.asyncio
async def test_poll_once_runs_discovered_epochs_concurrently() -> None:
    first = _Epoch(uuid4())
    second = _Epoch(uuid4())

    repository = _Repository(
        {
            0: [
                first,
                second,
            ],
        }
    )

    worker = _BlockingWorker(
        expected_starts=2,
    )

    supervisor = _supervisor(
        repository,
        worker,
        page_size=2,
    )

    poll_task = asyncio.create_task(
        supervisor.poll_once()
    )

    try:
        await asyncio.wait_for(
            worker.all_started.wait(),
            timeout=1.0,
        )

        assert len(worker.started) == 2
        assert set(worker.started) == {
            first.epoch_id,
            second.epoch_id,
        }

        assert set(worker.calls) == {
            (first.epoch_id, 1),
            (second.epoch_id, 1),
        }
    finally:
        worker.release.set()
        result = await poll_task

    assert result.epochs_discovered == 2
    assert result.epochs_processed == 2
    assert result.cycles_completed == 2
    assert result.next_offset == 2


@pytest.mark.asyncio
async def test_supervisor_round_robin_wraps_after_short_page() -> None:
    first = _Epoch(uuid4())
    second = _Epoch(uuid4())
    third = _Epoch(uuid4())

    repository = _Repository(
        {
            0: [first, second],
            2: [third],
        }
    )
    worker = _Worker()

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
        (2, 0),
        (2, 2),
        (2, 0),
    ]


@pytest.mark.asyncio
async def test_empty_page_wraps_to_zero() -> None:
    repository = _Repository(
        {
            0: [],
        }
    )
    worker = _Worker()

    supervisor = _supervisor(
        repository,
        worker,
        page_size=2,
    )

    result = await supervisor.poll_once()

    assert result.epochs_discovered == 0
    assert result.epochs_processed == 0
    assert result.cycles_completed == 0
    assert result.next_offset == 0
    assert worker.calls == []


@pytest.mark.asyncio
async def test_run_is_bounded_and_sleeps_between_polls() -> None:
    epoch = _Epoch(uuid4())
    repository = _Repository(
        {
            0: [epoch],
        }
    )
    worker = _Worker()
    sleeps: list[float] = []

    async def sleeper(delay: float) -> None:
        sleeps.append(delay)

    supervisor = _supervisor(
        repository,
        worker,
        page_size=2,
        sleeper=sleeper,
    )

    result = await supervisor.run(max_polls=3)

    assert result.polls_completed == 3
    assert result.epochs_processed == 3
    assert result.cycles_completed == 3
    assert sleeps == [0.01, 0.01]


@pytest.mark.asyncio
async def test_run_reschedules_fast_epoch_while_slow_sibling_is_active() -> None:
    fast = _Epoch(uuid4())
    slow = _Epoch(uuid4())

    repository = _Repository(
        {
            0: [
                fast,
                slow,
            ],
        }
    )

    worker = _UnevenWorker(
        fast_epoch_id=fast.epoch_id,
        slow_epoch_id=slow.epoch_id,
    )

    async def sleeper(
        _delay: float,
    ) -> None:
        await asyncio.sleep(0)

    supervisor = _supervisor(
        repository,
        worker,
        page_size=100,
        sleeper=sleeper,
    )

    run_task = asyncio.create_task(
        supervisor.run(
            max_polls=4,
        )
    )

    try:
        await asyncio.wait_for(
            worker.slow_started.wait(),
            timeout=1.0,
        )

        await asyncio.wait_for(
            worker.fast_repeated.wait(),
            timeout=1.0,
        )

        assert worker.fast_calls >= 2
        assert worker.slow_calls == 1
        assert not worker.release_slow.is_set()

    finally:
        worker.release_slow.set()

    result = await asyncio.wait_for(
        run_task,
        timeout=1.0,
    )

    assert result.polls_completed == 4
    assert result.epochs_processed >= 3
    assert result.cycles_completed >= 3


@pytest.mark.asyncio
async def test_request_stop_propagates_to_worker_and_prevents_new_poll() -> None:
    repository = _Repository(
        {
            0: [_Epoch(uuid4())],
        }
    )
    worker = _Worker()

    supervisor = _supervisor(
        repository,
        worker,
    )

    supervisor.request_stop()

    result = await supervisor.run()

    assert worker.stop_requested
    assert result.polls_completed == 0
    assert result.epochs_processed == 0
    assert worker.calls == []
    assert repository.calls == []


@pytest.mark.parametrize(
    ("poll_interval_seconds", "page_size"),
    [
        (0.0, 100),
        (-1.0, 100),
        (float("inf"), 100),
        (1.0, 0),
        (1.0, 101),
        (1.0, True),
    ],
)
def test_supervisor_policy_is_bounded(
    poll_interval_seconds: float,
    page_size: int,
) -> None:
    with pytest.raises(ValueError):
        OperationalPaperSessionRunSupervisorPolicy(
            poll_interval_seconds=poll_interval_seconds,
            page_size=page_size,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        True,
    ],
)
async def test_run_rejects_invalid_max_polls(
    value: int,
) -> None:
    supervisor = _supervisor(
        _Repository({}),
        _Worker(),
    )

    with pytest.raises(ValueError):
        await supervisor.run(max_polls=value)


@pytest.mark.asyncio
async def test_real_postgres_discovery_delivers_pending_epoch_once(
    database: Database,
    database_url: str,
    auth_user_id: UUID,
) -> None:
    activation = await _valid_activation(
        database_url,
        database,
        auth_user_id,
    )

    with psycopg.connect(database_url) as connection:
        _insert_activation(
            connection,
            _activation_row(activation),
        )

    repository = PostgresOperationalPaperSessionRunRepository(database)

    specification = runs.build_operational_paper_session_run_epoch_specification(activation)

    pending = await repository.start(
        specification,
        actor_id=auth_user_id,
        idempotency_key=f"supervisor:start:{uuid4().hex}",
        now=START_AT,
    )

    worker = _Worker()

    supervisor = OperationalPaperSessionRunSupervisor(
        repository,
        cast(
            OperationalPaperSessionRunWorker,
            worker,
        ),
        policy=OperationalPaperSessionRunSupervisorPolicy(
            poll_interval_seconds=0.01,
            page_size=100,
        ),
    )

    result = await supervisor.poll_once()

    assert worker.calls == [
        (pending.epoch_id, 1),
    ]

    assert result.epochs_discovered == 1
    assert result.epochs_processed == 1
    assert result.cycles_completed == 1
    assert result.next_offset == 0

    # The spy worker performs no persistence itself, proving discovery
    # is read-only and the supervisor does not forge epoch mutations.
    assert await repository.get(pending.epoch_id) == pending
