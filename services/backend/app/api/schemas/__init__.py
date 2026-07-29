"""Explicit request and response contracts for the ADT HTTP API."""

from app.api.schemas.auth import AdminMeResponse
from app.api.schemas.common import (
    ApiSchema,
    FinancialDecimal,
    JsonObject,
    NonBlankText,
    NonZeroFinancialDecimal,
    PositiveFinancialDecimal,
)
from app.api.schemas.errors import ErrorDetail, ErrorPayload, ErrorResponse
from app.api.schemas.movements import (
    CapitalMovementResponse,
    CapitalMovementType,
    MovementCreateRequest,
    MovementCreateType,
    MovementListResponse,
)
from app.api.schemas.pagination import PageMeta, PageParams, PaginatedResponse
from app.api.schemas.public import PublicSimulationSummaryResponse
from app.api.schemas.settings import (
    SettingPatchRequest,
    SettingResponse,
    SettingsListResponse,
)
from app.api.schemas.simulations import (
    SimulationCreateRequest,
    SimulationDetailResponse,
    SimulationListItem,
    SimulationListResponse,
    SimulationStatus,
)

__all__ = [
    "AdminMeResponse",
    "ApiSchema",
    "CapitalMovementResponse",
    "CapitalMovementType",
    "ErrorDetail",
    "ErrorPayload",
    "ErrorResponse",
    "FinancialDecimal",
    "JsonObject",
    "MovementCreateRequest",
    "MovementCreateType",
    "MovementListResponse",
    "NonBlankText",
    "NonZeroFinancialDecimal",
    "PageMeta",
    "PageParams",
    "PaginatedResponse",
    "PositiveFinancialDecimal",
    "PublicSimulationSummaryResponse",
    "SettingPatchRequest",
    "SettingResponse",
    "SettingsListResponse",
    "SimulationCreateRequest",
    "SimulationDetailResponse",
    "SimulationListItem",
    "SimulationListResponse",
    "SimulationStatus",
]
