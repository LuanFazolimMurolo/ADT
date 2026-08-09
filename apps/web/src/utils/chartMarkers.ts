import type { MarketCandle } from "../types/api";

export interface ChartAnnotationData {
  fills_count: number;
  fills: readonly {
    fill_id: string;
    trade_id: string;
    trade_sequence: number;
    role: "ENTRY" | "EXIT";
    event_time: string;
  }[];
  orders: readonly {
    order_id: string;
    created_at: string;
    status: string;
    stop_price: string | null;
    is_engine_protective_stop: boolean;
  }[];
}

export type ChartMarkerBarPosition = "aboveBar" | "belowBar" | "inBar";

export type ChartMarkerPricePosition =
  "atPriceTop" | "atPriceBottom" | "atPriceMiddle";

export type ChartMarkerShape = "circle" | "square" | "arrowUp" | "arrowDown";

interface ChartAnnotationMarkerBase {
  sourceId: string;
  tradeId: string | null;
  time: string;
  shape: ChartMarkerShape;
  text: string;
  color: string;
}

export interface ChartBarAnnotationMarker extends ChartAnnotationMarkerBase {
  kind: "bar";
  position: ChartMarkerBarPosition;
}

export interface ChartPriceAnnotationMarker extends ChartAnnotationMarkerBase {
  kind: "price";
  position: ChartMarkerPricePosition;
  price: number;
}

export type ChartAnnotationMarker =
  ChartBarAnnotationMarker | ChartPriceAnnotationMarker;

function finiteNumber(value: string): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    throw new Error("Uma anotação contém preço incompatível com o gráfico.");
  }
  return parsed;
}

function containingCandleOpenTime(
  candles: readonly MarketCandle[],
  eventTime: string,
): string | null {
  const eventMilliseconds = Date.parse(eventTime);
  if (!Number.isFinite(eventMilliseconds) || candles.length === 0) return null;

  let left = 0;
  let right = candles.length - 1;
  let candidate = -1;

  while (left <= right) {
    const middle = Math.floor((left + right) / 2);
    const openMilliseconds = Date.parse(candles[middle].open_time);
    if (!Number.isFinite(openMilliseconds)) {
      throw new Error("Um candle contém timestamp inválido.");
    }
    if (openMilliseconds <= eventMilliseconds) {
      candidate = middle;
      left = middle + 1;
    } else {
      right = middle - 1;
    }
  }

  if (candidate < 0) return null;
  const candle = candles[candidate];
  const closeMilliseconds = Date.parse(candle.close_time);
  if (
    !Number.isFinite(closeMilliseconds) ||
    eventMilliseconds > closeMilliseconds
  ) {
    return null;
  }
  return candle.open_time;
}

export function buildChartMarkers(
  candles: readonly MarketCandle[],
  annotations: ChartAnnotationData | null,
  selectedTradeId: string | null,
): ChartAnnotationMarker[] {
  if (!annotations) return [];

  const markers: ChartAnnotationMarker[] = [];

  for (const fill of annotations.fills) {
    const time = containingCandleOpenTime(candles, fill.event_time);
    if (!time) continue;
    const selected = selectedTradeId === fill.trade_id;
    const entry = fill.role === "ENTRY";
    markers.push({
      kind: "bar",
      sourceId: fill.fill_id,
      tradeId: fill.trade_id,
      time,
      position: entry ? "belowBar" : "aboveBar",
      shape: entry ? "arrowUp" : "arrowDown",
      text: `${selected ? "▶ " : ""}${entry ? "Entrada" : "Saída"} #${fill.trade_sequence}`,
      color: selected ? "#f5f7f5" : entry ? "#67f28a" : "#ff716b",
    });
  }

  for (const order of annotations.orders) {
    if (!order.is_engine_protective_stop || order.stop_price === null) continue;
    const time = containingCandleOpenTime(candles, order.created_at);
    if (!time) continue;
    markers.push({
      kind: "price",
      sourceId: order.order_id,
      tradeId: null,
      time,
      position: "atPriceBottom",
      shape: "square",
      text: `Stop ${order.status}`,
      color: "#f5b84b",
      price: finiteNumber(order.stop_price),
    });
  }

  const priority = (marker: ChartAnnotationMarker): number => {
    if (marker.shape === "arrowUp") return 0;
    if (marker.shape === "arrowDown") return 1;
    return 2;
  };

  return markers.sort((left, right) => {
    const timeOrder = left.time.localeCompare(right.time);
    if (timeOrder !== 0) return timeOrder;
    const priorityOrder = priority(left) - priority(right);
    if (priorityOrder !== 0) return priorityOrder;
    return left.sourceId.localeCompare(right.sourceId);
  });
}
