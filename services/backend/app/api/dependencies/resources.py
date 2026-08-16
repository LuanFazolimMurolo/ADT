"""Resolve application resources initialized by the FastAPI lifespan."""

from typing import cast

from fastapi import Depends, Request

from app.auth import SupabaseJWTVerifier
from app.database import Database
from app.market_data.asset_catalog import AssetMarketService
from app.market_data.candle_query import LocalMarketCandleReadService
from app.market_data.continuous import ContinuousCollectionStateStore
from app.market_data.raw_dataset_query import LocalRawDatasetReadService
from app.paper_trading.chart_annotations import PaperChartAnnotationReadService
from app.paper_trading.continuous import PaperRunnerStateStore
from app.paper_trading.dashboard import PaperDashboardReadService
from app.paper_trading.journal_export import PaperTradeJournalExportService
from app.paper_trading.journal_query import PaperTradeJournalReadService
from app.paper_trading.period_metrics import PaperPeriodMetricsService
from app.paper_trading.portfolio_timeline_query import (
    PaperPortfolioTimelineReadService,
)
from app.paper_trading.query import PaperTradingReadService
from app.repositories import (
    AdminRepository,
    CapitalMovementRepository,
    PostgresStrategyDefinitionRepository,
    PublicSimulationRepository,
    SettingsRepository,
    SimulationRepository,
)
from app.services import (
    AdminService,
    CapitalMovementService,
    MarketOperationService,
    PublicSimulationService,
    SettingsService,
    SimulationService,
)
from app.strategies import StrategyDefinitionService, builtin_indicator_capabilities


def get_database(request: Request) -> Database:
    """Return the application-owned PostgreSQL pool wrapper."""
    return cast(Database, request.app.state.database)


def get_asset_market_service(request: Request) -> AssetMarketService:
    """Return the application-owned public asset market service."""
    return cast(AssetMarketService, request.app.state.asset_market_service)


def get_market_candle_read_service(request: Request) -> LocalMarketCandleReadService:
    """Return the application-owned bounded local candle read service."""
    return cast(
        LocalMarketCandleReadService,
        request.app.state.market_candle_read_service,
    )


def get_raw_dataset_read_service(request: Request) -> LocalRawDatasetReadService:
    """Return the application-owned persisted RAW dataset reader."""
    return cast(
        LocalRawDatasetReadService,
        request.app.state.raw_dataset_read_service,
    )


def get_market_operation_service(request: Request) -> MarketOperationService:
    """Return the application-owned market-data operation control service."""
    return cast(MarketOperationService, request.app.state.market_operation_service)


def get_continuous_collection_state_store(
    request: Request,
) -> ContinuousCollectionStateStore:
    """Return the application-owned continuous collection state store."""
    return cast(
        ContinuousCollectionStateStore,
        request.app.state.continuous_collection_state_store,
    )


def get_paper_trading_read_service(request: Request) -> PaperTradingReadService:
    """Return the application-owned read-only paper-trading query service."""
    return cast(PaperTradingReadService, request.app.state.paper_trading_read_service)


def get_paper_chart_annotation_read_service(
    request: Request,
) -> PaperChartAnnotationReadService:
    """Return the application-owned bounded paper chart annotation service."""
    return cast(
        PaperChartAnnotationReadService,
        request.app.state.paper_chart_annotation_read_service,
    )


def get_paper_dashboard_read_service(request: Request) -> PaperDashboardReadService:
    """Return the application-owned paper-trading dashboard read service."""
    return cast(PaperDashboardReadService, request.app.state.paper_dashboard_read_service)


def get_paper_trade_journal_read_service(
    request: Request,
) -> PaperTradeJournalReadService:
    """Return the application-owned deterministic journal read service."""
    return cast(
        PaperTradeJournalReadService,
        request.app.state.paper_trade_journal_read_service,
    )


def get_paper_trade_journal_export_service(
    request: Request,
) -> PaperTradeJournalExportService:
    """Return the application-owned deterministic journal export service."""
    return cast(
        PaperTradeJournalExportService,
        request.app.state.paper_trade_journal_export_service,
    )


def get_paper_period_metrics_service(
    request: Request,
) -> PaperPeriodMetricsService:
    """Return the application-owned deterministic period-metrics service."""
    return cast(
        PaperPeriodMetricsService,
        request.app.state.paper_period_metrics_service,
    )


def get_paper_portfolio_timeline_read_service(
    request: Request,
) -> PaperPortfolioTimelineReadService:
    """Return the application-owned persisted portfolio-timeline reader."""
    return cast(
        PaperPortfolioTimelineReadService,
        request.app.state.paper_portfolio_timeline_read_service,
    )


def get_paper_runner_state_store(request: Request) -> PaperRunnerStateStore:
    """Return the application-owned latest paper-runner state store."""
    return cast(PaperRunnerStateStore, request.app.state.paper_runner_state_store)


def get_jwt_verifier(request: Request) -> SupabaseJWTVerifier:
    """Return the application-owned Supabase token verifier."""
    return cast(SupabaseJWTVerifier, request.app.state.jwt_verifier)


def get_admin_service(database: Database = Depends(get_database)) -> AdminService:
    """Build the administrator authorization service."""
    return AdminService(AdminRepository(database))


def get_public_simulation_service(
    database: Database = Depends(get_database),
) -> PublicSimulationService:
    """Build the safe public simulation service."""
    return PublicSimulationService(PublicSimulationRepository(database))


def get_simulation_service(
    database: Database = Depends(get_database),
) -> SimulationService:
    """Build the administrative simulation service."""
    return SimulationService(SimulationRepository(database))


def get_capital_movement_service(
    database: Database = Depends(get_database),
) -> CapitalMovementService:
    """Build the append-only capital movement service."""
    return CapitalMovementService(CapitalMovementRepository(database))


def get_strategy_definition_service(
    database: Database = Depends(get_database),
) -> StrategyDefinitionService:
    """Build the validated strategy-definition CRUD service."""

    return StrategyDefinitionService(
        PostgresStrategyDefinitionRepository(database),
        available_indicators=builtin_indicator_capabilities(),
    )


def get_settings_service(
    database: Database = Depends(get_database),
) -> SettingsService:
    """Build the system settings service."""
    return SettingsService(SettingsRepository(database))
