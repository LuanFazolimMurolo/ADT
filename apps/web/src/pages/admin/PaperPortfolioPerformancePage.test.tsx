import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  PaperDashboardSession,
  PaperPortfolioTimelinePageResponse,
} from "../../types/api";
import { PaperPortfolioPerformancePage } from "./PaperPortfolioPerformancePage";

const mocks = vi.hoisted(() => ({
  getPaperTradingDashboard: vi.fn(),
  getPaperPortfolioTimeline: vi.fn(),
}));

vi.mock("../../http/client", () => ({ apiClient: mocks }));

vi.mock("../../components/PortfolioPerformanceCharts", () => ({
  PortfolioPerformanceCharts: ({
    observations,
    comparison,
  }: {
    observations: readonly unknown[];
    comparison?: { observations: readonly unknown[] } | null;
  }) => (
    <div data-testid="portfolio-performance-charts">
      {observations.length} pontos
      {comparison ? ` + ${comparison.observations.length} comparação` : ""}
    </div>
  ),
}));

const sessionId = "a".repeat(64);
const comparisonSessionId = "9".repeat(64);

const dashboardSession = (
  id: string,
  symbol = "BTC/USDT",
): PaperDashboardSession =>
  ({
    session_id: id,
    symbol,
    base_asset: symbol.split("/")[0],
    quote_asset: symbol.split("/")[1],
    timeframe: "1m",
    strategy_name: "test",
    strategy_version: "1",
    state_available: true,
  }) as PaperDashboardSession;

const timeline: PaperPortfolioTimelinePageResponse = {
  schema_version: 1,
  session_id: sessionId,
  config_checksum: "b".repeat(64),
  state_id: "c".repeat(64),
  state_checksum: "d".repeat(64),
  state_replayed_at: "2026-08-08T00:04:30Z",
  symbol: "BTC/USDT",
  base_asset: "BTC",
  quote_asset: "USDT",
  timeframe: "1m",
  dataset_version: "e".repeat(64),
  source_checksum: "f".repeat(64),
  timeline_id: "1".repeat(64),
  timeline_content_checksum: "2".repeat(64),
  initial_capital: "1000",
  requested_before: null,
  available_start: "2026-08-08T00:00:00Z",
  available_end: "2026-08-08T00:04:00Z",
  range_start: "2026-08-08T00:00:00Z",
  range_end: "2026-08-08T00:04:00Z",
  limit: 5000,
  count: 4,
  total_observations: 4,
  has_more_before: false,
  next_before: null,
  content_checksum: "3".repeat(64),
  items: [
    {
      candle_index: 0,
      candle_open_time: "2026-08-08T00:00:00Z",
      candle_close_time: "2026-08-08T00:00:59.999Z",
      mark_price: "100",
      quote_cash: "900",
      base_quantity: "1",
      average_entry_price: "100",
      cost_basis: "100",
      realized_pnl: "0",
      unrealized_pnl: "0",
      total_fees: "0.1",
      total_slippage_cost: "0.02",
      equity: "1000",
      peak_equity: "1000",
      drawdown: "0",
      drawdown_pct: "0",
      risk_halt: false,
    },
    {
      candle_index: 1,
      candle_open_time: "2026-08-08T00:01:00Z",
      candle_close_time: "2026-08-08T00:01:59.999Z",
      mark_price: "110",
      quote_cash: "900",
      base_quantity: "1",
      average_entry_price: "100",
      cost_basis: "100",
      realized_pnl: "0",
      unrealized_pnl: "10",
      total_fees: "0.1",
      total_slippage_cost: "0.02",
      equity: "1010",
      peak_equity: "1010",
      drawdown: "0",
      drawdown_pct: "0",
      risk_halt: false,
    },
    {
      candle_index: 2,
      candle_open_time: "2026-08-08T00:02:00Z",
      candle_close_time: "2026-08-08T00:02:59.999Z",
      mark_price: "95",
      quote_cash: "950",
      base_quantity: "0.5",
      average_entry_price: "100",
      cost_basis: "50",
      realized_pnl: "-2.5",
      unrealized_pnl: "-2.5",
      total_fees: "0.2",
      total_slippage_cost: "0.04",
      equity: "997.5",
      peak_equity: "1010",
      drawdown: "12.5",
      drawdown_pct: "1.2376237623762376",
      risk_halt: false,
    },
    {
      candle_index: 3,
      candle_open_time: "2026-08-08T00:03:00Z",
      candle_close_time: "2026-08-08T00:03:59.999Z",
      mark_price: "120",
      quote_cash: "950",
      base_quantity: "0.5",
      average_entry_price: "100",
      cost_basis: "50",
      realized_pnl: "-2.5",
      unrealized_pnl: "10",
      total_fees: "0.2",
      total_slippage_cost: "0.04",
      equity: "1010",
      peak_equity: "1010",
      drawdown: "0",
      drawdown_pct: "0",
      risk_halt: false,
    },
  ],
};

const comparisonTimeline: PaperPortfolioTimelinePageResponse = {
  ...timeline,
  session_id: comparisonSessionId,
  config_checksum: "4".repeat(64),
  state_id: "5".repeat(64),
  state_checksum: "6".repeat(64),
  timeline_id: "7".repeat(64),
  timeline_content_checksum: "8".repeat(64),
  content_checksum: "0".repeat(64),
  items: timeline.items.map((item) => ({
    ...item,
    equity: item.candle_index === 3 ? "1020" : item.equity,
    drawdown_pct: item.candle_index === 2 ? "2.5" : item.drawdown_pct,
  })),
};

beforeEach(() => {
  mocks.getPaperTradingDashboard.mockReset();
  mocks.getPaperPortfolioTimeline.mockReset();
  mocks.getPaperTradingDashboard.mockResolvedValue({
    items: [dashboardSession(sessionId), dashboardSession(comparisonSessionId)],
  });
  mocks.getPaperPortfolioTimeline.mockImplementation((id: string) =>
    Promise.resolve(id === comparisonSessionId ? comparisonTimeline : timeline),
  );
});

describe("PaperPortfolioPerformancePage", () => {
  it("carrega a timeline persistida pelo session_id da URL", async () => {
    render(
      <MemoryRouter
        initialEntries={[
          `/admin/paper-trading/performance?session_id=${sessionId}`,
        ]}
      >
        <PaperPortfolioPerformancePage />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "Performance histórica" }),
    ).toBeDefined();
    expect(
      (await screen.findByTestId("portfolio-performance-charts")).textContent,
    ).toContain("4 pontos");
    expect(mocks.getPaperPortfolioTimeline).toHaveBeenCalledWith(sessionId, {
      limit: 5000,
    });
    expect(screen.getAllByText("USDT 1.010,00").length).toBeGreaterThanOrEqual(
      3,
    );
    expect(
      screen.getByText(/Maior no recorte 1,2376237623762376%/),
    ).toBeDefined();
    expect(screen.getByText(/não executa estratégia/)).toBeDefined();
  });

  it("carrega no máximo uma segunda timeline e mostra comparação textual", async () => {
    render(
      <MemoryRouter
        initialEntries={[
          `/admin/paper-trading/performance?session_id=${sessionId}`,
        ]}
      >
        <PaperPortfolioPerformancePage />
      </MemoryRouter>,
    );

    const select = await screen.findByLabelText("Comparar com");
    fireEvent.change(select, { target: { value: comparisonSessionId } });

    expect(
      await screen.findByRole("heading", { name: "Sessões lado a lado" }),
    ).toBeDefined();
    expect(mocks.getPaperPortfolioTimeline).toHaveBeenCalledWith(
      comparisonSessionId,
      { limit: 5000 },
    );
    expect(
      (await screen.findByTestId("portfolio-performance-charts")).textContent,
    ).toContain("+ 4 comparação");
    expect(screen.getByText("USDT 1.020,00")).toBeDefined();
    expect(
      screen.getByText("Máximo 2 sessões · 5000 pontos por sessão"),
    ).toBeDefined();
  });

  it("bloqueia overlay nominal entre moedas de cotação diferentes", async () => {
    mocks.getPaperTradingDashboard.mockResolvedValue({
      items: [
        dashboardSession(sessionId),
        dashboardSession(comparisonSessionId, "ETH/BRL"),
      ],
    });
    mocks.getPaperPortfolioTimeline.mockImplementation((id: string) =>
      Promise.resolve(
        id === comparisonSessionId
          ? {
              ...comparisonTimeline,
              symbol: "ETH/BRL",
              base_asset: "ETH",
              quote_asset: "BRL",
            }
          : timeline,
      ),
    );

    render(
      <MemoryRouter
        initialEntries={[
          `/admin/paper-trading/performance?session_id=${sessionId}`,
        ]}
      >
        <PaperPortfolioPerformancePage />
      </MemoryRouter>,
    );

    fireEvent.change(await screen.findByLabelText("Comparar com"), {
      target: { value: comparisonSessionId },
    });

    expect(
      await screen.findByText(/moedas de cotação diferentes/),
    ).toBeDefined();
    expect(
      screen.queryByRole("heading", { name: "Sessões lado a lado" }),
    ).toBeNull();
  });

  it("explicita quando o recorte foi limitado aos pontos mais recentes", async () => {
    mocks.getPaperPortfolioTimeline.mockImplementation((id: string) =>
      Promise.resolve(
        id === comparisonSessionId
          ? comparisonTimeline
          : {
              ...timeline,
              count: 4,
              total_observations: 9000,
              has_more_before: true,
              next_before: timeline.range_start,
            },
      ),
    );

    render(
      <MemoryRouter
        initialEntries={[
          `/admin/paper-trading/performance?session_id=${sessionId}`,
        ]}
      >
        <PaperPortfolioPerformancePage />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText(/pontos mais recentes de 9000/),
    ).toBeDefined();
  });

  it("não consulta timeline até uma sessão válida ser selecionada", async () => {
    render(
      <MemoryRouter initialEntries={["/admin/paper-trading/performance"]}>
        <PaperPortfolioPerformancePage />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("heading", { name: "Selecione uma sessão" }),
    ).toBeDefined();
    expect(mocks.getPaperPortfolioTimeline).not.toHaveBeenCalled();
  });
});
