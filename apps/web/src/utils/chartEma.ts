import type { MarketCandle } from "../types/api";

export interface ChartLinePoint {
  time: string;
  value: number;
}

function finiteChartNumber(value: string): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    throw new Error(
      "O candle contém valor incompatível com a projeção visual.",
    );
  }
  return parsed;
}

/**
 * Build a visual EMA projection from the currently loaded closed candles.
 *
 * The backend remains authoritative. This function mirrors the engine's
 * arithmetic seed and recurrence, but the browser renderer necessarily uses
 * IEEE-754 numbers instead of the engine's fixed Decimal context.
 */
export function calculateChartEma(
  candles: readonly MarketCandle[],
  period: number,
): ChartLinePoint[] {
  if (!Number.isInteger(period) || period < 1) {
    throw new Error("O período da EMA deve ser um inteiro positivo.");
  }
  if (candles.length < period) return [];

  const closes = candles.map((candle) => finiteChartNumber(candle.close));
  const points: ChartLinePoint[] = [];
  let previous =
    closes.slice(0, period).reduce((total, value) => total + value, 0) / period;
  points.push({
    time: candles[period - 1].open_time,
    value: previous,
  });

  const alpha = 2 / (period + 1);
  for (let index = period; index < closes.length; index += 1) {
    previous = previous + alpha * (closes[index] - previous);
    points.push({
      time: candles[index].open_time,
      value: previous,
    });
  }
  return points;
}
