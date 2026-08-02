"""Resolve application resources initialized by the FastAPI lifespan."""

from typing import cast

from fastapi import Depends, Request

from app.auth import SupabaseJWTVerifier
from app.database import Database
from app.market_data.asset_catalog import AssetMarketService
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
