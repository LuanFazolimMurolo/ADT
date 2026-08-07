import { describe, expect, it } from "vitest";
import type { MarketCandle } from "../types/api";
import { calculateChartEma } from "./chartEma";

function candle(index: number, close: string): MarketCandle {
  const open = new Date(Date.UTC(2026, 0, 1, 0, index));
  const closeTime = new Date(open.getTime() + 60_000);
  return {
    open_time: open.toISOString(),
    close_time: closeTime.toISOString(),
    open: close,
    high: close,
    low: close,
    close,
    volume: "1",
    quote_volume: null,
    trade_count: null,
    is_closed: true,
    source: "binance",
  };
}

describe("calculateChartEma", () => {
  it("usa média aritmética como seed e recorrência 2/(período+1)", () => {
    const result = calculateChartEma(
      [candle(0, "1"), candle(1, "2"), candle(2, "3"), candle(3, "4")],
      3,
    );

    expect(result).toEqual([
      {
        time: "2026-01-01T00:02:00.000Z",
        value: 2,
      },
      {
        time: "2026-01-01T00:03:00.000Z",
        value: 3,
      },
    ]);
  });

  it("não publica valores durante o warmup", () => {
    expect(calculateChartEma([candle(0, "1")], 2)).toEqual([]);
  });

  it("rejeita período e valor incompatíveis", () => {
    expect(() => calculateChartEma([candle(0, "1")], 0)).toThrow(
      /inteiro positivo/,
    );
    expect(() => calculateChartEma([candle(0, "não-numérico")], 1)).toThrow(
      /incompatível/,
    );
  });
});
