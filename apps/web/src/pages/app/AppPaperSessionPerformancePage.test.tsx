import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../http/client";
import type {
  AppPaperPeriodMetricsSeries,
  AppPaperPortfolioObservation,
  AppPaperPortfolioTimelinePage,
  AppPaperSessionDetail,
} from "../../types/api";
import { AppPaperSessionPerformancePage } from "./AppPaperSessionPerformancePage";

const mocks = vi.hoisted(() => ({
  getAppPaperSession: vi.fn(),
  getAppPaperPortfolioTimeline: vi.fn(),
  getAppPaperPeriodMetrics: vi.fn(),
}));

vi.mock("../../http/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../http/client")>()),
  apiClient: mocks,
}));

vi.mock("../../components/PortfolioPerformanceCharts", () => ({
  PortfolioPerformanceCharts: ({
    observations,
  }: {
    observations: readonly AppPaperPortfolioObservation[];
  }) => (
    <div aria-label="portfolio charts">
      Equity histórica · PnL realizado · PnL não realizado · Taxas acumuladas ·
      Slippage acumulado · Drawdown · {observations.length}
    </div>
  ),
}));

vi.mock("../../components/PeriodPerformanceCharts", () => ({
  PeriodPerformanceCharts: () => (
    <div aria-label="period charts">PnL realizado por período</div>
  ),
}));

const SESSION_ID = "a".repeat(64);
const detail: AppPaperSessionDetail = {
  session_id: SESSION_ID,
  base_asset: "BTC",
  quote_asset: "USDT",
  timeframe: "15m",
  strategy_name: "paper-buy-test",
  strategy_version: "1",
  state_available: true,
  last_candle_open_time: "2026-08-03T23:45:00Z",
};

function observation(index: number): AppPaperPortfolioObservation {
  const open = new Date(Date.UTC(2026, 7, 1, 0, index * 15));
  const close = new Date(open.getTime() + 15 * 60_000);
  return {
    candle_index: index,
    candle_open_time: open.toISOString(),
    candle_close_time: close.toISOString(),
    mark_price: "105.123456789012345678",
    quote_cash: "900.000000000000000000",
    base_quantity: "1.000000000000000000",
    average_entry_price: "100.000000000000000000",
    cost_basis: "100.000000000000000000",
    realized_pnl: "4.123456789012345678",
    unrealized_pnl: "5.123456789012345678",
    total_fees: "0.100000000000000000",
    total_slippage_cost: "0.010000000000000000",
    equity: "1009.123456789012345678",
    peak_equity: "1010.000000000000000000",
    drawdown: "0.876543210987654322",
    drawdown_pct: "0.086786456533431121",
    risk_halt: false,
  };
}

function timeline(
  overrides: Partial<AppPaperPortfolioTimelinePage> = {},
): AppPaperPortfolioTimelinePage {
  const items = [observation(0), observation(1)];
  return {
    session_id: SESSION_ID,
    state_checksum: "b".repeat(64),
    base_asset: "BTC",
    quote_asset: "USDT",
    timeframe: "15m",
    dataset_version: "c".repeat(64),
    timeline_id: "d".repeat(64),
    timeline_content_checksum: "e".repeat(64),
    initial_capital: "1000.000000000000000000",
    available_start: "2026-08-01T00:00:00Z",
    available_end: "2026-08-04T00:00:00Z",
    range_start: "2026-08-01T00:00:00Z",
    range_end: "2026-08-04T00:00:00Z",
    limit: 1000,
    count: items.length,
    total_observations: items.length,
    has_more_before: false,
    next_before: null,
    content_checksum: "f".repeat(64),
    items,
    ...overrides,
  };
}

function periods(): AppPaperPeriodMetricsSeries {
  return {
    session_id: SESSION_ID,
    quote_asset: "USDT",
    granularity: "DAILY",
    period_from: "2026-08-01T00:00:00Z",
    period_before: "2026-08-04T00:00:00Z",
    items: [
      {
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
      },
    ],
    totals: {
      periods_count: 3,
      active_periods_count: 1,
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
    },
    query_checksum: "1".repeat(64),
    content_checksum: "2".repeat(64),
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/app/sessions/${SESSION_ID}/performance`]}>
      <Routes>
        <Route
          path="/app/sessions/:sessionId/performance"
          element={<AppPaperSessionPerformancePage />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mocks.getAppPaperSession.mockReset();
  mocks.getAppPaperPortfolioTimeline.mockReset();
  mocks.getAppPaperPeriodMetrics.mockReset();
  mocks.getAppPaperSession.mockResolvedValue(detail);
  mocks.getAppPaperPortfolioTimeline.mockResolvedValue(timeline());
  mocks.getAppPaperPeriodMetrics.mockResolvedValue(periods());
});

describe("AppPaperSessionPerformancePage", () => {
  it("carrega detail primeiro e usa somente os endpoints /app bounded", async () => {
    renderPage();

    expect(await screen.findByLabelText("portfolio charts")).toBeDefined();
    expect(mocks.getAppPaperSession).toHaveBeenCalledWith(SESSION_ID);
    expect(mocks.getAppPaperPortfolioTimeline).toHaveBeenCalledWith(
      SESSION_ID,
      {
        limit: 1000,
      },
    );
    expect(mocks.getAppPaperPeriodMetrics).toHaveBeenCalledWith(SESSION_ID, {
      periodFrom: "2026-08-01T00:00:00.000Z",
      periodBefore: "2026-08-04T00:00:00.000Z",
      granularity: "DAILY",
    });
    expect(mocks.getAppPaperSession.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.getAppPaperPortfolioTimeline.mock.invocationCallOrder[0],
    );
  });

  it("renderiza charts, tabela Decimal/UTC, disclosure e provenance acessíveis", async () => {
    renderPage();

    expect(
      await screen.findByText("Performance realizada por período"),
    ).toBeDefined();
    expect(screen.getByLabelText("period charts")).toBeDefined();
    expect(screen.getByText(/Equity histórica/)).toBeDefined();
    expect(screen.getAllByText(/Drawdown/).length).toBeGreaterThan(0);
    expect(document.body.textContent).toContain("1009.123456789012345678");
    expect(document.body.textContent).toContain("0.086786456533431121");
    expect(document.body.textContent).toContain("apenas resultados realizados");
    expect(document.body.textContent).toContain("dddddddddddd…");
    expect(screen.getAllByText(/UTC/).length).toBeGreaterThan(0);
    expect(screen.queryByLabelText(/Session ID/i)).toBeNull();
    expect(screen.queryByLabelText(/Moeda de cotação/i)).toBeNull();
    expect(document.body.textContent).not.toMatch(/signal/i);
    expect(document.body.textContent).not.toMatch(/comparar sessão/i);
  });

  it("mostra 403 seguro sem logout implícito nem leituras posteriores", async () => {
    mocks.getAppPaperSession.mockRejectedValue(
      new ApiError(403, "forbidden", "internal session path"),
    );
    renderPage();

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Esta conta não possui acesso a esta sessão.",
    );
    expect(document.body.textContent).not.toContain("internal session path");
    expect(mocks.getAppPaperPortfolioTimeline).not.toHaveBeenCalled();
    expect(mocks.getAppPaperPeriodMetrics).not.toHaveBeenCalled();
  });

  it("pagina backward sem ultrapassar 5000 observações", async () => {
    const user = userEvent.setup();
    const currentItems = Array.from({ length: 4500 }, (_, index) =>
      observation(500 + index),
    );
    const olderItems = Array.from({ length: 1000 }, (_, index) =>
      observation(index),
    );
    mocks.getAppPaperPortfolioTimeline
      .mockResolvedValueOnce(
        timeline({
          items: currentItems,
          count: currentItems.length,
          total_observations: 5500,
          range_start: "2026-08-02T00:00:00Z",
          has_more_before: true,
          next_before: "2026-08-02T00:00:00Z",
        }),
      )
      .mockResolvedValueOnce(
        timeline({
          items: olderItems,
          count: olderItems.length,
          total_observations: 5500,
          range_end: "2026-08-02T00:00:00Z",
          has_more_before: false,
        }),
      );
    renderPage();
    await screen.findByLabelText("portfolio charts");

    await user.click(
      screen.getByRole("button", { name: "Carregar histórico anterior" }),
    );

    await waitFor(() =>
      expect(mocks.getAppPaperPortfolioTimeline).toHaveBeenLastCalledWith(
        SESSION_ID,
        { before: "2026-08-02T00:00:00Z", limit: 500 },
      ),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("portfolio charts").textContent).toContain(
        "5000",
      ),
    );
  });

  it("rejeita mudança de binding durante a paginação", async () => {
    const user = userEvent.setup();
    mocks.getAppPaperPortfolioTimeline
      .mockResolvedValueOnce(
        timeline({
          has_more_before: true,
          next_before: "2026-08-02T00:00:00Z",
          total_observations: 4,
        }),
      )
      .mockResolvedValueOnce(
        timeline({
          timeline_id: "9".repeat(64),
          total_observations: 4,
        }),
      );
    renderPage();
    await screen.findByLabelText("portfolio charts");

    await user.click(
      screen.getByRole("button", { name: "Carregar histórico anterior" }),
    );

    expect((await screen.findByRole("alert")).textContent).toContain(
      "timeline persistida mudou",
    );
  });

  it("isola erro de period metrics sem remover a timeline", async () => {
    mocks.getAppPaperPeriodMetrics.mockRejectedValue(
      new Error("private metrics path"),
    );
    renderPage();

    expect(await screen.findByLabelText("portfolio charts")).toBeDefined();
    expect((await screen.findByRole("alert")).textContent).toContain(
      "performance realizada por período",
    );
    expect(document.body.textContent).not.toContain("private metrics path");
  });

  it("trata timeline vazia e falha de timeline com estados seguros", async () => {
    mocks.getAppPaperPortfolioTimeline.mockResolvedValueOnce(
      timeline({ items: [], count: 0, total_observations: 0 }),
    );
    const first = renderPage();
    expect(await screen.findByText("Timeline sem observações")).toBeDefined();
    first.unmount();
    mocks.getAppPaperPeriodMetrics.mockClear();

    mocks.getAppPaperPortfolioTimeline.mockRejectedValueOnce(
      new Error("/private/timeline.parquet"),
    );
    renderPage();
    expect((await screen.findByRole("alert")).textContent).toContain(
      "timeline histórica persistida",
    );
    expect(document.body.textContent).not.toContain(
      "/private/timeline.parquet",
    );
    expect(mocks.getAppPaperPeriodMetrics).not.toHaveBeenCalled();
  });
});
