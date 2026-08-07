import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  MarketCandlePageResponse,
  PaperChartAnnotationPageResponse,
  PaperDashboardResponse,
} from "../../types/api";
import { InstrumentChartPage } from "./InstrumentChartPage";

const mocks = vi.hoisted(() => ({
  getMarketCandles: vi.fn(),
  getPaperChartAnnotations: vi.fn(),
  getPaperTradingDashboard: vi.fn(),
}));

vi.mock("../../http/client", () => ({
  apiClient: mocks,
}));

vi.mock("../../components/FinancialCandlestickChart", () => ({
  FinancialCandlestickChart: ({
    candles,
    fastPeriod,
    slowPeriod,
    annotations,
  }: {
    candles: readonly unknown[];
    fastPeriod: number | null;
    slowPeriod: number | null;
    annotations: PaperChartAnnotationPageResponse | null;
  }) => (
    <div data-testid="chart">
      Candles: {candles.length} · EMA: {String(fastPeriod)}/{String(slowPeriod)}{" "}
      · Eventos: {annotations?.count ?? 0}
    </div>
  ),
}));

function page(
  openTime: string,
  nextBefore: string | null,
  hasMoreBefore: boolean,
): MarketCandlePageResponse {
  const closeTime = new Date(Date.parse(openTime) + 60_000).toISOString();
  return {
    schema_version: 1,
    exchange: "binance",
    market_type: "spot",
    symbol: "BTCUSDT",
    base_asset: "BTC",
    quote_asset: "USDT",
    timeframe: "1m",
    requested_before: null,
    available_start: openTime,
    available_end: closeTime,
    range_start: openTime,
    range_end: closeTime,
    limit: 1000,
    count: 1,
    dataset_candle_count: 2,
    dataset_version: "a".repeat(64),
    dataset_version_algorithm: "sha256",
    content_checksum: "b".repeat(64),
    has_more_before: hasMoreBefore,
    next_before: nextBefore,
    items: [
      {
        open_time: openTime,
        close_time: closeTime,
        open: "100",
        high: "110",
        low: "90",
        close: "105",
        volume: "1",
        quote_volume: null,
        trade_count: null,
        is_closed: true,
        source: "binance",
      },
    ],
  };
}

const dashboard: PaperDashboardResponse = {
  items: [
    {
      session_id: "1".repeat(64),
      symbol: "BTC/USDT",
      base_asset: "BTC",
      quote_asset: "USDT",
      timeframe: "1m",
      strategy_name: "ema-cross-example",
      strategy_version: "1",
      initial_capital: "1000",
      state_available: true,
      candles_processed: 1,
      last_candle_open_time: "2026-01-01T00:00:00Z",
      replayed_at: "2026-01-01T00:01:00Z",
      orders_count: 1,
      fills_count: 1,
      open_orders_count: 0,
      risk_halt: false,
      metrics: null,
      portfolio: null,
      position: null,
      latest_market_regime: null,
      runner: null,
    },
  ],
  totals: {
    scope: "page",
    sessions_count: 1,
    initialized_count: 1,
    pending_count: 0,
    runner_failed_count: 0,
    risk_halted_count: 0,
    open_positions_count: 0,
    open_orders_count: 0,
    configured_capital: "1000",
    initialized_capital: "1000",
    equity: "1000",
    total_pnl: "0",
    return_pct: "0",
    maximum_drawdown_pct: "0",
  },
  page: 1,
  page_size: 100,
  total: 1,
  total_pages: 1,
  runner: null,
};

const annotations: PaperChartAnnotationPageResponse = {
  schema_version: 1,
  session_id: "1".repeat(64),
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
  strategy_name: "ema-cross-example",
  strategy_version: "1",
  strategy_parameters: {},
  ema_fast_period: 3,
  ema_slow_period: 5,
  range_start: "2026-01-01T00:00:00Z",
  range_end: "2026-01-01T00:01:00Z",
  limit: 5000,
  count: 1,
  orders_count: 0,
  fills_count: 1,
  orders: [],
  fills: [
    {
      fill_id: "F1",
      order_id: "O1",
      trade_id: "6".repeat(64),
      trade_sequence: 1,
      role: "ENTRY",
      event_time: "2026-01-01T00:00:00Z",
      candle_index: 0,
      side: "BUY",
      order_type: "MARKET",
      time_in_force: "GTC",
      client_tag: "entry",
      fill_reason: "MARKET_OPEN",
      liquidity: "TAKER",
      quantity: "1",
      base_price: "100",
      execution_price: "100",
      notional: "100",
      fee: "0",
      slippage_cost: "0",
      is_engine_protective_stop: false,
    },
  ],
  last_candle_open_time: "2026-01-01T00:00:00Z",
  replayed_at: "2026-01-01T00:01:00Z",
  content_checksum: "7".repeat(64),
};

describe("InstrumentChartPage", () => {
  beforeEach(() => {
    mocks.getMarketCandles.mockReset();
    mocks.getPaperChartAnnotations.mockReset();
    mocks.getPaperTradingDashboard.mockReset();
    mocks.getPaperTradingDashboard.mockResolvedValue(dashboard);
    mocks.getPaperChartAnnotations.mockResolvedValue(annotations);
  });

  it("carrega o instrumento da URL e pagina para trás sem duplicar candles", async () => {
    mocks.getMarketCandles
      .mockResolvedValueOnce(
        page("2026-01-01T00:01:00.000Z", "2026-01-01T00:01:00.000Z", true),
      )
      .mockResolvedValueOnce(page("2026-01-01T00:00:00.000Z", null, false));

    render(
      <MemoryRouter
        initialEntries={[
          "/admin/paper-trading/chart?base=BTC&quote=USDT&timeframe=1m",
        ]}
      >
        <InstrumentChartPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/Candles: 1 · EMA: 9\/21/)).toBeDefined();
    expect(mocks.getMarketCandles).toHaveBeenCalledWith("BTC", "USDT", {
      timeframe: "1m",
      limit: 1000,
    });

    await userEvent.click(
      screen.getByRole("button", {
        name: "Carregar histórico anterior",
      }),
    );

    await waitFor(() => expect(screen.getByText(/Candles: 2/)).toBeDefined());
    expect(mocks.getMarketCandles).toHaveBeenLastCalledWith("BTC", "USDT", {
      timeframe: "1m",
      before: "2026-01-01T00:01:00.000Z",
      limit: 1000,
    });
  });

  it("carrega eventos e períodos EMA da sessão selecionada", async () => {
    mocks.getMarketCandles.mockResolvedValue(
      page("2026-01-01T00:00:00.000Z", null, false),
    );

    render(
      <MemoryRouter
        initialEntries={[
          `/admin/paper-trading/chart?session_id=${"1".repeat(64)}&base=BTC&quote=USDT&timeframe=1m&trade_id=${"6".repeat(64)}`,
        ]}
      >
        <InstrumentChartPage />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText(/Candles: 1 · EMA: 3\/5 · Eventos: 1/),
    ).toBeDefined();
    expect(mocks.getPaperChartAnnotations).toHaveBeenCalledWith(
      "1".repeat(64),
      {
        start: "2026-01-01T00:00:00.000Z",
        before: "2026-01-01T00:01:00.000Z",
        limit: 5000,
      },
    );
    expect(screen.getByText("Entrada executada")).toBeDefined();
    expect(
      screen.getByRole("link", { name: "Abrir journal da sessão" }),
    ).toBeDefined();
  });
});
