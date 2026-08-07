import { describe, expect, it } from "vitest";
import type {
  MarketCandle,
  PaperChartAnnotationPageResponse,
} from "../types/api";
import { buildChartMarkers } from "./chartMarkers";

const candles: MarketCandle[] = [
  {
    open_time: "2026-01-01T00:00:00Z",
    close_time: "2026-01-01T00:01:00Z",
    open: "100",
    high: "105",
    low: "95",
    close: "101",
    volume: "1",
    quote_volume: null,
    trade_count: null,
    is_closed: true,
    source: "test",
  },
  {
    open_time: "2026-01-01T00:01:00Z",
    close_time: "2026-01-01T00:02:00Z",
    open: "101",
    high: "110",
    low: "100",
    close: "109",
    volume: "1",
    quote_volume: null,
    trade_count: null,
    is_closed: true,
    source: "test",
  },
];

function annotations(): PaperChartAnnotationPageResponse {
  return {
    schema_version: 1,
    session_id: "a".repeat(64),
    config_checksum: "b".repeat(64),
    state_available: true,
    state_id: "c".repeat(64),
    state_checksum: "d".repeat(64),
    dataset_version: "e".repeat(64),
    source_checksum: "f".repeat(64),
    symbol: "BTC/USDT",
    base_asset: "BTC",
    quote_asset: "USDT",
    timeframe: "1m",
    strategy_name: "ema-cross-example",
    strategy_version: "1",
    strategy_parameters: {},
    ema_fast_period: 3,
    ema_slow_period: 5,
    range_start: "2026-01-01T00:00:00Z",
    range_end: "2026-01-01T00:02:00Z",
    limit: 5000,
    count: 3,
    orders_count: 1,
    fills_count: 2,
    orders: [
      {
        order_id: "O2",
        created_sequence: 2,
        created_at: "2026-01-01T00:01:00Z",
        opened_at: "2026-01-01T00:01:00Z",
        terminal_at: null,
        side: "SELL",
        order_type: "STOP_MARKET",
        time_in_force: "GTC",
        status: "OPEN",
        quantity: "1",
        limit_price: null,
        stop_price: "95",
        client_tag: "engine-stop-loss",
        rejection_code: null,
        is_engine_protective_stop: true,
      },
    ],
    fills: [
      {
        fill_id: "F1",
        order_id: "O1",
        trade_id: "1".repeat(64),
        trade_sequence: 1,
        role: "ENTRY",
        event_time: "2026-01-01T00:01:00Z",
        candle_index: 1,
        side: "BUY",
        order_type: "MARKET",
        time_in_force: "GTC",
        client_tag: "entry",
        fill_reason: "MARKET_OPEN",
        liquidity: "TAKER",
        quantity: "1",
        base_price: "101",
        execution_price: "101",
        notional: "101",
        fee: "0",
        slippage_cost: "0",
        is_engine_protective_stop: false,
      },
      {
        fill_id: "F2",
        order_id: "O3",
        trade_id: "1".repeat(64),
        trade_sequence: 1,
        role: "EXIT",
        event_time: "2026-01-01T00:01:30Z",
        candle_index: 1,
        side: "SELL",
        order_type: "MARKET",
        time_in_force: "GTC",
        client_tag: "exit",
        fill_reason: "MARKET_OPEN",
        liquidity: "TAKER",
        quantity: "1",
        base_price: "108",
        execution_price: "108",
        notional: "108",
        fee: "0",
        slippage_cost: "0",
        is_engine_protective_stop: false,
      },
    ],
    last_candle_open_time: "2026-01-01T00:01:00Z",
    replayed_at: "2026-01-01T00:02:00Z",
    content_checksum: "0".repeat(64),
  };
}

describe("buildChartMarkers", () => {
  it("ancora entradas, saídas e stop no candle correspondente", () => {
    const result = buildChartMarkers(candles, annotations(), "1".repeat(64));

    expect(result).toHaveLength(3);
    expect(result[0]).toMatchObject({
      time: "2026-01-01T00:01:00Z",
      shape: "arrowUp",
      text: "▶ Entrada #1",
    });
    expect(result[1]).toMatchObject({
      time: "2026-01-01T00:01:00Z",
      shape: "arrowDown",
      text: "▶ Saída #1",
    });
    expect(result[2]).toMatchObject({
      time: "2026-01-01T00:01:00Z",
      shape: "square",
      price: 95,
    });
  });

  it("ignora eventos fora dos candles carregados", () => {
    const value = annotations();
    value.fills[0].event_time = "2025-12-31T23:59:00Z";
    value.fills[1].event_time = "2026-01-01T00:03:00Z";

    expect(buildChartMarkers(candles, value, null)).toHaveLength(1);
  });
});
