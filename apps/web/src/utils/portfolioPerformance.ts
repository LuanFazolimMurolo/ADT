import type { PaperPortfolioObservation } from "../types/api";

export interface PortfolioChartPoint {
  time: string;
  value: number;
}

export interface PortfolioPerformanceSeries {
  equity: PortfolioChartPoint[];
  realizedPnl: PortfolioChartPoint[];
  unrealizedPnl: PortfolioChartPoint[];
  fees: PortfolioChartPoint[];
  slippage: PortfolioChartPoint[];
  drawdownPct: PortfolioChartPoint[];
}

export interface PortfolioPerformanceSummary {
  latest: PaperPortfolioObservation;
  maxDrawdownPct: string;
  maxDrawdownTime: string;
}

function visualNumber(value: string, field: string): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    throw new Error(`Valor inválido para projeção visual: ${field}.`);
  }
  return parsed;
}

function point(
  observation: PaperPortfolioObservation,
  field: keyof Pick<
    PaperPortfolioObservation,
    | "equity"
    | "realized_pnl"
    | "unrealized_pnl"
    | "total_fees"
    | "total_slippage_cost"
    | "drawdown_pct"
  >,
): PortfolioChartPoint {
  return {
    time: observation.candle_open_time,
    value: visualNumber(observation[field], field),
  };
}

export function buildPortfolioPerformanceSeries(
  observations: readonly PaperPortfolioObservation[],
): PortfolioPerformanceSeries {
  return {
    equity: observations.map((item) => point(item, "equity")),
    realizedPnl: observations.map((item) => point(item, "realized_pnl")),
    unrealizedPnl: observations.map((item) => point(item, "unrealized_pnl")),
    fees: observations.map((item) => point(item, "total_fees")),
    slippage: observations.map((item) => point(item, "total_slippage_cost")),
    drawdownPct: observations.map((item) => point(item, "drawdown_pct")),
  };
}

export function summarizePortfolioPerformance(
  observations: readonly PaperPortfolioObservation[],
): PortfolioPerformanceSummary | null {
  if (observations.length === 0) return null;

  let maxDrawdown = observations[0];
  let maxDrawdownValue = visualNumber(maxDrawdown.drawdown_pct, "drawdown_pct");

  for (const observation of observations.slice(1)) {
    const candidate = visualNumber(observation.drawdown_pct, "drawdown_pct");
    if (candidate > maxDrawdownValue) {
      maxDrawdown = observation;
      maxDrawdownValue = candidate;
    }
  }

  return {
    latest: observations[observations.length - 1],
    maxDrawdownPct: maxDrawdown.drawdown_pct,
    maxDrawdownTime: maxDrawdown.candle_open_time,
  };
}
