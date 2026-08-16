/**
 * Stable application-facing aliases generated from the backend OpenAPI schema.
 *
 * Run `npm run generate:api` after changing a Pydantic request/response model.
 */
import type { components } from "./openapi.generated";

type ApiSchemas = components["schemas"];

export type JsonValue = ApiSchemas["JsonValue-Input"];
export type AppMe = ApiSchemas["AppMeResponse"];
export type AppPaperSessionCatalogItem =
  ApiSchemas["AppPaperSessionCatalogItemResponse"];
export type AppPaperSessionCatalogResponse =
  ApiSchemas["AppPaperSessionCatalogResponse"];
export type AppPaperSessionDetail = ApiSchemas["AppPaperSessionDetailResponse"];
export type AppPaperChartAnnotationPage =
  ApiSchemas["AppPaperChartAnnotationPageResponse"];
export type AppPaperChartOrderAnnotation =
  ApiSchemas["AppPaperChartOrderAnnotationResponse"];
export type AppPaperChartFillAnnotation =
  ApiSchemas["AppPaperChartFillAnnotationResponse"];
export type AppPaperTrade = ApiSchemas["AppPaperTradeResponse"];
export type AppPaperTradeTotals = ApiSchemas["AppPaperTradeTotalsResponse"];
export type AppPaperTradePage = ApiSchemas["AppPaperTradePageResponse"];
export type AppPaperPortfolioObservation =
  ApiSchemas["AppPaperPortfolioObservationResponse"];
export type AppPaperPortfolioTimelinePage =
  ApiSchemas["AppPaperPortfolioTimelinePageResponse"];
export type AppPaperPeriodMetricsBucket =
  ApiSchemas["AppPaperPeriodMetricsBucketResponse"];
export type AppPaperPeriodMetricsTotals =
  ApiSchemas["AppPaperPeriodMetricsTotalsResponse"];
export type AppPaperPeriodMetricsSeries =
  ApiSchemas["AppPaperPeriodMetricsSeriesResponse"];
export type AdminMe = ApiSchemas["AdminMeResponse"];
export type HealthResponse = ApiSchemas["HealthResponse"];
export type PageMeta = ApiSchemas["PageMeta"];

export type SimulationStatus = ApiSchemas["SimulationStatus"];
export type SimulationListItem = ApiSchemas["SimulationListItem"];
export type SimulationDetail = ApiSchemas["SimulationDetailResponse"];
export type SimulationListResponse = ApiSchemas["SimulationListResponse"];
export type SimulationCreateRequest = ApiSchemas["SimulationCreateRequest"];

export type MovementCreateType = ApiSchemas["MovementCreateType"];
export type CapitalMovementType = ApiSchemas["CapitalMovementType"];
export type MovementCreateRequest = ApiSchemas["MovementCreateRequest"];
export type CapitalMovement = ApiSchemas["CapitalMovementResponse"];
export type MovementListResponse = ApiSchemas["MovementListResponse"];

export type SettingPatchRequest = ApiSchemas["SettingPatchRequest"];
export type Setting = ApiSchemas["SettingResponse"];
export type SettingsListResponse = ApiSchemas["SettingsListResponse"];

export type PublicSimulationSummary =
  ApiSchemas["PublicSimulationSummaryResponse"];
export type ApiErrorEnvelope = ApiSchemas["ErrorResponse"];

export type PaperRunnerSessionStatus = ApiSchemas["PaperRunnerSessionStatus"];
export type PaperRunnerCycleStatus = ApiSchemas["PaperRunnerCycleStatus"];
export type PaperMarketRegime = ApiSchemas["MarketRegimeKind"];
export type PaperTrendDirection = ApiSchemas["TrendDirection"];

export type PaperDashboardMetrics = ApiSchemas["PaperDashboardMetricsResponse"];
export type PaperDashboardPortfolio = ApiSchemas["PaperPortfolioResponse"];
export type PaperDashboardPosition =
  ApiSchemas["PaperDashboardPositionResponse"];
export type PaperDashboardRegime = ApiSchemas["PaperDashboardRegimeResponse"];
export type PaperDashboardRunnerResult =
  ApiSchemas["PaperDashboardRunnerResultResponse"];
export type PaperDashboardSession = ApiSchemas["PaperDashboardSessionResponse"];
export type PaperDashboardTotals = ApiSchemas["PaperDashboardTotalsResponse"];
export type PaperDashboardRunnerCycle =
  ApiSchemas["PaperDashboardRunnerCycleResponse"];
export type PaperDashboardResponse = ApiSchemas["PaperDashboardResponse"];
export type PaperTradeStatus = ApiSchemas["PaperTradeStatus"];
export type PaperTradeJournalRecord =
  ApiSchemas["PaperTradeJournalRecordResponse"];
export type PaperTradeJournalTotals =
  ApiSchemas["PaperTradeJournalTotalsResponse"];
export type PaperTradeJournalPageResponse =
  ApiSchemas["PaperTradeJournalPageResponse"];
export type PaperPeriodGranularity = ApiSchemas["PaperPeriodGranularity"];
export type PaperPeriodMetricsBucket =
  ApiSchemas["PaperPeriodMetricsBucketResponse"];
export type PaperPeriodMetricsTotals =
  ApiSchemas["PaperPeriodMetricsTotalsResponse"];
export type PaperPeriodMetricsSeriesResponse =
  ApiSchemas["PaperPeriodMetricsSeriesResponse"];
export type PaperPortfolioObservation =
  ApiSchemas["PaperPortfolioObservationResponse"];
export type PaperPortfolioTimelinePageResponse =
  ApiSchemas["PaperPortfolioTimelinePageResponse"];

export type MarketCandle = ApiSchemas["MarketCandleResponse"];
export type MarketCandlePageResponse = ApiSchemas["MarketCandlePageResponse"];

export type RawDatasetIntegrityResponse =
  ApiSchemas["RawDatasetIntegrityResponse"];
export type RawDatasetResponse = ApiSchemas["RawDatasetResponse"];
export type RawDatasetPageResponse = ApiSchemas["RawDatasetPageResponse"];
export type PaperChartAnnotationPageResponse =
  ApiSchemas["PaperChartAnnotationPageResponse"];
export type PaperChartOrderAnnotation =
  ApiSchemas["PaperChartOrderAnnotationResponse"];
export type PaperChartFillAnnotation =
  ApiSchemas["PaperChartFillAnnotationResponse"];
export type PaperChartFillRole = ApiSchemas["PaperChartFillRole"];

export type MarketOperationType = ApiSchemas["MarketOperationType"];
export type MarketOperationState = ApiSchemas["MarketOperationState"];
export type MarketOperationFailureCode =
  ApiSchemas["MarketOperationFailureCode"];
export type MarketOperationDataset =
  ApiSchemas["MarketOperationDatasetResponse"];
export type MarketOperationBackfillPreviewRequest =
  ApiSchemas["MarketOperationBackfillPreviewRequest"];
export type MarketOperationIncrementalPreviewRequest =
  ApiSchemas["MarketOperationIncrementalPreviewRequest"];
export type MarketOperationPlanPreview =
  ApiSchemas["MarketOperationPlanPreviewResponse"];
export type IncrementalMarketOperationPlanPreview =
  ApiSchemas["IncrementalMarketOperationPlanPreviewResponse"];
export type MarketOperationSubmitRequest =
  ApiSchemas["MarketOperationSubmitRequest"];
export type MarketOperationControlRequest =
  ApiSchemas["MarketOperationControlRequest"];
export type MarketOperation = ApiSchemas["MarketOperationResponse"];
export type MarketOperationList = ApiSchemas["MarketOperationListResponse"];
export type MarketOperationTarget = ApiSchemas["MarketOperationTargetResponse"];
export type MarketOperationTargetList =
  ApiSchemas["MarketOperationTargetListResponse"];
