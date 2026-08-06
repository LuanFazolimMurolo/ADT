import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PaperPeriodMetricsSeriesResponse } from "../../types/api";
import { PaperPeriodMetricsPage } from "./PaperPeriodMetricsPage";

const mocks = vi.hoisted(() => ({
  getPaperPeriodMetrics: vi.fn(),
}));

vi.mock("../../http/client", () => ({ apiClient: mocks }));

const series: PaperPeriodMetricsSeriesResponse = {
  schema_version: 1,
  granularity: "DAILY",
  filters: {
    quote_asset: "USDT",
    period_from: "2026-08-01T00:00:00Z",
    period_before: "2026-08-03T00:00:00Z",
    session_id: null,
    base_asset: null,
    timeframe: null,
    strategy_name: null,
    strategy_version: null,
  },
  source_states: [
    {
      session_id: "a".repeat(64),
      config_checksum: "b".repeat(64),
      state_id: "c".repeat(64),
      state_checksum: "d".repeat(64),
      base_asset: "BTC",
      quote_asset: "USDT",
      last_candle_open_time: "2026-08-02T23:59:00Z",
      replayed_at: "2026-08-03T00:01:00Z",
    },
  ],
  items: [
    {
      period_start: "2026-08-01T00:00:00Z",
      period_end: "2026-08-02T00:00:00Z",
      quote_asset: "USDT",
      realizations_count: 2,
      winning_realizations_count: 1,
      losing_realizations_count: 1,
      breakeven_realizations_count: 0,
      sessions_count: 1,
      symbols_count: 1,
      exit_notional: "220",
      released_cost_basis: "203.8",
      realized_fees: "0.7",
      realized_slippage_cost: "0.2",
      gross_profit: "20",
      gross_loss: "-4.5",
      realized_pnl: "15.5",
      win_rate_pct: "50",
      profit_factor: "4.444444444444444444444444444",
    },
    {
      period_start: "2026-08-02T00:00:00Z",
      period_end: "2026-08-03T00:00:00Z",
      quote_asset: "USDT",
      realizations_count: 0,
      winning_realizations_count: 0,
      losing_realizations_count: 0,
      breakeven_realizations_count: 0,
      sessions_count: 0,
      symbols_count: 0,
      exit_notional: "0",
      released_cost_basis: "0",
      realized_fees: "0",
      realized_slippage_cost: "0",
      gross_profit: "0",
      gross_loss: "0",
      realized_pnl: "0",
      win_rate_pct: null,
      profit_factor: null,
    },
  ],
  totals: {
    periods_count: 2,
    active_periods_count: 1,
    quote_asset: "USDT",
    realizations_count: 2,
    winning_realizations_count: 1,
    losing_realizations_count: 1,
    breakeven_realizations_count: 0,
    sessions_count: 1,
    symbols_count: 1,
    exit_notional: "220",
    released_cost_basis: "203.8",
    realized_fees: "0.7",
    realized_slippage_cost: "0.2",
    gross_profit: "20",
    gross_loss: "-4.5",
    realized_pnl: "15.5",
    win_rate_pct: "50",
    profit_factor: "4.444444444444444444444444444",
  },
  query_checksum: "e".repeat(64),
  content_checksum: "f".repeat(64),
};

interface DeferredSeries {
  promise: Promise<PaperPeriodMetricsSeriesResponse>;
  resolve(value: PaperPeriodMetricsSeriesResponse): void;
}

function deferredSeries(): DeferredSeries {
  let resolvePromise:
    ((value: PaperPeriodMetricsSeriesResponse) => void) | undefined;
  const promise = new Promise<PaperPeriodMetricsSeriesResponse>((resolve) => {
    resolvePromise = resolve;
  });

  return {
    promise,
    resolve(value) {
      if (!resolvePromise) throw new Error("Deferred resolver unavailable");
      resolvePromise(value);
    },
  };
}

async function resolveSeries(
  request: DeferredSeries,
  value: PaperPeriodMetricsSeriesResponse,
): Promise<void> {
  await act(async () => {
    request.resolve(value);
    await request.promise;
  });
}

beforeEach(() => {
  mocks.getPaperPeriodMetrics.mockReset();
});

describe("PaperPeriodMetricsPage", () => {
  it("renderiza série contábil e explicita a limitação mark-to-market", async () => {
    const initialRequest = deferredSeries();
    mocks.getPaperPeriodMetrics.mockReturnValueOnce(initialRequest.promise);

    render(<PaperPeriodMetricsPage />);
    await resolveSeries(initialRequest, series);

    expect(screen.getAllByText("USDT 15,50")).toHaveLength(2);
    expect(screen.getByText("Performance por período")).toBeDefined();
    expect(
      screen.getByText(/PnL não realizado histórico, equity e drawdown/),
    ).toBeDefined();
    expect(mocks.getPaperPeriodMetrics).toHaveBeenCalledWith(
      expect.objectContaining({ quoteAsset: "USDT" }),
      "DAILY",
    );
  });

  it("aplica moeda e granularidade somente após submissão", async () => {
    const brlSeries: PaperPeriodMetricsSeriesResponse = {
      ...series,
      granularity: "MONTHLY",
      filters: { ...series.filters, quote_asset: "BRL" },
      items: series.items.map((item) => ({ ...item, quote_asset: "BRL" })),
      totals: { ...series.totals, quote_asset: "BRL" },
    };
    const initialRequest = deferredSeries();
    const filteredRequest = deferredSeries();
    mocks.getPaperPeriodMetrics
      .mockReturnValueOnce(initialRequest.promise)
      .mockReturnValueOnce(filteredRequest.promise);

    render(<PaperPeriodMetricsPage />);
    await resolveSeries(initialRequest, series);

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Moeda de cotação"), {
        target: { value: "brl" },
      });
      fireEvent.change(screen.getByLabelText("Granularidade"), {
        target: { value: "MONTHLY" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Aplicar período" }));
    });

    expect(mocks.getPaperPeriodMetrics).toHaveBeenLastCalledWith(
      expect.objectContaining({ quoteAsset: "BRL" }),
      "MONTHLY",
    );

    await resolveSeries(filteredRequest, brlSeries);
    expect(screen.getAllByText("BRL 15,50")).toHaveLength(2);
  });

  it("preserva buckets vazios na série contínua", async () => {
    const initialRequest = deferredSeries();
    mocks.getPaperPeriodMetrics.mockReturnValueOnce(initialRequest.promise);

    render(<PaperPeriodMetricsPage />);
    await resolveSeries(initialRequest, series);

    expect(screen.getByText("1 períodos com realizações · USDT")).toBeDefined();
    expect(screen.getAllByText("USDT 0,00").length).toBeGreaterThan(0);
  });
});
