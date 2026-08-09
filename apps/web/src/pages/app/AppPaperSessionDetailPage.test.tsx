import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  AppPaperChartAnnotationPage,
  AppPaperSessionDetail,
  AppPaperTradePage,
  MarketCandle,
  MarketCandlePageResponse,
} from "../../types/api";
import { ApiError } from "../../http/client";
import { AppPaperSessionDetailPage } from "./AppPaperSessionDetailPage";

const mocks = vi.hoisted(() => ({
  getAppPaperSession: vi.fn(),
  getAppMarketCandles: vi.fn(),
  getAppPaperChartAnnotations: vi.fn(),
  getAppPaperTrades: vi.fn(),
}));

vi.mock("../../http/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../http/client")>()),
  apiClient: mocks,
}));

vi.mock("../../components/FinancialCandlestickChart", () => ({
  FinancialCandlestickChart: ({
    candles,
  }: {
    candles: readonly MarketCandle[];
  }) => (
    <div aria-label="chart mock">Candles renderizados: {candles.length}</div>
  ),
}));

const SESSION_ID = "a".repeat(64);
const DATASET_VERSION = "b".repeat(64);

const detail: AppPaperSessionDetail = {
  session_id: SESSION_ID,
  base_asset: "BTC",
  quote_asset: "USDT",
  timeframe: "15m",
  strategy_name: "paper-buy-test",
  strategy_version: "1",
  state_available: true,
  last_candle_open_time: "2026-08-08T00:00:00Z",
};

const candle: MarketCandle = {
  open_time: "2026-08-08T00:00:00Z",
  close_time: "2026-08-08T00:14:59.999Z",
  open: "100",
  high: "110",
  low: "90",
  close: "105",
  volume: "2.5",
  quote_volume: null,
  trade_count: 10,
  is_closed: true,
  source: "fixture",
};

function candlePage(
  datasetVersion = DATASET_VERSION,
): MarketCandlePageResponse {
  return {
    schema_version: 1,
    exchange: "binance",
    market_type: "spot",
    symbol: "BTC/USDT",
    base_asset: "BTC",
    quote_asset: "USDT",
    timeframe: "15m",
    requested_before: null,
    available_start: candle.open_time,
    available_end: candle.close_time,
    range_start: candle.open_time,
    range_end: candle.close_time,
    limit: 5000,
    count: 1,
    dataset_candle_count: 1,
    dataset_version: datasetVersion,
    dataset_version_algorithm: "sha256",
    content_checksum: "c".repeat(64),
    has_more_before: false,
    next_before: null,
    items: [candle],
  };
}

function annotationPage(
  datasetVersion = DATASET_VERSION,
): AppPaperChartAnnotationPage {
  return {
    session_id: SESSION_ID,
    base_asset: "BTC",
    quote_asset: "USDT",
    timeframe: "15m",
    state_available: true,
    dataset_version: datasetVersion,
    range_start: candle.open_time,
    range_end: "2026-08-08T00:15:00Z",
    count: 2,
    orders_count: 1,
    fills_count: 1,
    orders: [
      {
        order_id: "O000000000001",
        created_at: "2026-08-08T00:05:00Z",
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
        event_time: "2026-08-08T00:05:00Z",
        side: "BUY",
        quantity: "1.000000000000000000",
        execution_price: "100.123456789012345678",
        fee: "0.100000000000000000",
        slippage_cost: "0.010000000000000000",
        is_engine_protective_stop: false,
      },
    ],
    last_candle_open_time: candle.open_time,
    content_checksum: "d".repeat(64),
  };
}

function tradePage(
  overrides: Partial<AppPaperTradePage> = {},
): AppPaperTradePage {
  return {
    items: [
      {
        trade_id: "T000000000001",
        sequence: 1,
        status: "OPEN",
        opened_at: "2026-08-08T00:05:00Z",
        closed_at: null,
        opened_quantity: "1.000000000000000000",
        closed_quantity: "0.000000000000000000",
        remaining_quantity: "1.000000000000000000",
        average_entry_price: "100.123456789012345678",
        average_exit_price: null,
        realized_pnl: "0.000000000000000000",
        unrealized_pnl: "5.123456789012345678",
        net_pnl: "5.123456789012345678",
        total_fees: "0.100000000000000000",
        total_slippage_cost: "0.010000000000000000",
        mark_price: "105.246913578024691356",
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
      total_unrealized_pnl: "5.123456789012345678",
      total_net_pnl: "5.123456789012345678",
      total_fees: "0.100000000000000000",
      total_slippage_cost: "0.010000000000000000",
    },
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/app/sessions/${SESSION_ID}`]}>
      <Routes>
        <Route
          path="/app/sessions/:sessionId"
          element={<AppPaperSessionDetailPage />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mocks.getAppPaperSession.mockResolvedValue(detail);
  mocks.getAppMarketCandles.mockResolvedValue(candlePage());
  mocks.getAppPaperChartAnnotations.mockResolvedValue(annotationPage());
  mocks.getAppPaperTrades.mockResolvedValue(tradePage());
});

describe("AppPaperSessionDetailPage", () => {
  it("carrega primeiro o detail e usa somente clientes /app bounded", async () => {
    renderPage();

    expect((await screen.findByLabelText("chart mock")).textContent).toContain(
      "Candles renderizados: 1",
    );
    expect(mocks.getAppPaperSession).toHaveBeenCalledWith(SESSION_ID);
    expect(mocks.getAppMarketCandles).toHaveBeenCalledWith("BTC", "USDT", {
      timeframe: "15m",
      limit: 5000,
    });
    expect(mocks.getAppPaperChartAnnotations).toHaveBeenCalledWith(
      SESSION_ID,
      expect.objectContaining({ limit: 5000 }),
    );
    await waitFor(() =>
      expect(mocks.getAppPaperTrades).toHaveBeenCalledWith(
        SESSION_ID,
        {},
        1,
        20,
      ),
    );
    expect(mocks.getAppPaperSession.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.getAppMarketCandles.mock.invocationCallOrder[0],
    );
  });

  it("renderiza fills, stops, UTC e Decimal strings sem recalcular accounting", async () => {
    renderPage();

    expect(await screen.findByText("Entrada executada")).toBeDefined();
    expect(screen.getByText("Stop protetivo")).toBeDefined();
    expect(screen.getByText("BTC/USDT")).toBeDefined();
    expect(screen.getAllByText(/UTC/).length).toBeGreaterThan(0);
    expect(document.body.textContent).toContain("100.123456789012345678");
    await waitFor(() =>
      expect(document.body.textContent).toContain("5.123456789012345678"),
    );
    expect(document.body.textContent).not.toMatch(/signal/i);
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(
      screen.getByRole("link", { name: "Performance" }).getAttribute("href"),
    ).toBe(`/app/sessions/${SESSION_ID}/performance`);
  });

  it("mostra 403 seguro sem encerrar a sessão nem consultar recursos", async () => {
    mocks.getAppPaperSession.mockRejectedValue(
      new ApiError(403, "forbidden", "internal detail"),
    );
    renderPage();

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Esta conta não possui acesso a esta sessão.",
    );
    expect(document.body.textContent).not.toContain("internal detail");
    expect(mocks.getAppMarketCandles).not.toHaveBeenCalled();
    expect(mocks.getAppPaperChartAnnotations).not.toHaveBeenCalled();
    expect(mocks.getAppPaperTrades).not.toHaveBeenCalled();
  });

  it("distingue 404 autorizado com mensagem segura", async () => {
    mocks.getAppPaperSession.mockRejectedValue(
      new ApiError(404, "not_found", "/private/session/path"),
    );
    renderPage();

    expect((await screen.findByRole("alert")).textContent).toContain(
      "A sessão solicitada não foi encontrada.",
    );
    expect(document.body.textContent).not.toContain("/private/session/path");
  });

  it("rejeita mismatch de dataset sem combinar annotations ao chart", async () => {
    mocks.getAppPaperChartAnnotations.mockResolvedValue(
      annotationPage("e".repeat(64)),
    );
    renderPage();

    expect((await screen.findByRole("alert")).textContent).toContain(
      "datasets incompatíveis",
    );
    expect(screen.queryByLabelText("chart mock")).toBeNull();
  });

  it("pagina trades no backend com page_size bounded", async () => {
    const user = userEvent.setup();
    mocks.getAppPaperTrades
      .mockResolvedValueOnce(tradePage({ total: 21, total_pages: 2 }))
      .mockResolvedValueOnce(
        tradePage({ items: [], page: 2, total: 21, total_pages: 2 }),
      );
    renderPage();
    await screen.findByText("Trades autorizados");
    await waitFor(() =>
      expect(mocks.getAppPaperTrades).toHaveBeenCalledTimes(1),
    );

    await user.click(screen.getByRole("button", { name: "Próxima" }));

    await waitFor(() =>
      expect(mocks.getAppPaperTrades).toHaveBeenLastCalledWith(
        SESSION_ID,
        {},
        2,
        20,
      ),
    );
  });

  it("mantém loading até a decisão inicial do backend", async () => {
    let resolveDetail!: (value: AppPaperSessionDetail) => void;
    mocks.getAppPaperSession.mockReturnValue(
      new Promise((resolve) => {
        resolveDetail = resolve;
      }),
    );
    renderPage();

    expect(screen.getByText("Carregando sessão autorizada…")).toBeDefined();
    resolveDetail(detail);
    expect(await screen.findByLabelText("chart mock")).toBeDefined();
  });
});
