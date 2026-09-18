"""Persistent PostgreSQL-driven supervisor for operational paper epochs."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from app.repositories.operational_paper_session_runs import (
    PostgresOperationalPaperSessionRunRepository,
)
from app.services.operational_paper_session_run_worker import (
    OperationalPaperSessionRunWorker,
    OperationalPaperSessionRunWorkerResult,
)

Sleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionRunSupervisorPolicy:
    """Bounded polling policy for one persistent supervisor process."""

    poll_interval_seconds: float = 1.0
    page_size: int = 100

    def __post_init__(self) -> None:
        if (
            isinstance(self.poll_interval_seconds, bool)
            or not math.isfinite(self.poll_interval_seconds)
            or self.poll_interval_seconds <= 0
        ):
            raise ValueError("O intervalo do supervisor paper é inválido.")

        if type(self.page_size) is not int or not 1 <= self.page_size <= 100:
            raise ValueError("O tamanho da página do supervisor paper é inválido.")


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionRunSupervisorPollResult:
    """One bounded discovery and convergence pass."""

    epochs_discovered: int
    epochs_processed: int
    cycles_completed: int
    next_offset: int
    last_worker_result: OperationalPaperSessionRunWorkerResult | None = None


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionRunSupervisorLoopResult:
    """Bounded or shutdown-completed supervisor loop result."""

    polls_completed: int
    epochs_processed: int
    cycles_completed: int
    last_worker_result: OperationalPaperSessionRunWorkerResult | None = None


class OperationalPaperSessionRunSupervisor:
    """Consume PostgreSQL desired state without executing inside FastAPI."""

    def __init__(
        self,
        repository: PostgresOperationalPaperSessionRunRepository,
        worker: OperationalPaperSessionRunWorker,
        *,
        policy: OperationalPaperSessionRunSupervisorPolicy | None = None,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._repository = repository
        self._worker = worker
        self._policy = policy or OperationalPaperSessionRunSupervisorPolicy()
        self._sleeper = sleeper
        self._stop_requested = asyncio.Event()
        self._offset = 0

    @property
    def worker(self) -> OperationalPaperSessionRunWorker:
        """Worker identity owned by this supervisor process."""
        return self._worker

    def request_stop(self) -> None:
        """Stop scheduling new epochs and let the active cycle finish."""
        self._stop_requested.set()
        self._worker.request_stop()

    async def poll_once(
        self,
    ) -> OperationalPaperSessionRunSupervisorPollResult:
        """Discover one bounded page and converge each epoch once."""
        epochs = await self._repository.list_nonterminal(
            limit=self._policy.page_size,
            offset=self._offset,
        )

        discovered = len(epochs)
        selected_epochs = []

        for epoch in epochs:
            if self._stop_requested.is_set():
                break
            selected_epochs.append(epoch)

        results = await asyncio.gather(
            *(
                self._worker.run_epoch(
                    epoch.epoch_id,
                    max_cycles=1,
                )
                for epoch in selected_epochs
            )
        )

        processed = len(results)
        cycles_completed = sum(
            result.cycles_completed
            for result in results
        )
        last_worker_result = (
            results[-1]
            if results
            else None
        )

        if discovered < self._policy.page_size:
            next_offset = 0
        else:
            next_offset = self._offset + discovered

        self._offset = next_offset

        return OperationalPaperSessionRunSupervisorPollResult(
            epochs_discovered=discovered,
            epochs_processed=processed,
            cycles_completed=cycles_completed,
            next_offset=next_offset,
            last_worker_result=last_worker_result,
        )

    async def run(
        self,
        *,
        max_polls: int | None = None,
    ) -> OperationalPaperSessionRunSupervisorLoopResult:
        """Run persistent polling with independent per-epoch cycle scheduling."""
        if max_polls is not None and (
            isinstance(max_polls, bool)
            or max_polls <= 0
        ):
            raise ValueError(
                "O limite de polls do supervisor paper é inválido."
            )

        polls_completed = 0
        epochs_processed = 0
        cycles_completed = 0
        last_worker_result: OperationalPaperSessionRunWorkerResult | None = None

        active_tasks: dict[
            UUID,
            asyncio.Task[
                OperationalPaperSessionRunWorkerResult
            ],
        ] = {}

        def collect_finished() -> None:
            nonlocal epochs_processed
            nonlocal cycles_completed
            nonlocal last_worker_result

            finished = [
                epoch_id
                for epoch_id, task in active_tasks.items()
                if task.done()
            ]

            for epoch_id in finished:
                task = active_tasks.pop(epoch_id)
                result = task.result()

                epochs_processed += 1
                cycles_completed += result.cycles_completed
                last_worker_result = result

        async def drain_active() -> None:
            if not active_tasks:
                return

            await asyncio.gather(
                *active_tasks.values()
            )
            collect_finished()

        try:
            while not self._stop_requested.is_set():
                # Reap completed epochs before discovery so a fast
                # epoch can immediately receive its next bounded
                # cycle even while a slower sibling is still active.
                collect_finished()

                epochs = await self._repository.list_nonterminal(
                    limit=self._policy.page_size,
                    offset=self._offset,
                )

                discovered = len(epochs)

                if discovered < self._policy.page_size:
                    next_offset = 0
                else:
                    next_offset = (
                        self._offset + discovered
                    )

                self._offset = next_offset

                for epoch in epochs:
                    if self._stop_requested.is_set():
                        break

                    if epoch.epoch_id in active_tasks:
                        continue

                    active_tasks[epoch.epoch_id] = (
                        asyncio.create_task(
                            self._worker.run_epoch(
                                epoch.epoch_id,
                                max_cycles=1,
                            )
                        )
                    )

                polls_completed += 1

                if (
                    max_polls is not None
                    and polls_completed >= max_polls
                ):
                    break

                if self._stop_requested.is_set():
                    break

                await self._wait_until_next_poll()

            await drain_active()

        except asyncio.CancelledError:
            # A process-level shutdown must stop scheduling new
            # work and let already-running synchronous paper
            # cycles reach their safe boundary.
            self.request_stop()

            if active_tasks:
                await asyncio.gather(
                    *active_tasks.values(),
                    return_exceptions=True,
                )

            raise

        return OperationalPaperSessionRunSupervisorLoopResult(
            polls_completed=polls_completed,
            epochs_processed=epochs_processed,
            cycles_completed=cycles_completed,
            last_worker_result=last_worker_result,
        )

    async def _wait_until_next_poll(self) -> None:
        async def wait_for_stop() -> None:
            await self._stop_requested.wait()

        sleep_task = asyncio.ensure_future(self._sleeper(self._policy.poll_interval_seconds))
        stop_task = asyncio.create_task(wait_for_stop())

        try:
            await asyncio.wait(
                {sleep_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            sleep_task.cancel()
            stop_task.cancel()
            await asyncio.gather(
                sleep_task,
                stop_task,
                return_exceptions=True,
            )

        if not sleep_task.cancelled():
            sleep_task.result()
