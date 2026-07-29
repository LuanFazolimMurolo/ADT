"""Public response contracts with an intentionally narrow projection."""

from datetime import datetime

from pydantic import AliasChoices, Field

from app.api.schemas.common import (
    ApiSchema,
    FinancialDecimal,
    NonBlankText,
    PositiveFinancialDecimal,
)
from app.api.schemas.simulations import SimulationStatus


class PublicSimulationSummaryResponse(ApiSchema):
    """Active simulation summary without UUID or administrator identifiers."""

    name: NonBlankText = Field(validation_alias=AliasChoices("name", "simulation_name"))
    currency: NonBlankText
    initial_capital: PositiveFinancialDecimal
    current_balance: FinancialDecimal
    total_profit_loss: FinancialDecimal
    started_at: datetime
    status: SimulationStatus
