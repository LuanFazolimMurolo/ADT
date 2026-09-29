"""Persistent PostgreSQL-driven supervisor for operational collector epochs."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.domain.errors import PersistenceUnavailableError
from app.repositories.operational_market_data_collectors import (
    PostgresOperationalMarketDataCollectorRepository,
)
from app.services.operational_market_data_collector_worker import (
    OperationalMarketDataCollectorWorker,
    OperationalMarketDataCollectorWorkerResult,
)

Sleeper = Callable[[float], Awaitable[None]]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OperationalMarketDataCollectorSupervisorPolicy:
    """Bounded polling policy for one persistent collector supervisor."""

    poll_interval_seconds: float = 1.0
    page_size: int = 100
    persistence_retry_seconds: float = 5.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.poll_interval_seconds, bool)
            or not math.isfinite(self.poll_interval_seconds)
            or self.poll_interval_seconds <= 0
        ):
            raise ValueError("O intervalo do supervisor collector é inválido.")

        if type(self.page_size) is not int or not 1 <= self.page_size <= 100:
            raise ValueError("O tamanho da página do supervisor collector é inválido.")

        retry = self.persistence_retry_seconds
        if (
            isinstance(retry, bool)
            or not math.isfinite(retry)
            or retry <= 0
        ):
            raise ValueError(
                "O intervalo de retry de persistência do supervisor collector é inválido."
            )


@dataclass(frozen=True, slots=True)
class OperationalMarketDataCollectorSupervisorPollResult:
    """One bounded discovery and convergence pass."""

    epochs_discovered: int
    epochs_processed: int
    cycles_completed: int
    next_offset: int
    last_worker_result: OperationalMarketDataCollectorWorkerResult | None = None


@dataclass(frozen=True, slots=True)
class OperationalMarketDataCollectorSupervisorLoopResult:
    """Bounded or shutdown-completed supervisor loop result."""

    polls_completed: int
    epochs_processed: int
    cycles_completed: int
    last_worker_result: OperationalMarketDataCollectorWorkerResult | None = None


class OperationalMarketDataCollectorSupervisor:
    """Consume PostgreSQL desired state outside FastAPI request execution."""

    def __init__(
        self,
        repository: PostgresOperationalMarketDataCollectorRepository,
        worker: OperationalMarketDataCollectorWorker,
        *,
        policy: OperationalMarketDataCollectorSupervisorPolicy | None = None,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._repository = repository
        self._worker = worker
        self._policy = policy or OperationalMarketDataCollectorSupervisorPolicy()
        self._sleeper = sleeper
        self._stop_requested = asyncio.Event()
        self._offset = 0

    @property
    def worker(self) -> OperationalMarketDataCollectorWorker:
        """Worker identity owned by this supervisor process."""
        return self._worker

    def request_stop(self) -> None:
        """Stop scheduling new epochs and let an active cycle finish."""
        self._stop_requested.set()
        self._worker.request_stop()

    async def poll_once(
        self,
    ) -> OperationalMarketDataCollectorSupervisorPollResult:
        """Discover one bounded page and converge each epoch once."""
        epochs = await self._repository.list_nonterminal(
            limit=self._policy.page_size,
            offset=self._offset,
        )

        discovered = len(epochs)
        processed = 0
        cycles_completed = 0
        last_worker_result: OperationalMarketDataCollectorWorkerResult | None = None

        for epoch in epochs:
            if self._stop_requested.is_set():
                break

            result = await self._worker.run_epoch(
                epoch.epoch_id,
                max_cycles=1,
            )

            processed += 1
            cycles_completed += result.cycles_completed
            last_worker_result = result

        if discovered < self._policy.page_size:
            next_offset = 0
        else:
            next_offset = self._offset + discovered

        self._offset = next_offset

        return OperationalMarketDataCollectorSupervisorPollResult(
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
    ) -> OperationalMarketDataCollectorSupervisorLoopResult:
        """Poll persistently until shutdown or an optional test bound."""
        if max_polls is not None and (isinstance(max_polls, bool) or max_polls <= 0):
            raise ValueError("O limite de polls do supervisor collector é inválido.")

        polls_completed = 0
        epochs_processed = 0
        cycles_completed = 0
        last_worker_result: OperationalMarketDataCollectorWorkerResult | None = None

        while not self._stop_requested.is_set():
            try:
                poll = await self.poll_once()
            except PersistenceUnavailableError:
                if self._stop_requested.is_set():
                    break

                logger.warning(
                    "Operational market-data supervisor persistence unavailable; retrying.",
                    extra={
                        "component": "market-supervisor",
                        "event": "persistence_retry",
                        "operation": "poll_once",
                        "retry_delay_seconds": (
                            self._policy.persistence_retry_seconds
                        ),
                    },
                )

                await self._wait_for_delay(
                    self._policy.persistence_retry_seconds
                )
                continue

            polls_completed += 1
            epochs_processed += poll.epochs_processed
            cycles_completed += poll.cycles_completed

            if poll.last_worker_result is not None:
                last_worker_result = poll.last_worker_result

            if max_polls is not None and polls_completed >= max_polls:
                break

            if self._stop_requested.is_set():
                break

            await self._wait_until_next_poll()

        return OperationalMarketDataCollectorSupervisorLoopResult(
            polls_completed=polls_completed,
            epochs_processed=epochs_processed,
            cycles_completed=cycles_completed,
            last_worker_result=last_worker_result,
        )

    async def _wait_until_next_poll(self) -> None:
        await self._wait_for_delay(
            self._policy.poll_interval_seconds
        )

    async def _wait_for_delay(
        self,
        delay_seconds: float,
    ) -> None:
        """Wait for one bounded delay while remaining shutdown-responsive."""

        sleep_task = asyncio.ensure_future(
            self._sleeper(delay_seconds)
        )
        stop_task = asyncio.create_task(
            self._stop_requested.wait()
        )

        try:
            await asyncio.wait(
                {
                    sleep_task,
                    stop_task,
                },
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not sleep_task.done():
                sleep_task.cancel()

            if not stop_task.done():
                stop_task.cancel()

            await asyncio.gather(
                sleep_task,
                stop_task,
                return_exceptions=True,
            )

        if (
            sleep_task.done()
            and not sleep_task.cancelled()
        ):
            sleep_task.result()
