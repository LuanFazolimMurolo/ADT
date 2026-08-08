import { describe, expect, it } from "vitest";
import type { PaperPortfolioObservation } from "../types/api";
import {
  buildPortfolioPerformanceSeries,
  summarizePortfolioPerformance,
} from "./portfolioPerformance";

function observation(
  index: number,
  values: Partial<PaperPortfolioObservation> = {},
): PaperPortfolioObservation {
  return {
    candle_index: index,
    candle_open_time: `2026-08-08T00:0${index}:00Z`,
    candle_close_time: `2026-08-08T00:0${index}:59.999Z`,
    mark_price: "100",
    quote_cash: "900",
    base_quantity: "1",
    average_entry_price: "100",
    cost_basis: "100",
    realized_pnl: "0",
    unrealized_pnl: "0",
    total_fees: "0",
    total_slippage_cost: "0",
    equity: "1000",
    peak_equity: "1000",
    drawdown: "0",
    drawdown_pct: "0",
    risk_halt: false,
    ...values,
  };
}

describe("portfolioPerformance", () => {
  it("projeta somente coordenadas visuais sem alterar os strings-fonte", () => {
    const values = [
      observation(0, {
        equity: "1000.125",
        realized_pnl: "10.5",
        unrealized_pnl: "-2.25",
        total_fees: "0.125",
        total_slippage_cost: "0.05",
        drawdown_pct: "1.75",
      }),
    ];

    const result = buildPortfolioPerformanceSeries(values);

    expect(result.equity).toEqual([
      { time: values[0].candle_open_time, value: 1000.125 },
    ]);
    expect(result.realizedPnl[0].value).toBe(10.5);
    expect(result.unrealizedPnl[0].value).toBe(-2.25);
    expect(result.fees[0].value).toBe(0.125);
    expect(result.slippage[0].value).toBe(0.05);
    expect(result.drawdownPct[0].value).toBe(1.75);
    expect(values[0].equity).toBe("1000.125");
  });

  it("resume o último ponto e o maior drawdown do recorte", () => {
    const values = [
      observation(0, { drawdown_pct: "1.5" }),
      observation(1, { drawdown_pct: "7.25" }),
      observation(2, { drawdown_pct: "3" }),
    ];

    expect(summarizePortfolioPerformance(values)).toEqual({
      latest: values[2],
      maxDrawdownPct: "7.25",
      maxDrawdownTime: values[1].candle_open_time,
    });
  });

  it("rejeita valores não finitos na fronteira de visualização", () => {
    expect(() =>
      buildPortfolioPerformanceSeries([
        observation(0, { equity: "not-a-decimal" }),
      ]),
    ).toThrow(/equity/);
  });
});
