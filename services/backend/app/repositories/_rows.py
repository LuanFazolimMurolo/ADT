"""Strict conversion from psycopg dictionary rows to domain records."""

from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from psycopg.rows import DictRow

from app.domain.models import (
    CapitalMovement,
    JsonObject,
    JsonValue,
    LedgerMovementType,
    PublicSimulationSummary,
    SimulationDetails,
    SimulationRun,
    SimulationStatus,
    SystemSetting,
)


def simulation_run_from_row(row: DictRow) -> SimulationRun:
    """Build a simulation record from a typed database row."""
    return SimulationRun(
        id=cast(UUID, row["id"]),
        name=cast(str, row["name"]),
        status=SimulationStatus(cast(str, row["status"])),
        currency=cast(str, row["currency"]),
        initial_capital=cast(Decimal, row["initial_capital"]),
        started_at=cast(datetime, row["started_at"]),
        ended_at=cast(datetime | None, row["ended_at"]),
        created_by=cast(UUID, row["created_by"]),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
    )


def simulation_details_from_row(row: DictRow) -> SimulationDetails:
    """Build a simulation and its ledger totals from a database row."""
    return SimulationDetails(
        simulation=simulation_run_from_row(row),
        current_balance=cast(Decimal, row["current_balance"]),
        total_profit_loss=cast(Decimal, row["total_profit_loss"]),
    )


def public_summary_from_row(row: DictRow) -> PublicSimulationSummary:
    """Build the UUID-free public simulation projection."""
    return PublicSimulationSummary(
        simulation_name=cast(str, row["simulation_name"]),
        currency=cast(str, row["currency"]),
        initial_capital=cast(Decimal, row["initial_capital"]),
        current_balance=cast(Decimal, row["current_balance"]),
        total_profit_loss=cast(Decimal, row["total_profit_loss"]),
        started_at=cast(datetime, row["started_at"]),
        status=SimulationStatus(cast(str, row["status"])),
    )


def movement_from_row(row: DictRow) -> CapitalMovement:
    """Build an immutable ledger record from a database row."""
    raw_metadata = row.get("metadata")
    return CapitalMovement(
        id=cast(UUID, row["id"]),
        simulation_id=cast(UUID, row["simulation_id"]),
        type=LedgerMovementType(cast(str, row["type"])),
        amount=cast(Decimal, row["amount"]),
        reason=cast(str, row["reason"]),
        reference_id=cast(UUID | None, row["reference_id"]),
        created_by=cast(UUID | None, row["created_by"]),
        created_at=cast(datetime, row["created_at"]),
        metadata=cast(JsonObject | None, raw_metadata),
    )


def setting_from_row(row: DictRow) -> SystemSetting:
    """Build a system setting from a database row."""
    return SystemSetting(
        key=cast(str, row["key"]),
        value=cast(JsonValue, row["value"]),
        description=cast(str, row["description"]),
        is_public=cast(bool, row["is_public"]),
        updated_by=cast(UUID | None, row["updated_by"]),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
    )
