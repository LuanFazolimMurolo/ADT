"""Separate-process runtime wiring for PostgreSQL-backed market-data operations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final
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
from app.market_data.operations import MarketOperationSnapshot
from app.market_data.orchestration import BackfillExecutor
from app.market_data.planning import MarketDataPlanner
from app.market_data.services import default_local_services
from app.repositories import PostgresMarketOperationRepository

MarketOperationRuntimeClock = Callable[[], datetime]

MARKET_OPERATION_LEASE_DURATION: Final = timedelta(minutes=2)
MARKET_OPERATION_HEARTBEAT_INTERVAL_SECONDS: Final = (
    MARKET_OPERATION_LEASE_DURATION.total_seconds() / 4
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def run_market_operation_worker_once(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    owner_id: UUID | None = None,
    clock: MarketOperationRuntimeClock | None = None,
) -> MarketOperationSnapshot | None:
    """Build one isolated worker runtime and process at most one operation."""
    effective_clock = clock or _utc_now
    effective_owner_id = owner_id if owner_id is not None else uuid4()

    database = Database(settings.supabase_database_url.get_secret_value())
    await database.open()

    try:
        async with PublicMarketHttpClient(
            base_url=BINANCE_MARKET_DATA_BASE_URL,
            user_agent=settings.market_user_agent,
            timeout_seconds=settings.market_http_timeout,
            max_connections=settings.market_http_max_connections,
            retries=settings.market_http_retries,
            max_retry_after_seconds=settings.market_http_max_retry_after,
            transport=transport,
        ) as market_http_client:
            adapter = BinanceSpotAdapter(
                market_http_client,
                allow_open_candles=settings.market_allow_open_candles,
                now=effective_clock,
            )

            _catalog_service, history_service = default_local_services(
                settings.data_dir,
                adapter,
                max_fetch_candles=settings.market_max_fetch_candles,
                clock=effective_clock,
                lock_timeout_seconds=settings.market_job_lock_timeout,
                lock_stale_after_seconds=settings.market_job_stale_after,
            )

            jobs = MarketJobCatalog(
                settings.data_dir,
                clock=effective_clock,
                stale_after_seconds=settings.market_job_stale_after,
            )

            planner = MarketDataPlanner(
                adapter_request_limit=adapter.limits.max_candles_per_request,
                max_fetch_candles=settings.market_max_fetch_candles,
                chunk_candles=settings.market_backfill_chunk_candles,
                max_total_candles=settings.market_backfill_max_total_candles,
                max_chunks=settings.market_job_max_chunks,
                clock=effective_clock,
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
                owner_id=effective_owner_id,
                clock=effective_clock,
                lease_duration=MARKET_OPERATION_LEASE_DURATION,
            )
            worker = MarketOperationWorker(
                session=session,
                planner=planner,
                executor=executor,
                jobs=jobs,
                history=history_service,
                clock=effective_clock,
                heartbeat_interval_seconds=MARKET_OPERATION_HEARTBEAT_INTERVAL_SECONDS,
            )

            return await worker.run_once()
    finally:
        await database.close()
