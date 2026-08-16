"""Separate-process runtime wiring for PostgreSQL-backed market-data operations."""

from __future__ import annotations

import asyncio
import math
import signal
from collections.abc import Awaitable, Callable, Iterator
from contextlib import AsyncExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import FrameType, TracebackType
from typing import Final, Protocol
from uuid import UUID, uuid4

import httpx

from app.core.config import Settings
from app.database import Database
from app.market_data.binance import BINANCE_MARKET_DATA_BASE_URL, BinanceSpotAdapter
from app.market_data.http import PublicMarketHttpClient
from app.market_data.jobs import MarketJobCatalog
from app.market_data.operation_worker import (
    MarketOperationWorker,
    MarketOperationWorkerSession,
)
from app.market_data.operations import (
    MarketOperationSnapshot,
    MarketOperationState,
)
from app.market_data.orchestration import BackfillExecutor
from app.market_data.planning import MarketDataPlanner
from app.market_data.services import default_local_services
from app.repositories import PostgresMarketOperationRepository

MarketOperationRuntimeClock = Callable[[], datetime]
MarketOperationWorkerSleeper = Callable[[float], Awaitable[None]]

MARKET_OPERATION_LEASE_DURATION: Final = timedelta(minutes=2)
MARKET_OPERATION_HEARTBEAT_INTERVAL_SECONDS: Final = (
    MARKET_OPERATION_LEASE_DURATION.total_seconds() / 4
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_interval_seconds(interval_seconds: float) -> None:
    if not math.isfinite(interval_seconds) or interval_seconds <= 0:
        raise ValueError("interval_seconds must be finite and positive")


def _validate_max_cycles(max_cycles: int | None) -> None:
    if max_cycles is not None and max_cycles <= 0:
        raise ValueError("max_cycles must be positive")


@dataclass(frozen=True, slots=True)
class MarketOperationWorkerLoopResult:
    """Sanitized summary of one bounded or interrupted polling run."""

    cycles_completed: int
    operations_processed: int
    idle_cycles: int
    last_operation_id: UUID | None
    last_state: MarketOperationState | None


class MarketOperationPoller(Protocol):
    """Minimal poll boundary consumed by the continuous runner."""

    async def run_once(self) -> MarketOperationSnapshot | None:
        """Process at most one queued operation."""
        ...


class MarketOperationSignalLoop(Protocol):
    """Event-loop signal boundary used by the process-level shutdown hook."""

    def call_soon_threadsafe(
        self,
        callback: Callable[[], object],
    ) -> object: ...


class MarketOperationWorkerRuntime:
    """Own long-lived PostgreSQL, HTTP and market-data worker resources."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        owner_id: UUID | None = None,
        clock: MarketOperationRuntimeClock | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._owner_id = owner_id if owner_id is not None else uuid4()
        self._clock = clock or _utc_now
        self._stack: AsyncExitStack | None = None
        self._worker: MarketOperationWorker | None = None

    @property
    def owner_id(self) -> UUID:
        """Return the stable owner identity for this process runtime."""
        return self._owner_id

    async def __aenter__(self) -> MarketOperationWorkerRuntime:
        if self._stack is not None:
            raise RuntimeError("market-operation worker runtime is already open")

        stack = AsyncExitStack()
        await stack.__aenter__()

        try:
            settings = self._settings

            database = Database(settings.supabase_database_url.get_secret_value())
            stack.push_async_callback(database.close)
            await database.open()

            market_http_client = await stack.enter_async_context(
                PublicMarketHttpClient(
                    base_url=BINANCE_MARKET_DATA_BASE_URL,
                    user_agent=settings.market_user_agent,
                    timeout_seconds=settings.market_http_timeout,
                    max_connections=settings.market_http_max_connections,
                    retries=settings.market_http_retries,
                    max_retry_after_seconds=settings.market_http_max_retry_after,
                    transport=self._transport,
                )
            )

            adapter = BinanceSpotAdapter(
                market_http_client,
                allow_open_candles=settings.market_allow_open_candles,
                now=self._clock,
            )

            _catalog_service, history_service = default_local_services(
                settings.data_dir,
                adapter,
                max_fetch_candles=settings.market_max_fetch_candles,
                clock=self._clock,
                lock_timeout_seconds=settings.market_job_lock_timeout,
                lock_stale_after_seconds=settings.market_job_stale_after,
            )

            jobs = MarketJobCatalog(
                settings.data_dir,
                clock=self._clock,
                stale_after_seconds=settings.market_job_stale_after,
            )

            planner = MarketDataPlanner(
                adapter_request_limit=adapter.limits.max_candles_per_request,
                max_fetch_candles=settings.market_max_fetch_candles,
                chunk_candles=settings.market_backfill_chunk_candles,
                max_total_candles=settings.market_backfill_max_total_candles,
                max_chunks=settings.market_job_max_chunks,
                clock=self._clock,
            )

            executor = BackfillExecutor(
                history=history_service,
                jobs=jobs,
                data_dir=settings.data_dir,
                lock_timeout_seconds=settings.market_job_lock_timeout,
                lock_stale_after_seconds=settings.market_job_stale_after,
            )

            repository = PostgresMarketOperationRepository(database)
            session = MarketOperationWorkerSession(
                repository=repository,
                owner_id=self._owner_id,
                clock=self._clock,
                lease_duration=MARKET_OPERATION_LEASE_DURATION,
            )

            self._worker = MarketOperationWorker(
                session=session,
                planner=planner,
                executor=executor,
                jobs=jobs,
                history=history_service,
                clock=self._clock,
                heartbeat_interval_seconds=(MARKET_OPERATION_HEARTBEAT_INTERVAL_SECONDS),
            )
            self._stack = stack
            return self
        except BaseException:
            await stack.aclose()
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        stack = self._stack
        self._stack = None
        self._worker = None

        if stack is None:
            return None

        return await stack.__aexit__(exc_type, exc, traceback)

    async def run_once(self) -> MarketOperationSnapshot | None:
        """Process at most one queued operation using the open runtime."""
        worker = self._worker

        if worker is None:
            raise RuntimeError("market-operation worker runtime is not open")

        return await worker.run_once()


class MarketOperationContinuousRunner:
    """Poll one long-lived worker runtime without reconnecting per cycle."""

    def __init__(
        self,
        *,
        runtime: MarketOperationPoller,
        interval_seconds: float,
        sleeper: MarketOperationWorkerSleeper = asyncio.sleep,
    ) -> None:
        _validate_interval_seconds(interval_seconds)

        self._runtime = runtime
        self._interval_seconds = interval_seconds
        self._sleeper = sleeper
        self._stop_requested = asyncio.Event()
        self._active_poll: asyncio.Task[MarketOperationSnapshot | None] | None = None

    def request_stop(self) -> None:
        """Idempotently stop polling and cancel one active poll cooperatively."""
        if self._stop_requested.is_set():
            return

        self._stop_requested.set()
        active_poll = self._active_poll
        if active_poll is not None and not active_poll.done():
            active_poll.cancel()

    async def run(
        self,
        *,
        max_cycles: int | None = None,
    ) -> MarketOperationWorkerLoopResult:
        """Drain queued work immediately and sleep only after an idle poll."""
        _validate_max_cycles(max_cycles)

        cycles_completed = 0
        operations_processed = 0
        idle_cycles = 0
        last_operation: MarketOperationSnapshot | None = None

        while not self._stop_requested.is_set() and (
            max_cycles is None or cycles_completed < max_cycles
        ):
            active_poll = asyncio.create_task(self._runtime.run_once())
            self._active_poll = active_poll
            try:
                operation = await active_poll
            except asyncio.CancelledError:
                if not self._stop_requested.is_set():
                    raise
                break
            finally:
                self._active_poll = None

            cycles_completed += 1

            if operation is None:
                idle_cycles += 1
            else:
                operations_processed += 1
                last_operation = operation

            if max_cycles is not None and cycles_completed >= max_cycles:
                break

            if self._stop_requested.is_set():
                break

            if operation is None:
                await self._wait_until_next_cycle()

        return MarketOperationWorkerLoopResult(
            cycles_completed=cycles_completed,
            operations_processed=operations_processed,
            idle_cycles=idle_cycles,
            last_operation_id=(last_operation.operation_id if last_operation is not None else None),
            last_state=(last_operation.state if last_operation is not None else None),
        )

    async def _wait_until_next_cycle(self) -> None:
        async def wait_for_stop() -> None:
            await self._stop_requested.wait()

        sleep_task: asyncio.Future[None] = asyncio.ensure_future(
            self._sleeper(self._interval_seconds)
        )
        stop_task = asyncio.create_task(wait_for_stop())

        try:
            await asyncio.wait(
                {sleep_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            sleep_task.cancel()
            stop_task.cancel()
            await asyncio.gather(sleep_task, stop_task, return_exceptions=True)

        if not sleep_task.cancelled():
            sleep_task.result()


SignalHandler = signal.Handlers | int | Callable[[int, FrameType | None], object]


@contextmanager
def _market_operation_shutdown_signals(
    runner: MarketOperationContinuousRunner,
    *,
    loop: MarketOperationSignalLoop | None = None,
) -> Iterator[None]:
    """Own SIGTERM/SIGINT handlers only for the continuous worker lifecycle."""
    signal_loop = loop if loop is not None else asyncio.get_running_loop()
    installed: list[tuple[signal.Signals, SignalHandler]] = []

    def request_shutdown(_signum: int, _frame: FrameType | None) -> None:
        signal_loop.call_soon_threadsafe(runner.request_stop)

    for shutdown_signal in (signal.SIGTERM, signal.SIGINT):
        try:
            previous = signal.signal(shutdown_signal, request_shutdown)
        except (OSError, RuntimeError, ValueError):
            continue
        if previous is None:
            previous = signal.SIG_DFL
        installed.append((shutdown_signal, previous))

    try:
        yield
    finally:
        for shutdown_signal, previous in reversed(installed):
            signal.signal(shutdown_signal, previous)


async def run_market_operation_worker_once(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    owner_id: UUID | None = None,
    clock: MarketOperationRuntimeClock | None = None,
) -> MarketOperationSnapshot | None:
    """Build one isolated worker runtime and process at most one operation."""
    async with MarketOperationWorkerRuntime(
        settings,
        transport=transport,
        owner_id=owner_id,
        clock=clock,
    ) as runtime:
        return await runtime.run_once()


async def run_market_operation_worker_loop(
    settings: Settings,
    *,
    interval_seconds: float,
    max_cycles: int | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    owner_id: UUID | None = None,
    clock: MarketOperationRuntimeClock | None = None,
    sleeper: MarketOperationWorkerSleeper = asyncio.sleep,
) -> MarketOperationWorkerLoopResult:
    """Run repeated polls with one stable owner and one resource lifecycle."""
    _validate_interval_seconds(interval_seconds)
    _validate_max_cycles(max_cycles)

    runtime = MarketOperationWorkerRuntime(
        settings,
        transport=transport,
        owner_id=owner_id,
        clock=clock,
    )
    runner = MarketOperationContinuousRunner(
        runtime=runtime,
        interval_seconds=interval_seconds,
        sleeper=sleeper,
    )

    with _market_operation_shutdown_signals(runner):
        async with runtime:
            return await runner.run(max_cycles=max_cycles)
