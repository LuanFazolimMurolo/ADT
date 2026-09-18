"""Standalone persistent operational paper-run supervisor process."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.database import Database
from app.market_data.catalog import JsonMarketDataCatalog
from app.market_data.locks import DatasetLockManager
from app.market_data.storage import ParquetCandleStore
from app.paper_trading.repository import PaperTradingRepository
from app.paper_trading.service import PaperTradingService
from app.repositories.operational_mandates import (
    PostgresOperationalMandateRepository,
)
from app.repositories.operational_paper_capital_authorizations import (
    PostgresOperationalPaperCapitalAuthorizationRepository,
)
from app.repositories.operational_paper_session_activations import (
    PostgresOperationalPaperSessionActivationRepository,
)
from app.repositories.operational_paper_session_materializations import (
    PostgresOperationalPaperSessionMaterializationRepository,
)
from app.repositories.operational_paper_session_profiles import (
    PostgresOperationalPaperSessionProfileRepository,
)
from app.repositories.operational_paper_session_runs import (
    PostgresOperationalPaperSessionRunRepository,
)
from app.repositories.simulations import SimulationRepository
from app.runtime._signals import install_stop_handlers
from app.services.operational_paper_session_run_supervisor import (
    OperationalPaperSessionRunSupervisor,
)
from app.services.operational_paper_session_run_worker import (
    OperationalPaperSessionRunWorker,
)
from app.services.operational_paper_session_runs import (
    OperationalPaperSessionRunService,
)
from app.strategies.registry import StrategyPluginRegistry

logger = logging.getLogger(__name__)


def _clock() -> datetime:
    return datetime.now(UTC)


def build_supervisor(
    settings: Settings,
    database: Database,
) -> OperationalPaperSessionRunSupervisor:
    """Build the persistent paper-run composition root without starting it."""

    run_repository = (
        PostgresOperationalPaperSessionRunRepository(
            database
        )
    )

    paper_repository = PaperTradingRepository(
        settings.data_dir,
        lock_timeout_seconds=settings.market_job_lock_timeout,
        lock_stale_after_seconds=settings.market_job_stale_after,
    )

    registry = StrategyPluginRegistry.builtins()

    raw_catalog = JsonMarketDataCatalog(
        settings.data_dir,
        clock=_clock,
    )

    raw_store = ParquetCandleStore(
        settings.data_dir
    )

    raw_locks = DatasetLockManager(
        settings.data_dir,
        timeout_seconds=settings.market_job_lock_timeout,
        stale_after_seconds=settings.market_job_stale_after,
        clock=_clock,
    )

    control = OperationalPaperSessionRunService(
        repository=run_repository,
        activation_repository=(
            PostgresOperationalPaperSessionActivationRepository(
                database
            )
        ),
        materialization_repository=(
            PostgresOperationalPaperSessionMaterializationRepository(
                database
            )
        ),
        authorization_repository=(
            PostgresOperationalPaperCapitalAuthorizationRepository(
                database
            )
        ),
        profile_repository=(
            PostgresOperationalPaperSessionProfileRepository(
                database
            )
        ),
        mandate_repository=(
            PostgresOperationalMandateRepository(
                database
            )
        ),
        simulation_repository=SimulationRepository(
            database
        ),
        paper_repository=paper_repository,
        registry=registry,
        raw_catalog=raw_catalog,
        raw_store=raw_store,
        raw_locks=raw_locks,
        clock=_clock,
    )

    paper = PaperTradingService(
        settings.data_dir,
        repository=paper_repository,
        registry=registry,
        clock=_clock,
        lock_timeout_seconds=settings.market_job_lock_timeout,
        lock_stale_after_seconds=settings.market_job_stale_after,
    )

    worker = OperationalPaperSessionRunWorker(
        run_repository,
        control,
        paper,
        clock=_clock,
    )

    return OperationalPaperSessionRunSupervisor(
        run_repository,
        worker,
    )


async def run(settings: Settings) -> None:
    """Run until SIGINT/SIGTERM requests a cooperative shutdown."""

    database = Database(
        settings.supabase_database_url.get_secret_value()
    )

    await database.open()

    try:
        supervisor = build_supervisor(
            settings,
            database,
        )

        install_stop_handlers(
            supervisor.request_stop
        )

        logger.info(
            "Operational paper-run supervisor starting."
        )

        await supervisor.run()

        logger.info(
            "Operational paper-run supervisor stopped."
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
