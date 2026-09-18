"""Standalone persistent market-data collector supervisor process."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.database import Database
from app.market_data.asset_catalog import AssetMarketService
from app.market_data.binance import (
    BINANCE_MARKET_DATA_BASE_URL,
    BinanceSpotAdapter,
)
from app.market_data.continuous import ContinuousCollectionStateStore
from app.market_data.http import PublicMarketHttpClient
from app.market_data.jobs import MarketJobCatalog
from app.market_data.locks import DatasetLockManager
from app.market_data.orchestration import BackfillExecutor
from app.market_data.planning import MarketDataPlanner
from app.market_data.services import default_local_services
from app.market_data.storage import ParquetCandleStore
from app.repositories.operational_market_data_collectors import (
    PostgresOperationalMarketDataCollectorRepository,
)
from app.runtime._signals import install_stop_handlers
from app.services.operational_market_data_collector_runtime import (
    OperationalMarketDataCollectorRuntimeExecutor,
)
from app.services.operational_market_data_collector_supervisor import (
    OperationalMarketDataCollectorSupervisor,
)
from app.services.operational_market_data_collector_worker import (
    OperationalMarketDataCollectorWorker,
)

logger = logging.getLogger(__name__)


def _clock() -> datetime:
    return datetime.now(UTC)


def build_supervisor(
    settings: Settings,
    database: Database,
    market_http_client: PublicMarketHttpClient,
) -> OperationalMarketDataCollectorSupervisor:
    """Build the persistent collector composition root without starting it."""

    adapter = BinanceSpotAdapter(
        market_http_client,
        allow_open_candles=settings.market_allow_open_candles,
        now=_clock,
    )

    instruments = AssetMarketService(
        adapter,
        catalog_ttl_seconds=settings.market_asset_catalog_ttl_seconds,
        max_instruments=settings.market_asset_catalog_max_instruments,
        clock=_clock,
    )

    _catalog, history = default_local_services(
        settings.data_dir,
        adapter,
        max_fetch_candles=settings.market_max_fetch_candles,
        clock=_clock,
        lock_timeout_seconds=settings.market_job_lock_timeout,
        lock_stale_after_seconds=settings.market_job_stale_after,
    )

    store = ParquetCandleStore(
        settings.data_dir
    )

    jobs = MarketJobCatalog(
        settings.data_dir,
        clock=_clock,
        stale_after_seconds=settings.market_job_stale_after,
    )

    planner = MarketDataPlanner(
        adapter_request_limit=adapter.limits.max_candles_per_request,
        max_fetch_candles=settings.market_max_fetch_candles,
        chunk_candles=settings.market_backfill_chunk_candles,
        max_total_candles=settings.market_backfill_max_total_candles,
        max_chunks=settings.market_job_max_chunks,
        clock=_clock,
    )

    executor = BackfillExecutor(
        history=history,
        jobs=jobs,
        data_dir=settings.data_dir,
        lock_timeout_seconds=settings.market_job_lock_timeout,
        lock_stale_after_seconds=settings.market_job_stale_after,
    )

    state_store = ContinuousCollectionStateStore(
        settings.data_dir
    )

    locks = DatasetLockManager(
        settings.data_dir,
        timeout_seconds=settings.market_job_lock_timeout,
        stale_after_seconds=settings.market_job_stale_after,
        clock=_clock,
    )

    runtime = OperationalMarketDataCollectorRuntimeExecutor(
        instruments=instruments,
        history=history,
        planner=planner,
        executor=executor,
        store=store,
        state_store=state_store,
        lock_manager=locks,
        recovery_hook=jobs.recover_abandoned,
        clock=_clock,
    )

    repository = (
        PostgresOperationalMarketDataCollectorRepository(
            database
        )
    )

    worker = OperationalMarketDataCollectorWorker(
        repository,
        runtime,
        clock=_clock,
    )

    return OperationalMarketDataCollectorSupervisor(
        repository,
        worker,
    )


async def run(settings: Settings) -> None:
    """Run until SIGINT/SIGTERM requests a cooperative shutdown."""

    database = Database(
        settings.supabase_database_url.get_secret_value()
    )

    market_http_client = PublicMarketHttpClient(
        base_url=BINANCE_MARKET_DATA_BASE_URL,
        user_agent=settings.market_user_agent,
        timeout_seconds=settings.market_http_timeout,
        max_connections=settings.market_http_max_connections,
        retries=settings.market_http_retries,
        max_retry_after_seconds=settings.market_http_max_retry_after,
    )

    await database.open()

    try:
        async with market_http_client:
            supervisor = build_supervisor(
                settings,
                database,
                market_http_client,
            )

            install_stop_handlers(
                supervisor.request_stop
            )

            logger.info(
                "Operational market-data supervisor starting."
            )

            await supervisor.run()

            logger.info(
                "Operational market-data supervisor stopped."
            )
    finally:
        await database.close()


def main() -> int:
    settings = get_settings()
    setup_logging(settings)
    asyncio.run(run(settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
