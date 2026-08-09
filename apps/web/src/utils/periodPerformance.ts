import type { PaperPeriodGranularity } from "../types/api";

export interface PeriodMetricsBucketData {
  period_start: string;
  period_end: string;
  realizations_count: number;
  winning_realizations_count: number;
  losing_realizations_count: number;
  breakeven_realizations_count: number;
  realized_fees: string;
  realized_slippage_cost: string;
  realized_pnl: string;
  profit_factor: string | null;
}

export interface PeriodPerformanceSeriesData {
  granularity: PaperPeriodGranularity;
  items: readonly PeriodMetricsBucketData[];
}

export interface PeriodChartPoint {
  time: string;
  value: number;
}

export interface PeriodOutcomeDistribution {
  wins: number;
  losses: number;
  breakeven: number;
  total: number;
}

export interface PeriodHeatmapCell {
  periodStart: string;
  periodEnd: string;
  realizedPnl: string;
  realizationsCount: number;
  intensity: number;
  sign: "positive" | "negative" | "zero";
}

export interface PeriodHeatmapProjection {
  cells: PeriodHeatmapCell[];
  totalBuckets: number;
  visibleBuckets: number;
  truncated: boolean;
}

export interface PeriodPerformanceProjection {
  realizedPnl: PeriodChartPoint[];
  fees: PeriodChartPoint[];
  slippage: PeriodChartPoint[];
  profitFactor: PeriodChartPoint[];
  realizations: PeriodChartPoint[];
  outcomes: PeriodOutcomeDistribution;
}

export const PERIOD_HEATMAP_MAX_BUCKETS = 366;

function visualNumber(value: string, field: string): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    throw new Error(`Valor inválido para visualização por período: ${field}.`);
  }
  return parsed;
}

function metricPoint(
  bucket: PeriodMetricsBucketData,
  field: "realized_pnl" | "realized_fees" | "realized_slippage_cost",
): PeriodChartPoint {
  return {
    time: bucket.period_start,
    value: visualNumber(bucket[field], field),
  };
}

export function buildPeriodPerformanceProjection(
  series: PeriodPerformanceSeriesData,
): PeriodPerformanceProjection {
  const outcomes = series.items.reduce<PeriodOutcomeDistribution>(
    (current, bucket) => ({
      wins: current.wins + bucket.winning_realizations_count,
      losses: current.losses + bucket.losing_realizations_count,
      breakeven: current.breakeven + bucket.breakeven_realizations_count,
      total: current.total + bucket.realizations_count,
    }),
    { wins: 0, losses: 0, breakeven: 0, total: 0 },
  );

  return {
    realizedPnl: series.items.map((bucket) =>
      metricPoint(bucket, "realized_pnl"),
    ),
    fees: series.items.map((bucket) => metricPoint(bucket, "realized_fees")),
    slippage: series.items.map((bucket) =>
      metricPoint(bucket, "realized_slippage_cost"),
    ),
    profitFactor: series.items
      .filter((bucket) => bucket.profit_factor !== null)
      .map((bucket) => ({
        time: bucket.period_start,
        value: visualNumber(bucket.profit_factor ?? "0", "profit_factor"),
      })),
    realizations: series.items.map((bucket) => ({
      time: bucket.period_start,
      value: bucket.realizations_count,
    })),
    outcomes,
  };
}

export function buildPeriodHeatmap(
  series: PeriodPerformanceSeriesData,
  maxBuckets = PERIOD_HEATMAP_MAX_BUCKETS,
): PeriodHeatmapProjection {
  if (!Number.isInteger(maxBuckets) || maxBuckets < 1) {
    throw new Error("O limite visual do heatmap é inválido.");
  }

  const selected = series.items.slice(-maxBuckets);
  const numericValues = selected.map((bucket) =>
    visualNumber(bucket.realized_pnl, "realized_pnl"),
  );
  const maximumMagnitude = numericValues.reduce(
    (maximum, value) => Math.max(maximum, Math.abs(value)),
    0,
  );

  const cells = selected.map((bucket, index) => {
    const numeric = numericValues[index];
    return {
      periodStart: bucket.period_start,
      periodEnd: bucket.period_end,
      realizedPnl: bucket.realized_pnl,
      realizationsCount: bucket.realizations_count,
      intensity:
        maximumMagnitude === 0 ? 0 : Math.abs(numeric) / maximumMagnitude,
      sign: numeric > 0 ? "positive" : numeric < 0 ? "negative" : "zero",
    } satisfies PeriodHeatmapCell;
  });

  return {
    cells,
    totalBuckets: series.items.length,
    visibleBuckets: cells.length,
    truncated: series.items.length > cells.length,
  };
}

export function distributionPercentage(value: number, total: number): number {
  if (total <= 0) return 0;
  return (value / total) * 100;
}
