import { getPublicConfig } from "../config/env";
import { getSupabaseClient } from "../lib/supabase";
import { signOutLocally } from "../auth/signOut";
import { getPersistedSupabaseAccessToken } from "../auth/supabaseSessionStorage";
import type {
  AdminMe,
  AppMe,
  AppPaperChartAnnotationPage,
  AppPaperPeriodMetricsSeries,
  AppPaperPortfolioTimelinePage,
  AppPaperSessionCatalogResponse,
  AppPaperSessionDetail,
  AppPaperTradePage,
  ApiErrorEnvelope,
  CapitalMovement,
  HealthResponse,
  MovementCreateRequest,
  MovementListResponse,
  MarketCandlePageResponse,
  PaperChartAnnotationPageResponse,
  PaperDashboardResponse,
  PaperPeriodGranularity,
  PaperPeriodMetricsSeriesResponse,
  PaperPortfolioTimelinePageResponse,
  PaperTradeJournalPageResponse,
  PaperTradeStatus,
  PublicSimulationSummary,
  Setting,
  SettingPatchRequest,
  SettingsListResponse,
  SimulationCreateRequest,
  SimulationDetail,
  SimulationListResponse,
} from "../types/api";
import type { SystemStatus } from "../types/system";

const STATUS_MESSAGES: Record<number, string> = {
  401: "Sua sessão expirou. Entre novamente.",
  403: "Esta conta não possui acesso administrativo.",
  404: "O recurso solicitado não foi encontrado.",
  409: "A operação conflita com o estado atual dos dados.",
  422: "Revise os dados informados.",
  503: "O serviço está temporariamente indisponível.",
};

const browserFetch: typeof fetch = (input, init) =>
  globalThis.fetch(input, init);

const NETWORK_ERROR_MESSAGE =
  "Não foi possível conectar à API. Tente novamente em instantes.";
const INVALID_RESPONSE_MESSAGE = "A API retornou uma resposta inválida.";
const SESSION_ERROR_MESSAGE =
  "Não foi possível validar sua sessão. Entre novamente.";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface PaperTradeJournalFilters {
  sessionId?: string;
  baseAsset?: string;
  quoteAsset?: string;
  timeframe?: string;
  strategyName?: string;
  strategyVersion?: string;
  status?: PaperTradeStatus;
  openedFrom?: string;
  openedBefore?: string;
  closedFrom?: string;
  closedBefore?: string;
}

export interface AppPaperTradeFilters {
  status?: PaperTradeStatus;
  openedFrom?: string;
  openedBefore?: string;
  closedFrom?: string;
  closedBefore?: string;
}

export interface AppPaperPortfolioTimelineQuery {
  before?: string;
  limit?: number;
}

export interface AppPaperPeriodMetricsQuery {
  periodFrom: string;
  periodBefore: string;
  granularity: PaperPeriodGranularity;
}

export interface PaperPeriodMetricsFilters {
  quoteAsset: string;
  periodFrom: string;
  periodBefore: string;
  sessionId?: string;
  baseAsset?: string;
  timeframe?: string;
  strategyName?: string;
  strategyVersion?: string;
}

export interface MarketCandleQuery {
  timeframe: string;
  before?: string;
  limit?: number;
}

export interface PaperChartAnnotationQuery {
  start: string;
  before: string;
  limit?: number;
}

export interface PaperPortfolioTimelineQuery {
  before?: string;
  limit?: number;
}

function appendQueryValue(
  params: URLSearchParams,
  key: string,
  value: string | undefined,
): void {
  if (value) params.set(key, value);
}

export interface ApiClientOptions {
  baseUrl?: string;
  getAccessToken?: () => Promise<string | null>;
  refreshAccessToken?: () => Promise<string | null>;
  onAuthenticationFailure?: (failedAccessToken: string | null) => Promise<void>;
  fetchImplementation?: typeof fetch;
}

export class ApiClient {
  private readonly fetchImplementation: typeof fetch;

  constructor(private readonly options: ApiClientOptions) {
    this.fetchImplementation = options.fetchImplementation ?? browserFetch;
  }

  private async parse<T>(response: Response): Promise<T> {
    const text = await response.text();
    const requestId = response.headers.get("X-Request-ID") ?? undefined;
    if (!text.trim()) {
      throw new ApiError(
        response.status,
        "invalid_response",
        INVALID_RESPONSE_MESSAGE,
        undefined,
        requestId,
      );
    }
    try {
      return JSON.parse(text) as T;
    } catch {
      throw new ApiError(
        response.status,
        "invalid_response",
        INVALID_RESPONSE_MESSAGE,
        undefined,
        requestId,
      );
    }
  }

  private async performRequest<T>(
    path: string,
    options: RequestInit,
    token: string | null,
  ): Promise<T> {
    const headers = new Headers(options.headers);
    headers.set("Accept", "application/json");
    if (options.body) headers.set("Content-Type", "application/json");
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const baseUrl = this.options.baseUrl ?? getPublicConfig().apiUrl;
    let response: Response;
    try {
      response = await this.fetchImplementation(`${baseUrl}${path}`, {
        ...options,
        headers,
      });
    } catch (error) {
      if (error instanceof TypeError) {
        throw new ApiError(0, "network_error", NETWORK_ERROR_MESSAGE);
      }
      throw error;
    }

    if (response.ok) return this.parse<T>(response);

    let envelope: ApiErrorEnvelope | undefined;
    try {
      envelope = await this.parse<ApiErrorEnvelope>(response);
    } catch {
      envelope = undefined;
    }
    throw new ApiError(
      response.status,
      envelope?.error?.code ?? `http_${response.status}`,
      envelope?.error?.message ??
        STATUS_MESSAGES[response.status] ??
        "Não foi possível concluir a solicitação.",
      envelope?.error?.details,
      response.headers.get("X-Request-ID") ?? undefined,
    );
  }

  async request<T>(
    path: string,
    options: RequestInit = {},
    authenticated = true,
  ): Promise<T> {
    const method = (options.method ?? "GET").toUpperCase();
    let token: string | null = null;
    if (authenticated && this.options.getAccessToken) {
      try {
        token = await this.options.getAccessToken();
      } catch {
        await this.options.onAuthenticationFailure?.(null);
        throw new ApiError(0, "session_unavailable", SESSION_ERROR_MESSAGE);
      }
    }

    try {
      return await this.performRequest<T>(path, options, token);
    } catch (error) {
      if (
        error instanceof ApiError &&
        error.status === 401 &&
        method === "GET" &&
        authenticated &&
        this.options.refreshAccessToken
      ) {
        let refreshedToken: string | null = null;
        try {
          refreshedToken = await this.options.refreshAccessToken();
        } catch {
          await this.options.onAuthenticationFailure?.(token);
          throw error;
        }
        if (refreshedToken) {
          try {
            return await this.performRequest<T>(path, options, refreshedToken);
          } catch (retryError) {
            if (retryError instanceof ApiError && retryError.status === 401) {
              await this.options.onAuthenticationFailure?.(refreshedToken);
            }
            throw retryError;
          }
        }
        await this.options.onAuthenticationFailure?.(token);
      } else if (
        error instanceof ApiError &&
        authenticated &&
        error.status === 401
      ) {
        await this.options.onAuthenticationFailure?.(token);
      }
      throw error;
    }
  }

  getSystemStatus(): Promise<SystemStatus> {
    return this.request("/api/v1/system/status", {}, false);
  }

  getHealth(): Promise<HealthResponse> {
    return this.request("/health", {}, false);
  }

  getDatabaseHealth(): Promise<HealthResponse> {
    return this.request("/health/database", {}, false);
  }

  getReadiness(): Promise<HealthResponse> {
    return this.request("/health/readiness", {}, false);
  }

  getPublicSimulation(): Promise<PublicSimulationSummary | null> {
    return this.request("/api/v1/public/simulation", {}, false);
  }

  getAppMe(): Promise<AppMe> {
    return this.request("/api/v1/app/me");
  }

  getAppPaperSessions(
    page = 1,
    pageSize = 20,
  ): Promise<AppPaperSessionCatalogResponse> {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    return this.request(
      `/api/v1/app/paper-trading/sessions?${params.toString()}`,
    );
  }

  getAppPaperSession(sessionId: string): Promise<AppPaperSessionDetail> {
    return this.request(
      `/api/v1/app/paper-trading/sessions/${encodeURIComponent(sessionId)}`,
    );
  }

  getAppPaperChartAnnotations(
    sessionId: string,
    query: PaperChartAnnotationQuery,
  ): Promise<AppPaperChartAnnotationPage> {
    const params = new URLSearchParams({
      start: query.start,
      before: query.before,
    });
    if (query.limit !== undefined) params.set("limit", String(query.limit));
    return this.request(
      `/api/v1/app/paper-trading/sessions/${encodeURIComponent(sessionId)}/chart-annotations?${params.toString()}`,
    );
  }

  getAppPaperTrades(
    sessionId: string,
    filters: AppPaperTradeFilters = {},
    page = 1,
    pageSize = 20,
  ): Promise<AppPaperTradePage> {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    appendQueryValue(params, "status", filters.status);
    appendQueryValue(params, "opened_from", filters.openedFrom);
    appendQueryValue(params, "opened_before", filters.openedBefore);
    appendQueryValue(params, "closed_from", filters.closedFrom);
    appendQueryValue(params, "closed_before", filters.closedBefore);
    return this.request(
      `/api/v1/app/paper-trading/sessions/${encodeURIComponent(sessionId)}/trades?${params.toString()}`,
    );
  }

  getAppPaperPortfolioTimeline(
    sessionId: string,
    query: AppPaperPortfolioTimelineQuery = {},
  ): Promise<AppPaperPortfolioTimelinePage> {
    const params = new URLSearchParams();
    appendQueryValue(params, "before", query.before);
    if (query.limit !== undefined) params.set("limit", String(query.limit));
    const suffix = params.size ? `?${params.toString()}` : "";
    return this.request(
      `/api/v1/app/paper-trading/sessions/${encodeURIComponent(sessionId)}/portfolio-timeline${suffix}`,
    );
  }

  getAppPaperPeriodMetrics(
    sessionId: string,
    query: AppPaperPeriodMetricsQuery,
  ): Promise<AppPaperPeriodMetricsSeries> {
    const params = new URLSearchParams({
      period_from: query.periodFrom,
      period_before: query.periodBefore,
      granularity: query.granularity,
    });
    return this.request(
      `/api/v1/app/paper-trading/sessions/${encodeURIComponent(sessionId)}/period-metrics?${params.toString()}`,
    );
  }

  getAdminMe(): Promise<AdminMe> {
    return this.request("/api/v1/admin/me");
  }

  getMarketCandles(
    baseAsset: string,
    quoteAsset: string,
    query: MarketCandleQuery,
  ): Promise<MarketCandlePageResponse> {
    const params = new URLSearchParams({ timeframe: query.timeframe });
    appendQueryValue(params, "before", query.before);
    if (query.limit !== undefined) params.set("limit", String(query.limit));
    return this.request(
      `/api/v1/admin/market-data/candles/${encodeURIComponent(baseAsset)}/${encodeURIComponent(quoteAsset)}?${params.toString()}`,
    );
  }

  getAppMarketCandles(
    baseAsset: string,
    quoteAsset: string,
    query: MarketCandleQuery,
  ): Promise<MarketCandlePageResponse> {
    const params = new URLSearchParams({ timeframe: query.timeframe });
    appendQueryValue(params, "before", query.before);
    if (query.limit !== undefined) params.set("limit", String(query.limit));
    return this.request(
      `/api/v1/app/market-data/candles/${encodeURIComponent(baseAsset)}/${encodeURIComponent(quoteAsset)}?${params.toString()}`,
    );
  }

  getPaperChartAnnotations(
    sessionId: string,
    query: PaperChartAnnotationQuery,
  ): Promise<PaperChartAnnotationPageResponse> {
    const params = new URLSearchParams({
      start: query.start,
      before: query.before,
    });
    if (query.limit !== undefined) params.set("limit", String(query.limit));
    return this.request(
      `/api/v1/admin/paper-trading/sessions/${encodeURIComponent(sessionId)}/chart-annotations?${params.toString()}`,
    );
  }

  getPaperPortfolioTimeline(
    sessionId: string,
    query: PaperPortfolioTimelineQuery = {},
  ): Promise<PaperPortfolioTimelinePageResponse> {
    const params = new URLSearchParams();
    appendQueryValue(params, "before", query.before);
    if (query.limit !== undefined) params.set("limit", String(query.limit));
    const suffix = params.size > 0 ? `?${params.toString()}` : "";
    return this.request(
      `/api/v1/admin/paper-trading/sessions/${encodeURIComponent(sessionId)}/portfolio-timeline${suffix}`,
    );
  }

  getPaperTradingDashboard(
    page = 1,
    pageSize = 20,
  ): Promise<PaperDashboardResponse> {
    return this.request(
      `/api/v1/admin/paper-trading/dashboard?page=${page}&page_size=${pageSize}`,
    );
  }

  getPaperTradeJournal(
    filters: PaperTradeJournalFilters = {},
    page = 1,
    pageSize = 20,
  ): Promise<PaperTradeJournalPageResponse> {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    appendQueryValue(params, "session_id", filters.sessionId);
    appendQueryValue(params, "base_asset", filters.baseAsset);
    appendQueryValue(params, "quote_asset", filters.quoteAsset);
    appendQueryValue(params, "timeframe", filters.timeframe);
    appendQueryValue(params, "strategy_name", filters.strategyName);
    appendQueryValue(params, "strategy_version", filters.strategyVersion);
    appendQueryValue(params, "status", filters.status);
    appendQueryValue(params, "opened_from", filters.openedFrom);
    appendQueryValue(params, "opened_before", filters.openedBefore);
    appendQueryValue(params, "closed_from", filters.closedFrom);
    appendQueryValue(params, "closed_before", filters.closedBefore);

    return this.request(
      `/api/v1/admin/paper-trading/journal?${params.toString()}`,
    );
  }

  getPaperPeriodMetrics(
    filters: PaperPeriodMetricsFilters,
    granularity: PaperPeriodGranularity = "DAILY",
  ): Promise<PaperPeriodMetricsSeriesResponse> {
    const params = new URLSearchParams({
      quote_asset: filters.quoteAsset,
      period_from: filters.periodFrom,
      period_before: filters.periodBefore,
      granularity,
    });
    appendQueryValue(params, "session_id", filters.sessionId);
    appendQueryValue(params, "base_asset", filters.baseAsset);
    appendQueryValue(params, "timeframe", filters.timeframe);
    appendQueryValue(params, "strategy_name", filters.strategyName);
    appendQueryValue(params, "strategy_version", filters.strategyVersion);

    return this.request(
      `/api/v1/admin/paper-trading/period-metrics?${params.toString()}`,
    );
  }

  listSimulations(page = 1, pageSize = 20): Promise<SimulationListResponse> {
    return this.request(
      `/api/v1/admin/simulations?page=${page}&page_size=${pageSize}`,
    );
  }

  createSimulation(
    payload: SimulationCreateRequest,
  ): Promise<SimulationDetail> {
    return this.request("/api/v1/admin/simulations", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  getSimulation(id: string): Promise<SimulationDetail> {
    return this.request(`/api/v1/admin/simulations/${encodeURIComponent(id)}`);
  }

  completeSimulation(id: string): Promise<SimulationDetail> {
    return this.request(
      `/api/v1/admin/simulations/${encodeURIComponent(id)}/complete`,
      {
        method: "POST",
      },
    );
  }

  cancelSimulation(id: string): Promise<SimulationDetail> {
    return this.request(
      `/api/v1/admin/simulations/${encodeURIComponent(id)}/cancel`,
      {
        method: "POST",
      },
    );
  }

  listMovements(
    id: string,
    page = 1,
    pageSize = 20,
  ): Promise<MovementListResponse> {
    return this.request(
      `/api/v1/admin/simulations/${encodeURIComponent(id)}/movements?page=${page}&page_size=${pageSize}`,
    );
  }

  createMovement(
    id: string,
    payload: MovementCreateRequest,
  ): Promise<CapitalMovement> {
    return this.request(
      `/api/v1/admin/simulations/${encodeURIComponent(id)}/movements`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    );
  }

  listSettings(): Promise<SettingsListResponse> {
    return this.request("/api/v1/admin/settings");
  }

  updateSetting(
    key: string,
    value: SettingPatchRequest["value"],
  ): Promise<Setting> {
    return this.request(`/api/v1/admin/settings/${encodeURIComponent(key)}`, {
      method: "PATCH",
      body: JSON.stringify({ value }),
    });
  }
}

export const apiClient = new ApiClient({
  getAccessToken: async () => {
    const { data } = await getSupabaseClient().auth.getSession();
    return data.session?.access_token ?? null;
  },
  refreshAccessToken: async () => {
    const { data } = await getSupabaseClient().auth.refreshSession();
    return data.session?.access_token ?? null;
  },
  onAuthenticationFailure: async (failedAccessToken) => {
    const persistedAccessToken = getPersistedSupabaseAccessToken();
    if (
      failedAccessToken !== null &&
      persistedAccessToken !== null &&
      failedAccessToken !== persistedAccessToken
    ) {
      return;
    }
    signOutLocally();
  },
});
