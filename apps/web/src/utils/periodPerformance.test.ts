import { describe, expect, it } from "vitest";
import type { PaperPeriodMetricsSeriesResponse } from "../types/api";
import {
  buildPeriodHeatmap,
  buildPeriodPerformanceProjection,
  distributionPercentage,
} from "./periodPerformance";

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
  source_states: [],
  items: [
    {
      period_start: "2026-08-01T00:00:00Z",
      period_end: "2026-08-02T00:00:00Z",
      quote_asset: "USDT",
      realizations_count: 3,
      winning_realizations_count: 2,
      losing_realizations_count: 1,
      breakeven_realizations_count: 0,
      sessions_count: 1,
      symbols_count: 1,
      exit_notional: "300",
      released_cost_basis: "280",
      realized_fees: "1.2",
      realized_slippage_cost: "0.3",
      gross_profit: "25",
      gross_loss: "-5",
      realized_pnl: "20",
      win_rate_pct: "66.6666666666666667",
      profit_factor: "5",
    },
    {
      period_start: "2026-08-02T00:00:00Z",
      period_end: "2026-08-03T00:00:00Z",
      quote_asset: "USDT",
      realizations_count: 1,
      winning_realizations_count: 0,
      losing_realizations_count: 0,
      breakeven_realizations_count: 1,
      sessions_count: 1,
      symbols_count: 1,
      exit_notional: "100",
      released_cost_basis: "100",
      realized_fees: "0.5",
      realized_slippage_cost: "0.1",
      gross_profit: "0",
      gross_loss: "0",
      realized_pnl: "-10",
      win_rate_pct: "0",
      profit_factor: null,
    },
  ],
  totals: {
    periods_count: 2,
    active_periods_count: 2,
    quote_asset: "USDT",
    realizations_count: 4,
    winning_realizations_count: 2,
    losing_realizations_count: 1,
    breakeven_realizations_count: 1,
    sessions_count: 1,
    symbols_count: 1,
    exit_notional: "400",
    released_cost_basis: "380",
    realized_fees: "1.7",
    realized_slippage_cost: "0.4",
    gross_profit: "25",
    gross_loss: "-5",
    realized_pnl: "10",
    win_rate_pct: "50",
    profit_factor: "5",
  },
  query_checksum: "a".repeat(64),
  content_checksum: "b".repeat(64),
};

describe("periodPerformance", () => {
  it("projeta PnL, custos, profit factor e contagem sem substituir os strings-fonte", () => {
    const result = buildPeriodPerformanceProjection(series);

    expect(result.realizedPnl.map((point) => point.value)).toEqual([20, -10]);
    expect(result.fees.map((point) => point.value)).toEqual([1.2, 0.5]);
    expect(result.slippage.map((point) => point.value)).toEqual([0.3, 0.1]);
    expect(result.profitFactor).toEqual([
      { time: "2026-08-01T00:00:00Z", value: 5 },
    ]);
    expect(result.realizations.map((point) => point.value)).toEqual([3, 1]);
    expect(series.items[0].realized_pnl).toBe("20");
  });

  it("resume distribuição win/loss/breakeven", () => {
    expect(buildPeriodPerformanceProjection(series).outcomes).toEqual({
      wins: 2,
      losses: 1,
      breakeven: 1,
      total: 4,
    });
  });

  it("calcula percentuais somente para apresentação", () => {
    expect(distributionPercentage(2, 4)).toBe(50);
    expect(distributionPercentage(1, 0)).toBe(0);
  });

  it("gera heatmap visual bounded preservando o Decimal-fonte", () => {
    const heatmap = buildPeriodHeatmap(series, 1);

    expect(heatmap.totalBuckets).toBe(2);
    expect(heatmap.visibleBuckets).toBe(1);
    expect(heatmap.truncated).toBe(true);
    expect(heatmap.cells).toEqual([
      expect.objectContaining({
        periodStart: "2026-08-02T00:00:00Z",
        realizedPnl: "-10",
        realizationsCount: 1,
        intensity: 1,
        sign: "negative",
      }),
    ]);
    expect(series.items[1].realized_pnl).toBe("-10");
  });
});
