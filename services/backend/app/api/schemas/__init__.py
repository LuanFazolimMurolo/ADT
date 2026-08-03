"""Explicit request and response contracts for the ADT HTTP API."""

from app.api.schemas.assets import AssetListResponse, AssetPriceResponse, AssetResponse
from app.api.schemas.auth import AdminMeResponse
from app.api.schemas.collection import (
    ContinuousCollectionStatusResponse,
    ContinuousTargetResultResponse,
)
from app.api.schemas.common import (
    ApiSchema,
    FinancialDecimal,
    FinancialDecimalStringInput,
    JsonObject,
    NonBlankText,
    NonZeroFinancialDecimal,
    NonZeroFinancialDecimalStringInput,
    PositiveFinancialDecimal,
    PositiveFinancialDecimalStringInput,
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
from app.api.schemas.paper_trading import (
    PaperFillListResponse,
    PaperFillResponse,
    PaperOrderListResponse,
    PaperOrderResponse,
    PaperPortfolioResponse,
    PaperRunnerSessionResultResponse,
    PaperRunnerStatusResponse,
    PaperSessionDetailResponse,
    PaperSessionListResponse,
    PaperSessionSummaryResponse,
)
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
from app.api.schemas.strategies import (
    StrategyDefinitionArchiveRequest,
    StrategyDefinitionCreateRequest,
    StrategyDefinitionListResponse,
    StrategyDefinitionReplaceRequest,
    StrategyDefinitionResponse,
    StrategyParameterInput,
    StrategyParameterResponse,
)
from app.api.schemas.system import SystemStatus

__all__ = [
    "AdminMeResponse",
    "AssetListResponse",
    "AssetPriceResponse",
    "AssetResponse",
    "ApiSchema",
    "CapitalMovementResponse",
    "CapitalMovementType",
    "ContinuousTargetResultResponse",
    "ContinuousCollectionStatusResponse",
    "ErrorDetail",
    "ErrorPayload",
    "ErrorResponse",
    "FinancialDecimal",
    "FinancialDecimalStringInput",
    "JsonObject",
    "MovementCreateRequest",
    "MovementCreateType",
    "MovementListResponse",
    "NonBlankText",
    "NonZeroFinancialDecimal",
    "NonZeroFinancialDecimalStringInput",
    "PageMeta",
    "PageParams",
    "PaginatedResponse",
    "PositiveFinancialDecimal",
    "PositiveFinancialDecimalStringInput",
    "PaperFillListResponse",
    "PaperFillResponse",
    "PaperOrderListResponse",
    "PaperOrderResponse",
    "PaperPortfolioResponse",
    "PaperRunnerSessionResultResponse",
    "PaperRunnerStatusResponse",
    "PaperSessionDetailResponse",
    "PaperSessionListResponse",
    "PaperSessionSummaryResponse",
    "PublicSimulationSummaryResponse",
    "SettingPatchRequest",
    "SettingResponse",
    "SettingsListResponse",
    "SimulationCreateRequest",
    "SimulationDetailResponse",
    "SimulationListItem",
    "SimulationListResponse",
    "SimulationStatus",
    "StrategyDefinitionArchiveRequest",
    "StrategyDefinitionCreateRequest",
    "StrategyDefinitionListResponse",
    "StrategyDefinitionReplaceRequest",
    "StrategyDefinitionResponse",
    "StrategyParameterInput",
    "StrategyParameterResponse",
    "SystemStatus",
]
