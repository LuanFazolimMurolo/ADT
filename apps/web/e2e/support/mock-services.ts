import type { BrowserContext, Request, Route } from "@playwright/test";
import type {
  AppPaperChartAnnotationPage,
  AppPaperPeriodMetricsSeries,
  AppPaperPortfolioTimelinePage,
  AppPaperSessionCatalogResponse,
  AppPaperSessionDetail,
  AppPaperTradePage,
  CapitalMovement,
  JsonValue,
  MovementCreateRequest,
  MarketCandlePageResponse,
  PaperChartAnnotationPageResponse,
  PaperDashboardResponse,
  PaperPeriodGranularity,
  PaperPeriodMetricsSeriesResponse,
  PaperPortfolioTimelinePageResponse,
  PaperTradeJournalPageResponse,
  Setting,
  SimulationCreateRequest,
  SimulationDetail,
  SimulationListItem,
} from "../../src/types/api";
import type { SystemStatus } from "../../src/types/system";
import {
  ADMIN_EMAIL,
  ADMIN_ID,
  API_ORIGIN,
  E2E_PASSWORD,
  REQUEST_ID,
  SUPABASE_ORIGIN,
  USER_EMAIL,
  USER_ID,
  WEB_ORIGIN,
} from "./constants";
import {
  createInitialMovement,
  createSession,
  createSettings,
  createSimulation,
  createUser,
  type MockRole,
} from "./factories";

type BackendMode = "online" | "service-unavailable" | "network-error";

interface RecordedRequest {
  method: string;
  pathname: string;
  search: string;
  origin: string;
  authorization?: string;
  body?: unknown;
}

interface ErrorResponse {
  error: {
    code: string;
    message: string;
    details?: JsonValue;
  };
}

interface RequestHold {
  promise: Promise<void>;
  release(): void;
}

const JSON_HEADERS = {
  "Content-Type": "application/json",
  "X-Request-ID": REQUEST_ID,
};

export const PAPER_SESSION_ID = "c".repeat(64);
export const ADMIN_PAPER_SESSION_ID = "a".repeat(64);
export const ADMIN_PAPER_TRADE_ID = "e".repeat(64);

function decimalToBigInt(value: string): bigint {
  const match = /^(-?)(\d+)(?:\.(\d{1,8}))?$/.exec(value);
  if (!match) throw new Error(`Decimal E2E inválido: ${value}`);
  const [, sign, integer, fraction = ""] = match;
  const scaled = BigInt(`${integer}${fraction.padEnd(8, "0")}`);
  return sign === "-" ? -scaled : scaled;
}

function bigIntToDecimal(value: bigint): string {
  const sign = value < 0n ? "-" : "";
  const absolute = value < 0n ? -value : value;
  const raw = absolute.toString().padStart(9, "0");
  return `${sign}${raw.slice(0, -8)}.${raw.slice(-8)}`;
}

function normalizeDecimal(value: string): string {
  return bigIntToDecimal(decimalToBigInt(value));
}

function asListItem(simulation: SimulationDetail): SimulationListItem {
  return {
    id: simulation.id,
    name: simulation.name,
    status: simulation.status,
    currency: simulation.currency,
    initial_capital: simulation.initial_capital,
    current_balance: simulation.current_balance,
    total_profit_loss: simulation.total_profit_loss,
    started_at: simulation.started_at,
    ended_at: simulation.ended_at,
    created_at: simulation.created_at,
    updated_at: simulation.updated_at,
  };
}

function adminMarketCandlePage(timeframe: string): MarketCandlePageResponse {
  const datasetVersion = "a".repeat(64);
  return {
    schema_version: 1,
    exchange: "binance",
    market_type: "spot",
    symbol: "BTC/USDT",
    base_asset: "BTC",
    quote_asset: "USDT",
    timeframe,
    requested_before: null,
    available_start: "2026-08-08T00:00:00Z",
    available_end: "2026-08-08T00:03:00Z",
    range_start: "2026-08-08T00:00:00Z",
    range_end: "2026-08-08T00:03:00Z",
    limit: 1_000,
    count: 3,
    dataset_candle_count: 3,
    dataset_version: datasetVersion,
    dataset_version_algorithm: "sha256",
    content_checksum: "b".repeat(64),
    has_more_before: false,
    next_before: null,
    items: [
      [
        "2026-08-08T00:00:00Z",
        "2026-08-08T00:00:59.999Z",
        "100",
        "110",
        "90",
        "105",
      ],
      [
        "2026-08-08T00:01:00Z",
        "2026-08-08T00:01:59.999Z",
        "105",
        "115",
        "100",
        "112",
      ],
      [
        "2026-08-08T00:02:00Z",
        "2026-08-08T00:02:59.999Z",
        "112",
        "120",
        "108",
        "118",
      ],
    ].map(([openTime, closeTime, open, high, low, close]) => ({
      open_time: openTime,
      close_time: closeTime,
      open,
      high,
      low,
      close,
      volume: "2.500000000000000000",
      quote_volume: null,
      trade_count: 10,
      is_closed: true,
      source: "e2e_fixture",
    })),
  };
}

function adminChartAnnotations(
  rangeStart: string,
  rangeEnd: string,
): PaperChartAnnotationPageResponse {
  return {
    schema_version: 1,
    session_id: ADMIN_PAPER_SESSION_ID,
    config_checksum: "2".repeat(64),
    state_available: true,
    state_id: "3".repeat(64),
    state_checksum: "4".repeat(64),
    dataset_version: "a".repeat(64),
    source_checksum: "5".repeat(64),
    symbol: "BTC/USDT",
    base_asset: "BTC",
    quote_asset: "USDT",
    timeframe: "1m",
    strategy_name: "no-op",
    strategy_version: "2",
    strategy_parameters: {},
    ema_fast_period: 3,
    ema_slow_period: 5,
    range_start: rangeStart,
    range_end: rangeEnd,
    limit: 5_000,
    count: 2,
    orders_count: 1,
    fills_count: 1,
    orders: [
      {
        order_id: "O000000000002",
        created_sequence: 2,
        created_at: "2026-08-08T00:02:00Z",
        opened_at: "2026-08-08T00:02:00Z",
        terminal_at: null,
        status: "OPEN",
        side: "SELL",
        order_type: "STOP_MARKET",
        time_in_force: "GTC",
        quantity: "0.500000000000000000",
        limit_price: null,
        stop_price: "95.000000000000000000",
        client_tag: "protective-stop",
        rejection_code: null,
        is_engine_protective_stop: true,
      },
    ],
    fills: [
      {
        fill_id: "F000000000001",
        order_id: "O000000000001",
        trade_id: ADMIN_PAPER_TRADE_ID,
        trade_sequence: 2,
        role: "ENTRY",
        event_time: "2026-08-08T00:01:00Z",
        candle_index: 1,
        side: "BUY",
        order_type: "MARKET",
        time_in_force: "GTC",
        client_tag: "entry-e2e",
        fill_reason: "MARKET_OPEN",
        liquidity: "TAKER",
        quantity: "0.500000000000000000",
        base_price: "112.000000000000000000",
        execution_price: "112.000000000000000000",
        notional: "56.000000000000000000",
        fee: "0.056000000000000000",
        slippage_cost: "0.010000000000000000",
        is_engine_protective_stop: false,
      },
    ],
    last_candle_open_time: "2026-08-08T00:02:00Z",
    replayed_at: "2026-08-08T00:03:00Z",
    content_checksum: "6".repeat(64),
  };
}

function adminJournalPage(
  sessionId: string | null,
): PaperTradeJournalPageResponse {
  return {
    filters: {
      session_id: sessionId,
      base_asset: null,
      quote_asset: null,
      timeframe: null,
      strategy_name: null,
      strategy_version: null,
      status: null,
      opened_from: null,
      opened_before: null,
      closed_from: null,
      closed_before: null,
    },
    items: [
      {
        session_id: ADMIN_PAPER_SESSION_ID,
        config_checksum: "2".repeat(64),
        state_id: "3".repeat(64),
        state_checksum: "4".repeat(64),
        symbol: "BTC/USDT",
        base_asset: "BTC",
        quote_asset: "USDT",
        timeframe: "1m",
        strategy_name: "no-op",
        strategy_version: "2",
        strategy_parameters: {},
        last_candle_open_time: "2026-08-08T00:02:00Z",
        replayed_at: "2026-08-08T00:03:00Z",
        trade: {
          trade_id: ADMIN_PAPER_TRADE_ID,
          session_id: ADMIN_PAPER_SESSION_ID,
          sequence: 2,
          status: "OPEN",
          opened_at: "2026-08-08T00:01:00Z",
          last_entry_at: "2026-08-08T00:01:00Z",
          first_exit_at: null,
          closed_at: null,
          entry_executions: [
            {
              fill_id: "F000000000001",
              order_id: "O000000000001",
              order_sequence: 1,
              side: "BUY",
              order_type: "MARKET",
              time_in_force: "GTC",
              client_tag: "entry-e2e",
              fill_reason: "MARKET_OPEN",
              liquidity: "TAKER",
              quantity: "0.500000000000000000",
              base_price: "112.000000000000000000",
              execution_price: "112.000000000000000000",
              notional: "56.000000000000000000",
              fee: "0.056000000000000000",
              slippage_cost: "0.010000000000000000",
              event_time: "2026-08-08T00:01:00Z",
              candle_index: 1,
            },
          ],
          exit_executions: [],
          opened_quantity: "0.500000000000000000",
          closed_quantity: "0.000000000000000000",
          remaining_quantity: "0.500000000000000000",
          entry_notional: "56.000000000000000000",
          exit_notional: "0.000000000000000000",
          entry_fees: "0.056000000000000000",
          exit_fees: "0.000000000000000000",
          total_fees: "0.056000000000000000",
          entry_slippage_cost: "0.010000000000000000",
          exit_slippage_cost: "0.000000000000000000",
          total_slippage_cost: "0.010000000000000000",
          entry_cost_basis: "56.066000000000000000",
          released_cost_basis: "0.000000000000000000",
          remaining_cost_basis: "56.066000000000000000",
          average_entry_price: "112.000000000000000000",
          average_exit_price: null,
          realized_pnl: "0.000000000000000000",
          unrealized_pnl: "3.000000000000000000",
          net_pnl: "3.000000000000000000",
          mark_price: "118.000000000000000000",
        },
      },
    ],
    page: 1,
    page_size: 20,
    total: 1,
    total_pages: 1,
    totals: {
      trades_count: 1,
      closed_trades_count: 0,
      open_trades_count: 1,
      total_realized_pnl: "0.000000000000000000",
      total_unrealized_pnl: "3.000000000000000000",
      total_net_pnl: "3.000000000000000000",
      total_fees: "0.056000000000000000",
      total_slippage_cost: "0.010000000000000000",
    },
  };
}

function adminPortfolioTimeline(): PaperPortfolioTimelinePageResponse {
  const observation = (
    candleIndex: number,
    equity: string,
    realizedPnl: string,
    unrealizedPnl: string,
    drawdown: string,
    drawdownPct: string,
    riskHalt: boolean,
  ) => ({
    candle_index: candleIndex,
    candle_open_time: `2026-08-08T00:0${candleIndex}:00Z`,
    candle_close_time: `2026-08-08T00:0${candleIndex}:59.999Z`,
    mark_price: "118.000000000000000000",
    quote_cash: "944.000000000000000000",
    base_quantity: "0.500000000000000000",
    average_entry_price: "112.000000000000000000",
    cost_basis: "56.066000000000000000",
    realized_pnl: realizedPnl,
    unrealized_pnl: unrealizedPnl,
    total_fees: "0.056000000000000000",
    total_slippage_cost: "0.010000000000000000",
    equity,
    peak_equity: "1003.000000000000000000",
    drawdown,
    drawdown_pct: drawdownPct,
    risk_halt: riskHalt,
  });
  const items = [
    observation(0, "1000.000000000000000000", "0", "0", "0", "0", false),
    observation(1, "1003.000000000000000000", "0", "3", "0", "0", false),
    observation(
      2,
      "997.000000000000000000",
      "-2",
      "-1",
      "6",
      "0.598205383848454636",
      true,
    ),
  ];
  return {
    schema_version: 1,
    session_id: ADMIN_PAPER_SESSION_ID,
    config_checksum: "2".repeat(64),
    state_id: "3".repeat(64),
    state_checksum: "4".repeat(64),
    state_replayed_at: "2026-08-08T00:03:00Z",
    symbol: "BTC/USDT",
    base_asset: "BTC",
    quote_asset: "USDT",
    timeframe: "1m",
    dataset_version: "a".repeat(64),
    source_checksum: "5".repeat(64),
    timeline_id: "7".repeat(64),
    timeline_content_checksum: "8".repeat(64),
    initial_capital: "1000.000000000000000000",
    requested_before: null,
    available_start: "2026-08-08T00:00:00Z",
    available_end: "2026-08-08T00:03:00Z",
    range_start: "2026-08-08T00:00:00Z",
    range_end: "2026-08-08T00:03:00Z",
    limit: 5_000,
    count: items.length,
    total_observations: items.length,
    has_more_before: false,
    next_before: null,
    content_checksum: "9".repeat(64),
    items,
  };
}

function adminPeriodMetrics(
  granularity: PaperPeriodGranularity,
  quoteAsset: string,
  periodFrom: string,
  periodBefore: string,
): PaperPeriodMetricsSeriesResponse {
  const bucket = {
    period_start: "2026-08-08T00:00:00Z",
    period_end: "2026-08-09T00:00:00Z",
    quote_asset: quoteAsset,
    realizations_count: 2,
    winning_realizations_count: 1,
    losing_realizations_count: 1,
    breakeven_realizations_count: 0,
    sessions_count: 1,
    symbols_count: 1,
    exit_notional: "220.000000000000000000",
    released_cost_basis: "203.800000000000000000",
    realized_fees: "0.700000000000000000",
    realized_slippage_cost: "0.200000000000000000",
    gross_profit: "20.000000000000000000",
    gross_loss: "-4.500000000000000000",
    realized_pnl: "15.500000000000000000",
    win_rate_pct: "50.000000000000000000",
    profit_factor: "4.444444444444444444",
  };
  return {
    schema_version: 1,
    granularity,
    filters: {
      quote_asset: quoteAsset,
      period_from: periodFrom,
      period_before: periodBefore,
      session_id: null,
      base_asset: null,
      timeframe: null,
      strategy_name: null,
      strategy_version: null,
    },
    source_states: [
      {
        session_id: ADMIN_PAPER_SESSION_ID,
        config_checksum: "2".repeat(64),
        state_id: "3".repeat(64),
        state_checksum: "4".repeat(64),
        base_asset: "BTC",
        quote_asset: quoteAsset,
        last_candle_open_time: "2026-08-08T00:02:00Z",
        replayed_at: "2026-08-08T00:03:00Z",
      },
    ],
    items: [bucket],
    totals: {
      periods_count: 1,
      active_periods_count: 1,
      quote_asset: quoteAsset,
      realizations_count: 2,
      winning_realizations_count: 1,
      losing_realizations_count: 1,
      breakeven_realizations_count: 0,
      sessions_count: 1,
      symbols_count: 1,
      exit_notional: bucket.exit_notional,
      released_cost_basis: bucket.released_cost_basis,
      realized_fees: bucket.realized_fees,
      realized_slippage_cost: bucket.realized_slippage_cost,
      gross_profit: bucket.gross_profit,
      gross_loss: bucket.gross_loss,
      realized_pnl: bucket.realized_pnl,
      win_rate_pct: bucket.win_rate_pct,
      profit_factor: bucket.profit_factor,
    },
    query_checksum: "f".repeat(64),
    content_checksum: "0".repeat(64),
  };
}

export class MockServices {
  readonly requests: RecordedRequest[] = [];
  readonly unexpectedRequests: string[] = [];

  private backendMode: BackendMode = "online";
  private databaseAvailable = true;
  private pendingAdminUnauthorizedResponses = 0;
  private tokenSequence = 0;
  private movementSequence = 0;
  private readonly tokenRoles = new Map<string, MockRole>();
  private readonly refreshRoles = new Map<string, MockRole>();
  private readonly requestHolds = new Map<string, RequestHold>();
  private simulations: SimulationDetail[] = [];
  private readonly movements = new Map<string, CapitalMovement[]>();
  private settings: Setting[] = createSettings();

  lastIssuedAccessToken: string | null = null;
  recoveredEmail: string | null = null;
  recoveryRedirectTo: string | null = null;
  updatedPassword: string | null = null;

  constructor() {
    this.tokenRoles.set("e2e-recovery-access", "admin");
    this.refreshRoles.set("e2e-recovery-refresh", "admin");
  }

  async install(context: BrowserContext): Promise<void> {
    await context.route("**/*", async (route) => {
      const request = route.request();
      const url = new URL(request.url());

      if (url.origin === WEB_ORIGIN) {
        await route.continue();
        return;
      }
      if (url.origin === SUPABASE_ORIGIN) {
        await this.handleSupabase(route, request, url);
        return;
      }
      if (url.origin === API_ORIGIN) {
        await this.handleApi(route, request, url);
        return;
      }

      this.unexpectedRequests.push(
        `${request.method()} ${url.origin}${url.pathname}`,
      );
      await route.abort("blockedbyclient");
    });
  }

  seedActiveSimulation(
    overrides: Partial<SimulationDetail> = {},
  ): SimulationDetail {
    const simulation = createSimulation(overrides);
    this.simulations = [simulation];
    this.movements.set(simulation.id, [
      createInitialMovement(simulation.id, simulation.initial_capital),
    ]);
    return simulation;
  }

  setBackendMode(mode: BackendMode): void {
    this.backendMode = mode;
  }

  setDatabaseAvailable(available: boolean): void {
    this.databaseAvailable = available;
  }

  rejectNextAdminRequestsWith401(count = 1): void {
    this.pendingAdminUnauthorizedResponses = count;
  }

  requestsFor(method: string, pathname: string): RecordedRequest[] {
    return this.requests.filter(
      (request) =>
        request.method === method.toUpperCase() &&
        request.pathname === pathname,
    );
  }

  holdNextApiRequest(method: string, pathname: string): () => void {
    const key = `${method.toUpperCase()} ${pathname}`;
    let releasePromise: (() => void) | undefined;
    const promise = new Promise<void>((resolve) => {
      releasePromise = resolve;
    });
    const hold: RequestHold = {
      promise,
      release() {
        releasePromise?.();
      },
    };
    this.requestHolds.set(key, hold);
    return hold.release;
  }

  recoveryCallbackUrl(expired = false): string {
    if (expired) {
      return "/admin/reset-password#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired";
    }
    return "/admin/reset-password#access_token=e2e-recovery-access&refresh_token=e2e-recovery-refresh&expires_in=3600&token_type=bearer&type=recovery";
  }

  private record(request: Request, url: URL): void {
    const rawBody = request.postData();
    let body: unknown;
    if (rawBody) {
      try {
        body = JSON.parse(rawBody) as unknown;
      } catch {
        body = rawBody;
      }
    }
    this.requests.push({
      method: request.method(),
      pathname: url.pathname,
      search: url.search,
      origin: url.origin,
      authorization: request.headers().authorization,
      body,
    });
  }

  private corsHeaders(): Record<string, string> {
    return {
      "Access-Control-Allow-Origin": WEB_ORIGIN,
      "Access-Control-Expose-Headers": "x-request-id",
      Vary: "Origin",
    };
  }

  private async preflight(
    route: Route,
    request: Request,
    allowedMethods: readonly string[],
    allowedHeaders: readonly string[],
  ): Promise<void> {
    const headers = request.headers();
    const requestedMethod =
      headers["access-control-request-method"]?.toUpperCase();
    const requestedHeaders = (headers["access-control-request-headers"] ?? "")
      .split(",")
      .map((header) => header.trim().toLowerCase())
      .filter(Boolean);
    const normalizedAllowedHeaders = new Set(
      allowedHeaders.map((header) => header.toLowerCase()),
    );
    const permitted =
      requestedMethod !== undefined &&
      allowedMethods.includes(requestedMethod) &&
      requestedHeaders.every((header) => normalizedAllowedHeaders.has(header));

    await route.fulfill({
      status: permitted ? 204 : 400,
      headers: {
        ...this.corsHeaders(),
        "Access-Control-Allow-Methods": allowedMethods.join(", "),
        "Access-Control-Allow-Headers": allowedHeaders.join(", "),
      },
    });
  }

  private async json(
    route: Route,
    status: number,
    value: object | unknown[] | null,
    headers: Record<string, string> = {},
  ): Promise<void> {
    await route.fulfill({
      status,
      json: value,
      headers: {
        ...this.corsHeaders(),
        ...JSON_HEADERS,
        ...headers,
      },
    });
  }

  private async apiError(
    route: Route,
    status: number,
    code: string,
    message: string,
    details?: JsonValue,
  ): Promise<void> {
    const body: ErrorResponse = {
      error: {
        code,
        message,
        ...(details === undefined ? {} : { details }),
      },
    };
    await this.json(route, status, body);
  }

  private issueSession(role: MockRole) {
    this.tokenSequence += 1;
    const accessToken = `e2e-${role}-access-${this.tokenSequence}`;
    const refreshToken = `e2e-${role}-refresh-${this.tokenSequence}`;
    this.tokenRoles.set(accessToken, role);
    this.refreshRoles.set(refreshToken, role);
    this.lastIssuedAccessToken = accessToken;
    return createSession(role, accessToken, refreshToken);
  }

  private requestBody<T>(request: Request): T {
    const body = request.postData();
    if (!body)
      throw new Error(`Corpo ausente em ${request.method()} ${request.url()}`);
    return JSON.parse(body) as T;
  }

  private roleForRequest(request: Request): MockRole | null {
    const authorization = request.headers().authorization;
    if (!authorization?.startsWith("Bearer ")) return null;
    return this.tokenRoles.get(authorization.slice("Bearer ".length)) ?? null;
  }

  private async handleSupabase(
    route: Route,
    request: Request,
    url: URL,
  ): Promise<void> {
    this.record(request, url);
    if (request.method() === "OPTIONS") {
      await this.preflight(
        route,
        request,
        ["GET", "POST", "PUT", "OPTIONS"],
        ["accept", "apikey", "authorization", "content-type", "x-client-info"],
      );
      return;
    }

    if (
      request.method() === "POST" &&
      url.pathname === "/auth/v1/token" &&
      url.searchParams.get("grant_type") === "password"
    ) {
      const credentials = this.requestBody<{
        email?: string;
        password?: string;
      }>(request);
      const role =
        credentials.email === ADMIN_EMAIL
          ? "admin"
          : credentials.email === USER_EMAIL
            ? "user"
            : null;
      if (!role || credentials.password !== E2E_PASSWORD) {
        await this.json(
          route,
          400,
          {
            code: "invalid_credentials",
            msg: "Invalid login credentials",
          },
          { "X-Supabase-Api-Version": "2024-01-01" },
        );
        return;
      }
      await this.json(route, 200, this.issueSession(role));
      return;
    }

    if (
      request.method() === "POST" &&
      url.pathname === "/auth/v1/token" &&
      url.searchParams.get("grant_type") === "refresh_token"
    ) {
      const body = this.requestBody<{ refresh_token?: string }>(request);
      const role = body.refresh_token
        ? this.refreshRoles.get(body.refresh_token)
        : undefined;
      if (!role) {
        await this.json(route, 400, {
          code: "refresh_token_not_found",
          msg: "Invalid refresh token",
        });
        return;
      }
      await this.json(route, 200, this.issueSession(role));
      return;
    }

    if (request.method() === "POST" && url.pathname === "/auth/v1/logout") {
      await route.fulfill({
        status: 204,
        headers: this.corsHeaders(),
      });
      return;
    }

    if (request.method() === "POST" && url.pathname === "/auth/v1/recover") {
      const body = this.requestBody<{ email?: string }>(request);
      this.recoveredEmail = body.email ?? null;
      this.recoveryRedirectTo = url.searchParams.get("redirect_to");
      await this.json(route, 200, {});
      return;
    }

    if (request.method() === "GET" && url.pathname === "/auth/v1/user") {
      const role = this.roleForRequest(request);
      if (!role) {
        await this.json(route, 401, {
          code: "bad_jwt",
          msg: "Invalid JWT",
        });
        return;
      }
      await this.json(route, 200, createUser(role));
      return;
    }

    if (request.method() === "PUT" && url.pathname === "/auth/v1/user") {
      const role = this.roleForRequest(request);
      if (!role) {
        await this.json(route, 401, {
          code: "bad_jwt",
          msg: "Invalid JWT",
        });
        return;
      }
      const body = this.requestBody<{ password?: string }>(request);
      this.updatedPassword = body.password ?? null;
      await this.json(route, 200, createUser(role));
      return;
    }

    this.unexpectedRequests.push(
      `${request.method()} ${url.origin}${url.pathname}${url.search}`,
    );
    await route.fulfill({
      status: 501,
      json: { message: "Endpoint Supabase não mockado." },
      headers: this.corsHeaders(),
    });
  }

  private async requireAdmin(route: Route, request: Request): Promise<boolean> {
    const role = this.roleForRequest(request);
    if (!role) {
      await this.apiError(
        route,
        401,
        "authentication_required",
        "Autenticação válida é obrigatória.",
      );
      return false;
    }
    if (role !== "admin") {
      await this.apiError(
        route,
        403,
        "administrator_required",
        "Acesso administrativo negado.",
      );
      return false;
    }
    return true;
  }

  private async handleApi(
    route: Route,
    request: Request,
    url: URL,
  ): Promise<void> {
    this.record(request, url);
    if (request.method() === "OPTIONS") {
      await this.preflight(
        route,
        request,
        ["GET", "POST", "PATCH", "OPTIONS"],
        ["accept", "authorization", "content-type", "x-request-id"],
      );
      return;
    }
    const holdKey = `${request.method()} ${url.pathname}`;
    const hold = this.requestHolds.get(holdKey);
    if (hold) {
      this.requestHolds.delete(holdKey);
      await hold.promise;
    }
    if (this.backendMode === "network-error") {
      await route.abort("connectionfailed");
      return;
    }
    if (this.backendMode === "service-unavailable") {
      await this.apiError(
        route,
        503,
        "service_unavailable",
        "O serviço está temporariamente indisponível.",
      );
      return;
    }

    if (
      request.method() === "GET" &&
      url.pathname === "/api/v1/system/status"
    ) {
      const systemStatus = {
        status: "operational",
        version: "0.1.0",
        environment: "test",
        timestamp: "2026-07-29T15:00:00.000Z",
      } satisfies SystemStatus;
      await this.json(route, 200, systemStatus);
      return;
    }

    if (request.method() === "GET" && url.pathname === "/health") {
      await this.json(route, 200, { status: "healthy" });
      return;
    }

    if (
      request.method() === "GET" &&
      (url.pathname === "/health/database" ||
        url.pathname === "/health/readiness")
    ) {
      if (!this.databaseAvailable) {
        await this.apiError(
          route,
          503,
          "database_unavailable",
          "O banco de dados está temporariamente indisponível.",
        );
        return;
      }
      await this.json(route, 200, {
        status: url.pathname.endsWith("readiness") ? "ready" : "healthy",
      });
      return;
    }

    if (
      request.method() === "GET" &&
      url.pathname === "/api/v1/public/simulation"
    ) {
      if (!this.databaseAvailable) {
        await this.apiError(
          route,
          503,
          "database_unavailable",
          "O banco de dados está temporariamente indisponível.",
        );
        return;
      }
      const active = this.simulations.find(
        (simulation) => simulation.status === "ACTIVE",
      );
      await this.json(
        route,
        200,
        active
          ? {
              name: active.name,
              currency: active.currency,
              initial_capital: active.initial_capital,
              current_balance: active.current_balance,
              total_profit_loss: active.total_profit_loss,
              started_at: active.started_at,
              status: active.status,
            }
          : null,
      );
      return;
    }

    if (request.method() === "GET" && url.pathname === "/api/v1/app/me") {
      if (!this.databaseAvailable) {
        await this.apiError(
          route,
          503,
          "database_unavailable",
          "O banco de dados está temporariamente indisponível.",
        );
        return;
      }
      if (this.pendingAdminUnauthorizedResponses > 0) {
        this.pendingAdminUnauthorizedResponses -= 1;
        await this.apiError(route, 401, "token_expired", "A sessão expirou.");
        return;
      }
      const role = this.roleForRequest(request);
      if (!role) {
        await this.apiError(
          route,
          401,
          "authentication_required",
          "Autenticação válida é obrigatória.",
        );
        return;
      }
      await this.json(route, 200, {
        user_id: role === "admin" ? ADMIN_ID : USER_ID,
        is_admin: role === "admin",
      });
      return;
    }

    if (
      request.method() === "GET" &&
      url.pathname === "/api/v1/app/paper-trading/sessions"
    ) {
      const role = this.roleForRequest(request);
      if (!role) {
        await this.apiError(
          route,
          401,
          "authentication_required",
          "Autenticação válida é obrigatória.",
        );
        return;
      }
      const page = Number(url.searchParams.get("page") ?? "1");
      const pageSize = Number(url.searchParams.get("page_size") ?? "20");
      const isProjectOwnerReader = role === "admin";
      const response = {
        items:
          isProjectOwnerReader && page === 1
            ? [
                {
                  session_id: PAPER_SESSION_ID,
                  base_asset: "BTC",
                  quote_asset: "USDT",
                  timeframe: "15m",
                  strategy_name: "paper-buy-test",
                  strategy_version: "1",
                },
              ]
            : [],
        page,
        page_size: pageSize,
        total: isProjectOwnerReader ? 1 : 0,
        total_pages: isProjectOwnerReader ? 1 : 0,
      } satisfies AppPaperSessionCatalogResponse;
      await this.json(route, 200, response);
      return;
    }

    const appPaperAnnotationsMatch =
      /^\/api\/v1\/app\/paper-trading\/sessions\/([0-9a-f]{64})\/chart-annotations$/.exec(
        url.pathname,
      );
    if (request.method() === "GET" && appPaperAnnotationsMatch) {
      const role = this.roleForRequest(request);
      if (!role) {
        await this.apiError(
          route,
          401,
          "authentication_required",
          "Autenticação válida é obrigatória.",
        );
        return;
      }
      if (role !== "admin") {
        await this.apiError(route, 403, "forbidden", "Acesso negado.");
        return;
      }
      const [, sessionId] = appPaperAnnotationsMatch;
      const response = {
        session_id: sessionId,
        base_asset: "BTC",
        quote_asset: "USDT",
        timeframe: "15m",
        state_available: true,
        dataset_version: "a".repeat(64),
        range_start: url.searchParams.get("start") ?? "2026-08-08T00:00:00Z",
        range_end: url.searchParams.get("before") ?? "2026-08-08T00:45:00Z",
        count: 2,
        orders_count: 1,
        fills_count: 1,
        orders: [
          {
            order_id: "O000000000001",
            created_at: "2026-08-08T00:15:00Z",
            status: "OPEN",
            side: "SELL",
            quantity: "1.000000000000000000",
            stop_price: "95.000000000000000000",
            is_engine_protective_stop: true,
          },
        ],
        fills: [
          {
            fill_id: "F000000000001",
            trade_id: "T000000000001",
            trade_sequence: 1,
            role: "ENTRY",
            event_time: "2026-08-08T00:15:00Z",
            side: "BUY",
            quantity: "1.000000000000000000",
            execution_price: "105.000000000000000000",
            fee: "0.100000000000000000",
            slippage_cost: "0.010000000000000000",
            is_engine_protective_stop: false,
          },
        ],
        last_candle_open_time: "2026-08-08T00:30:00Z",
        content_checksum: "d".repeat(64),
      } satisfies AppPaperChartAnnotationPage;
      await this.json(route, 200, response);
      return;
    }

    const appPaperTradesMatch =
      /^\/api\/v1\/app\/paper-trading\/sessions\/([0-9a-f]{64})\/trades$/.exec(
        url.pathname,
      );
    if (request.method() === "GET" && appPaperTradesMatch) {
      const role = this.roleForRequest(request);
      if (!role) {
        await this.apiError(
          route,
          401,
          "authentication_required",
          "Autenticação válida é obrigatória.",
        );
        return;
      }
      if (role !== "admin") {
        await this.apiError(route, 403, "forbidden", "Acesso negado.");
        return;
      }
      const page = Number(url.searchParams.get("page") ?? "1");
      const pageSize = Number(url.searchParams.get("page_size") ?? "20");
      const response = {
        items: [
          {
            trade_id: "T000000000001",
            sequence: 1,
            status: "OPEN",
            opened_at: "2026-08-08T00:15:00Z",
            closed_at: null,
            opened_quantity: "1.000000000000000000",
            closed_quantity: "0.000000000000000000",
            remaining_quantity: "1.000000000000000000",
            average_entry_price: "105.000000000000000000",
            average_exit_price: null,
            realized_pnl: "0.000000000000000000",
            unrealized_pnl: "13.000000000000000000",
            net_pnl: "13.000000000000000000",
            total_fees: "0.100000000000000000",
            total_slippage_cost: "0.010000000000000000",
            mark_price: "118.000000000000000000",
          },
        ],
        page,
        page_size: pageSize,
        total: 1,
        total_pages: 1,
        totals: {
          trades_count: 1,
          closed_trades_count: 0,
          open_trades_count: 1,
          total_realized_pnl: "0.000000000000000000",
          total_unrealized_pnl: "13.000000000000000000",
          total_net_pnl: "13.000000000000000000",
          total_fees: "0.100000000000000000",
          total_slippage_cost: "0.010000000000000000",
        },
      } satisfies AppPaperTradePage;
      await this.json(route, 200, response);
      return;
    }

    const appPaperTimelineMatch =
      /^\/api\/v1\/app\/paper-trading\/sessions\/([0-9a-f]{64})\/portfolio-timeline$/.exec(
        url.pathname,
      );
    if (request.method() === "GET" && appPaperTimelineMatch) {
      const role = this.roleForRequest(request);
      if (!role) {
        await this.apiError(
          route,
          401,
          "authentication_required",
          "Autenticação válida é obrigatória.",
        );
        return;
      }
      if (role !== "admin") {
        await this.apiError(route, 403, "forbidden", "Acesso negado.");
        return;
      }
      const observation = (index: number, equity: string) => ({
        candle_index: index,
        candle_open_time: `2026-08-${index + 13}T00:00:00Z`,
        candle_close_time: `2026-08-${index + 13}T00:15:00Z`,
        mark_price: "105.000000000000000000",
        quote_cash: "900.000000000000000000",
        base_quantity: "1.000000000000000000",
        average_entry_price: "100.000000000000000000",
        cost_basis: "100.000000000000000000",
        realized_pnl: "4.900000000000000000",
        unrealized_pnl: "5.000000000000000000",
        total_fees: "0.100000000000000000",
        total_slippage_cost: "0.010000000000000000",
        equity,
        peak_equity: "1010.000000000000000000",
        drawdown: "0.100000000000000000",
        drawdown_pct: "0.009900990099009901",
        risk_halt: false,
      });
      const items = [
        observation(0, "1009.900000000000000000"),
        observation(1, "1009.800000000000000000"),
      ];
      const response = {
        session_id: appPaperTimelineMatch[1],
        state_checksum: "b".repeat(64),
        base_asset: "BTC",
        quote_asset: "USDT",
        timeframe: "15m",
        dataset_version: "a".repeat(64),
        timeline_id: "e".repeat(64),
        timeline_content_checksum: "f".repeat(64),
        initial_capital: "1000.000000000000000000",
        available_start: "2026-08-01T00:00:00Z",
        available_end: "2026-08-15T00:00:00Z",
        range_start: "2026-08-13T00:00:00Z",
        range_end: "2026-08-15T00:00:00Z",
        limit: Number(url.searchParams.get("limit") ?? "1000"),
        count: items.length,
        total_observations: items.length,
        has_more_before: false,
        next_before: null,
        content_checksum: "1".repeat(64),
        items,
      } satisfies AppPaperPortfolioTimelinePage;
      await this.json(route, 200, response);
      return;
    }

    const appPaperPeriodMatch =
      /^\/api\/v1\/app\/paper-trading\/sessions\/([0-9a-f]{64})\/period-metrics$/.exec(
        url.pathname,
      );
    if (request.method() === "GET" && appPaperPeriodMatch) {
      const role = this.roleForRequest(request);
      if (!role) {
        await this.apiError(
          route,
          401,
          "authentication_required",
          "Autenticação válida é obrigatória.",
        );
        return;
      }
      if (role !== "admin") {
        await this.apiError(route, 403, "forbidden", "Acesso negado.");
        return;
      }
      const bucket = {
        period_start: "2026-08-01T00:00:00Z",
        period_end: "2026-08-02T00:00:00Z",
        quote_asset: "USDT",
        realizations_count: 1,
        winning_realizations_count: 1,
        losing_realizations_count: 0,
        breakeven_realizations_count: 0,
        exit_notional: "105.000000000000000000",
        released_cost_basis: "100.000000000000000000",
        realized_fees: "0.100000000000000000",
        realized_slippage_cost: "0.010000000000000000",
        gross_profit: "4.890000000000000000",
        gross_loss: "0.000000000000000000",
        realized_pnl: "4.890000000000000000",
        win_rate_pct: "100.000000000000000000",
        profit_factor: null,
      };
      const response = {
        session_id: appPaperPeriodMatch[1],
        quote_asset: "USDT",
        granularity: (url.searchParams.get("granularity") ??
          "DAILY") as PaperPeriodGranularity,
        period_from:
          url.searchParams.get("period_from") ?? "2026-08-01T00:00:00Z",
        period_before:
          url.searchParams.get("period_before") ?? "2026-08-04T00:00:00Z",
        items: [bucket],
        totals: {
          periods_count: 3,
          active_periods_count: 1,
          quote_asset: "USDT",
          realizations_count: 1,
          winning_realizations_count: 1,
          losing_realizations_count: 0,
          breakeven_realizations_count: 0,
          exit_notional: bucket.exit_notional,
          released_cost_basis: bucket.released_cost_basis,
          realized_fees: bucket.realized_fees,
          realized_slippage_cost: bucket.realized_slippage_cost,
          gross_profit: bucket.gross_profit,
          gross_loss: bucket.gross_loss,
          realized_pnl: bucket.realized_pnl,
          win_rate_pct: bucket.win_rate_pct,
          profit_factor: bucket.profit_factor,
        },
        query_checksum: "2".repeat(64),
        content_checksum: "3".repeat(64),
      } satisfies AppPaperPeriodMetricsSeries;
      await this.json(route, 200, response);
      return;
    }

    const appPaperDetailMatch =
      /^\/api\/v1\/app\/paper-trading\/sessions\/([0-9a-f]{64})$/.exec(
        url.pathname,
      );
    if (request.method() === "GET" && appPaperDetailMatch) {
      const role = this.roleForRequest(request);
      if (!role) {
        await this.apiError(
          route,
          401,
          "authentication_required",
          "Autenticação válida é obrigatória.",
        );
        return;
      }
      if (role !== "admin") {
        await this.apiError(route, 403, "forbidden", "Acesso negado.");
        return;
      }
      const response = {
        session_id: appPaperDetailMatch[1],
        base_asset: "BTC",
        quote_asset: "USDT",
        timeframe: "15m",
        strategy_name: "paper-buy-test",
        strategy_version: "1",
        state_available: true,
        last_candle_open_time: "2026-08-08T00:30:00Z",
      } satisfies AppPaperSessionDetail;
      await this.json(route, 200, response);
      return;
    }

    const appMarketCandlesMatch =
      /^\/api\/v1\/app\/market-data\/candles\/([^/]+)\/([^/]+)$/.exec(
        url.pathname,
      );
    if (request.method() === "GET" && appMarketCandlesMatch) {
      const role = this.roleForRequest(request);
      if (!role) {
        await this.apiError(
          route,
          401,
          "authentication_required",
          "Autenticação válida é obrigatória.",
        );
        return;
      }
      const [, rawBaseAsset, rawQuoteAsset] = appMarketCandlesMatch;
      const baseAsset = decodeURIComponent(rawBaseAsset).toUpperCase();
      const quoteAsset = decodeURIComponent(rawQuoteAsset).toUpperCase();
      const timeframe = url.searchParams.get("timeframe") ?? "15m";
      const datasetVersion = "a".repeat(64);
      const contentChecksum = "b".repeat(64);
      const response = {
        schema_version: 1,
        exchange: "binance",
        market_type: "spot",
        symbol: `${baseAsset}/${quoteAsset}`,
        base_asset: baseAsset,
        quote_asset: quoteAsset,
        timeframe,
        requested_before: null,
        available_start: "2026-08-08T00:00:00Z",
        available_end: "2026-08-08T00:45:00Z",
        range_start: "2026-08-08T00:00:00Z",
        range_end: "2026-08-08T00:45:00Z",
        limit: Number(url.searchParams.get("limit") ?? "1000"),
        count: 3,
        dataset_candle_count: 3,
        dataset_version: datasetVersion,
        dataset_version_algorithm: "sha256",
        content_checksum: contentChecksum,
        has_more_before: false,
        next_before: null,
        items: [
          [
            "2026-08-08T00:00:00Z",
            "2026-08-08T00:14:59.999Z",
            "100",
            "110",
            "90",
            "105",
          ],
          [
            "2026-08-08T00:15:00Z",
            "2026-08-08T00:29:59.999Z",
            "105",
            "115",
            "100",
            "112",
          ],
          [
            "2026-08-08T00:30:00Z",
            "2026-08-08T00:44:59.999Z",
            "112",
            "120",
            "108",
            "118",
          ],
        ].map(([openTime, closeTime, open, high, low, close]) => ({
          open_time: openTime,
          close_time: closeTime,
          open,
          high,
          low,
          close,
          volume: "2.500000000000000000",
          quote_volume: null,
          trade_count: 10,
          is_closed: true,
          source: "e2e_fixture",
        })),
      } satisfies MarketCandlePageResponse;
      await this.json(route, 200, response, {
        "Cache-Control": "no-store",
        "X-ADT-Candle-Dataset-Version": datasetVersion,
        "X-ADT-Candle-Content-Checksum": contentChecksum,
        "X-ADT-Candle-Rows": "3",
      });
      return;
    }

    if (url.pathname.startsWith("/api/v1/admin/")) {
      if (!this.databaseAvailable) {
        await this.apiError(
          route,
          503,
          "database_unavailable",
          "O banco de dados está temporariamente indisponível.",
        );
        return;
      }
      if (this.pendingAdminUnauthorizedResponses > 0) {
        this.pendingAdminUnauthorizedResponses -= 1;
        await this.apiError(route, 401, "token_expired", "A sessão expirou.");
        return;
      }
      if (!(await this.requireAdmin(route, request))) return;
    }

    const adminMarketCandlesMatch =
      /^\/api\/v1\/admin\/market-data\/candles\/([^/]+)\/([^/]+)$/.exec(
        url.pathname,
      );
    if (request.method() === "GET" && adminMarketCandlesMatch) {
      const [, rawBaseAsset, rawQuoteAsset] = adminMarketCandlesMatch;
      const baseAsset = decodeURIComponent(rawBaseAsset).toUpperCase();
      const quoteAsset = decodeURIComponent(rawQuoteAsset).toUpperCase();
      if (baseAsset !== "BTC" || quoteAsset !== "USDT") {
        await this.apiError(
          route,
          404,
          "dataset_not_found",
          "Dataset não encontrado.",
        );
        return;
      }
      const response = adminMarketCandlePage(
        url.searchParams.get("timeframe") ?? "1m",
      );
      await this.json(route, 200, response, {
        "Cache-Control": "no-store",
        "X-ADT-Candle-Dataset-Version": response.dataset_version,
        "X-ADT-Candle-Content-Checksum": response.content_checksum,
        "X-ADT-Candle-Rows": String(response.count),
      });
      return;
    }

    const adminAnnotationsMatch =
      /^\/api\/v1\/admin\/paper-trading\/sessions\/([0-9a-f]{64})\/chart-annotations$/.exec(
        url.pathname,
      );
    if (request.method() === "GET" && adminAnnotationsMatch) {
      if (adminAnnotationsMatch[1] !== ADMIN_PAPER_SESSION_ID) {
        await this.apiError(
          route,
          404,
          "paper_session_not_found",
          "Sessão não encontrada.",
        );
        return;
      }
      await this.json(
        route,
        200,
        adminChartAnnotations(
          url.searchParams.get("start") ?? "2026-08-08T00:00:00Z",
          url.searchParams.get("before") ?? "2026-08-08T00:03:00Z",
        ),
      );
      return;
    }

    if (
      request.method() === "GET" &&
      url.pathname === "/api/v1/admin/paper-trading/journal"
    ) {
      await this.json(
        route,
        200,
        adminJournalPage(url.searchParams.get("session_id")),
      );
      return;
    }

    const adminTimelineMatch =
      /^\/api\/v1\/admin\/paper-trading\/sessions\/([0-9a-f]{64})\/portfolio-timeline$/.exec(
        url.pathname,
      );
    if (request.method() === "GET" && adminTimelineMatch) {
      if (adminTimelineMatch[1] !== ADMIN_PAPER_SESSION_ID) {
        await this.apiError(
          route,
          404,
          "paper_session_not_found",
          "Sessão não encontrada.",
        );
        return;
      }
      await this.json(route, 200, adminPortfolioTimeline());
      return;
    }

    if (
      request.method() === "GET" &&
      url.pathname === "/api/v1/admin/paper-trading/period-metrics"
    ) {
      const rawGranularity = url.searchParams.get("granularity");
      const granularity: PaperPeriodGranularity =
        rawGranularity === "WEEKLY" || rawGranularity === "MONTHLY"
          ? rawGranularity
          : "DAILY";
      await this.json(
        route,
        200,
        adminPeriodMetrics(
          granularity,
          url.searchParams.get("quote_asset") ?? "USDT",
          url.searchParams.get("period_from") ?? "2026-08-01T00:00:00Z",
          url.searchParams.get("period_before") ?? "2026-08-10T00:00:00Z",
        ),
      );
      return;
    }

    if (request.method() === "GET" && url.pathname === "/api/v1/admin/me") {
      await this.json(route, 200, {
        user_id: ADMIN_ID,
        is_admin: true,
      });
      return;
    }

    if (
      request.method() === "GET" &&
      url.pathname === "/api/v1/admin/paper-trading/dashboard"
    ) {
      const page = Number(url.searchParams.get("page") ?? "1");
      const pageSize = Number(url.searchParams.get("page_size") ?? "20");
      const response: PaperDashboardResponse = {
        items: [
          {
            session_id: ADMIN_PAPER_SESSION_ID,
            symbol: "BTC/USDT",
            base_asset: "BTC",
            quote_asset: "USDT",
            timeframe: "1m",
            strategy_name: "no-op",
            strategy_version: "2",
            initial_capital: "1000",
            state_available: true,
            candles_processed: 120,
            last_candle_open_time: "2026-08-04T20:59:00Z",
            replayed_at: "2026-08-04T21:00:02Z",
            orders_count: 4,
            fills_count: 2,
            open_orders_count: 1,
            risk_halt: false,
            metrics: {
              initial_capital: "1000",
              equity: "1125.5",
              total_pnl: "125.5",
              return_pct: "12.55",
              realized_pnl: "90",
              unrealized_pnl: "35.5",
              drawdown: "8",
              drawdown_pct: "0.71",
              total_fees: "1.25",
              total_slippage_cost: "0.4",
            },
            portfolio: {
              quote_cash: "625.5",
              base_quantity: "0.005",
              average_entry_price: "100000",
              realized_pnl: "90",
              unrealized_pnl: "35.5",
              total_fees: "1.25",
              total_slippage_cost: "0.4",
              equity: "1125.5",
              peak_equity: "1133.5",
              drawdown: "8",
              drawdown_pct: "0.71",
              cost_basis: "500",
            },
            position: {
              is_open: true,
              base_quantity: "0.005",
              average_entry_price: "100000",
              cost_basis: "500",
              market_value: "535.5",
            },
            latest_market_regime: {
              event_time: "2026-08-04T20:59:59.999Z",
              regime: "trend",
              trend_direction: "up",
              fast_ema: "106500",
              slow_ema: "104000",
              atr: "1200",
              atr_ratio: "0.0112",
              trend_strength: "2.0833",
            },
            runner: {
              status: "UPDATED",
              started_at: "2026-08-04T21:00:00Z",
              finished_at: "2026-08-04T21:00:02Z",
              state_id:
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
              candles_processed: 120,
              last_candle_open_time: "2026-08-04T20:59:00Z",
              error_code: null,
              matches_current_state: true,
            },
          },
          {
            session_id:
              "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
            symbol: "ETH/USDT",
            base_asset: "ETH",
            quote_asset: "USDT",
            timeframe: "5m",
            strategy_name: "no-op",
            strategy_version: "2",
            initial_capital: "2000",
            state_available: false,
            candles_processed: null,
            last_candle_open_time: null,
            replayed_at: null,
            orders_count: 0,
            fills_count: 0,
            open_orders_count: 0,
            risk_halt: null,
            metrics: null,
            portfolio: null,
            position: null,
            latest_market_regime: null,
            runner: null,
          },
        ],
        totals: {
          scope: "page",
          sessions_count: 2,
          initialized_count: 1,
          pending_count: 1,
          runner_failed_count: 0,
          risk_halted_count: 0,
          open_positions_count: 1,
          open_orders_count: 1,
          configured_capital: "3000",
          initialized_capital: "1000",
          equity: "1125.5",
          total_pnl: "125.5",
          return_pct: "12.55",
          maximum_drawdown_pct: "0.71",
        },
        page,
        page_size: pageSize,
        total: 2,
        total_pages: 1,
        runner: {
          cycle_index: 9,
          status: "COMPLETED",
          finished_at: "2026-08-04T21:00:02Z",
          next_cycle_at: "2026-08-04T21:00:32Z",
        },
      };
      await this.json(route, 200, response);
      return;
    }

    if (
      request.method() === "GET" &&
      url.pathname === "/api/v1/admin/simulations"
    ) {
      const page = Number(url.searchParams.get("page") ?? "1");
      const pageSize = Number(url.searchParams.get("page_size") ?? "20");
      const start = (page - 1) * pageSize;
      const total = this.simulations.length;
      await this.json(route, 200, {
        items: this.simulations.slice(start, start + pageSize).map(asListItem),
        pagination: {
          page,
          page_size: pageSize,
          total,
          total_pages: total === 0 ? 0 : Math.ceil(total / pageSize),
        },
      });
      return;
    }

    if (
      request.method() === "POST" &&
      url.pathname === "/api/v1/admin/simulations"
    ) {
      if (
        this.simulations.some((simulation) => simulation.status === "ACTIVE")
      ) {
        await this.apiError(
          route,
          409,
          "active_simulation_exists",
          "Já existe uma simulação ativa.",
        );
        return;
      }
      const body = this.requestBody<SimulationCreateRequest>(request);
      const simulation = createSimulation({
        name: body.name,
        currency: body.currency,
        initial_capital: normalizeDecimal(body.initial_capital),
        current_balance: normalizeDecimal(body.initial_capital),
      });
      this.simulations = [simulation, ...this.simulations];
      this.movements.set(simulation.id, [
        createInitialMovement(simulation.id, simulation.initial_capital),
      ]);
      await this.json(route, 201, simulation);
      return;
    }

    const detailMatch = /^\/api\/v1\/admin\/simulations\/([^/]+)$/.exec(
      url.pathname,
    );
    if (request.method() === "GET" && detailMatch) {
      const simulation = this.simulations.find(
        (item) => item.id === decodeURIComponent(detailMatch[1]),
      );
      if (!simulation) {
        await this.apiError(
          route,
          404,
          "simulation_not_found",
          "Simulação não encontrada.",
        );
        return;
      }
      await this.json(route, 200, simulation);
      return;
    }

    const movementMatch =
      /^\/api\/v1\/admin\/simulations\/([^/]+)\/movements$/.exec(url.pathname);
    if (request.method() === "GET" && movementMatch) {
      const simulationId = decodeURIComponent(movementMatch[1]);
      const items = this.movements.get(simulationId) ?? [];
      const page = Number(url.searchParams.get("page") ?? "1");
      const pageSize = Number(url.searchParams.get("page_size") ?? "20");
      const start = (page - 1) * pageSize;
      await this.json(route, 200, {
        items: items.slice(start, start + pageSize),
        pagination: {
          page,
          page_size: pageSize,
          total: items.length,
          total_pages:
            items.length === 0 ? 0 : Math.ceil(items.length / pageSize),
        },
      });
      return;
    }

    if (request.method() === "POST" && movementMatch) {
      const simulationId = decodeURIComponent(movementMatch[1]);
      const simulation = this.simulations.find(
        (item) => item.id === simulationId,
      );
      if (!simulation) {
        await this.apiError(
          route,
          404,
          "simulation_not_found",
          "Simulação não encontrada.",
        );
        return;
      }
      const body = this.requestBody<MovementCreateRequest>(request);
      const nextBalance =
        decimalToBigInt(simulation.current_balance) +
        decimalToBigInt(body.amount);
      if (nextBalance < 0n) {
        await this.apiError(
          route,
          409,
          "insufficient_balance",
          "Saldo insuficiente.",
        );
        return;
      }
      this.movementSequence += 1;
      const ledgerTypes = {
        DEPOSIT: "ADMIN_DEPOSIT",
        WITHDRAWAL: "ADMIN_WITHDRAWAL",
        ADJUSTMENT: "ADJUSTMENT",
      } as const;
      const movement: CapitalMovement = {
        id: `55555555-5555-4555-8555-${this.movementSequence
          .toString()
          .padStart(12, "0")}`,
        simulation_id: simulationId,
        type: ledgerTypes[body.type],
        amount: normalizeDecimal(body.amount),
        reason: body.reason,
        reference_id: null,
        created_by: ADMIN_ID,
        created_at: "2026-07-29T15:05:00.000Z",
        metadata: body.metadata ?? null,
      };
      this.movements.set(simulationId, [
        movement,
        ...(this.movements.get(simulationId) ?? []),
      ]);
      simulation.current_balance = bigIntToDecimal(nextBalance);
      simulation.updated_at = movement.created_at;
      await this.json(route, 201, movement);
      return;
    }

    const transitionMatch =
      /^\/api\/v1\/admin\/simulations\/([^/]+)\/(complete|cancel)$/.exec(
        url.pathname,
      );
    if (request.method() === "POST" && transitionMatch) {
      const simulation = this.simulations.find(
        (item) => item.id === decodeURIComponent(transitionMatch[1]),
      );
      if (!simulation) {
        await this.apiError(
          route,
          404,
          "simulation_not_found",
          "Simulação não encontrada.",
        );
        return;
      }
      simulation.status =
        transitionMatch[2] === "complete" ? "COMPLETED" : "CANCELLED";
      simulation.ended_at = "2026-07-29T16:00:00.000Z";
      simulation.updated_at = simulation.ended_at;
      await this.json(route, 200, simulation);
      return;
    }

    if (
      request.method() === "GET" &&
      url.pathname === "/api/v1/admin/settings"
    ) {
      await this.json(route, 200, { items: this.settings });
      return;
    }

    const settingMatch = /^\/api\/v1\/admin\/settings\/([^/]+)$/.exec(
      url.pathname,
    );
    if (request.method() === "PATCH" && settingMatch) {
      const key = decodeURIComponent(settingMatch[1]);
      const body = this.requestBody<{ value: JsonValue }>(request);
      const setting = this.settings.find((item) => item.key === key);
      if (!setting) {
        await this.apiError(
          route,
          404,
          "setting_not_found",
          "Configuração não encontrada.",
        );
        return;
      }
      setting.value = body.value;
      setting.updated_by = ADMIN_ID;
      setting.updated_at = "2026-07-29T15:10:00.000Z";
      this.settings = [...this.settings];
      await this.json(route, 200, setting);
      return;
    }

    this.unexpectedRequests.push(
      `${request.method()} ${url.origin}${url.pathname}${url.search}`,
    );
    await this.apiError(
      route,
      501,
      "endpoint_not_mocked",
      "Endpoint FastAPI não mockado.",
    );
  }
}
