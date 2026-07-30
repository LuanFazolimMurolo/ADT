"""Simulation request and administrative response contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Self
from uuid import UUID

from pydantic import StringConstraints

from app.api.schemas.common import (
    ApiSchema,
    FinancialDecimal,
    NonBlankText,
    PositiveFinancialDecimal,
    PositiveFinancialDecimalStringInput,
)
from app.api.schemas.pagination import PaginatedResponse

if TYPE_CHECKING:
    from app.domain.models import SimulationDetails

SimulationName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
]
CurrencyCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=3, max_length=3, pattern=r"^[A-Z]{3}$"),
]


class SimulationStatus(StrEnum):
    """States enforced by ``simulation_runs`` constraints."""

    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class SimulationCreateRequest(ApiSchema):
    """Fields accepted when opening a paper-trading simulation."""

    name: SimulationName
    initial_capital: PositiveFinancialDecimalStringInput
    currency: CurrencyCode


class SimulationListItem(ApiSchema):
    """One simulation in the administrative history."""

    id: UUID
    name: NonBlankText
    status: SimulationStatus
    currency: NonBlankText
    initial_capital: PositiveFinancialDecimal
    current_balance: FinancialDecimal
    total_profit_loss: FinancialDecimal
    started_at: datetime
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, details: SimulationDetails) -> Self:
        """Flatten the domain's nested simulation record for an API list item."""

        simulation = details.simulation
        return cls(
            id=simulation.id,
            name=simulation.name,
            status=SimulationStatus(simulation.status.value),
            currency=simulation.currency,
            initial_capital=simulation.initial_capital,
            current_balance=details.current_balance,
            total_profit_loss=details.total_profit_loss,
            started_at=simulation.started_at,
            ended_at=simulation.ended_at,
            created_at=simulation.created_at,
            updated_at=simulation.updated_at,
        )


class SimulationDetailResponse(SimulationListItem):
    """Simulation history plus calculated ledger totals."""

    created_by: UUID

    @classmethod
    def from_domain(cls, details: SimulationDetails) -> Self:
        """Flatten a simulation and its calculated totals for the detail API."""

        simulation = details.simulation
        return cls(
            id=simulation.id,
            name=simulation.name,
            status=SimulationStatus(simulation.status.value),
            currency=simulation.currency,
            initial_capital=simulation.initial_capital,
            started_at=simulation.started_at,
            ended_at=simulation.ended_at,
            created_at=simulation.created_at,
            updated_at=simulation.updated_at,
            created_by=simulation.created_by,
            current_balance=details.current_balance,
            total_profit_loss=details.total_profit_loss,
        )


class SimulationListResponse(PaginatedResponse[SimulationListItem]):
    """Paginated simulations ordered by the route's repository query."""
