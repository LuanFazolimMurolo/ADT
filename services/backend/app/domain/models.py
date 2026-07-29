"""Typed domain records for Phase 1B persistence."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TypeAlias
from uuid import UUID

JsonPrimitive: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class SimulationStatus(StrEnum):
    """Persisted lifecycle states for a simulation."""

    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class AdministrativeMovementType(StrEnum):
    """Movement names accepted by the administrative API."""

    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    ADJUSTMENT = "ADJUSTMENT"


class LedgerMovementType(StrEnum):
    """Movement names stored in the append-only PostgreSQL ledger."""

    INITIAL_CAPITAL = "INITIAL_CAPITAL"
    ADMIN_DEPOSIT = "ADMIN_DEPOSIT"
    ADMIN_WITHDRAWAL = "ADMIN_WITHDRAWAL"
    TRADE_PROFIT = "TRADE_PROFIT"
    TRADE_LOSS = "TRADE_LOSS"
    FEE = "FEE"
    ADJUSTMENT = "ADJUSTMENT"


@dataclass(frozen=True, slots=True)
class PublicSimulationSummary:
    """Safe public projection; deliberately has no simulation UUID."""

    simulation_name: str
    currency: str
    initial_capital: Decimal
    current_balance: Decimal
    total_profit_loss: Decimal
    started_at: datetime
    status: SimulationStatus


@dataclass(frozen=True, slots=True)
class SimulationRun:
    """Persisted simulation data visible to an administrator."""

    id: UUID
    name: str
    status: SimulationStatus
    currency: str
    initial_capital: Decimal
    started_at: datetime
    ended_at: datetime | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SimulationDetails:
    """A simulation together with its calculated ledger totals."""

    simulation: SimulationRun
    current_balance: Decimal
    total_profit_loss: Decimal


@dataclass(frozen=True, slots=True)
class CapitalMovement:
    """One immutable ledger movement and its optional audit metadata."""

    id: UUID
    simulation_id: UUID
    type: LedgerMovementType
    amount: Decimal
    reason: str
    reference_id: UUID | None
    created_by: UUID | None
    created_at: datetime
    metadata: JsonObject | None = None


@dataclass(frozen=True, slots=True)
class SystemSetting:
    """One non-secret persisted system setting."""

    key: str
    value: JsonValue
    description: str
    is_public: bool
    updated_by: UUID | None
    created_at: datetime
    updated_at: datetime
