import { useEffect, useMemo, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type SeriesMarkerBar,
  type SeriesMarkerPrice,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { MarketCandle } from "../types/api";
import { calculateChartEma } from "../utils/chartEma";
import {
  buildChartMarkers,
  type ChartAnnotationData,
} from "../utils/chartMarkers";

interface FinancialCandlestickChartProps {
  candles: readonly MarketCandle[];
  fastPeriod: number | null;
  slowPeriod: number | null;
  annotations: ChartAnnotationData | null;
  selectedTradeId: string | null;
  resetKey: number;
}

function utcTimestamp(value: string): UTCTimestamp {
  const milliseconds = Date.parse(value);
  if (!Number.isFinite(milliseconds)) {
    throw new Error("O candle contém timestamp inválido.");
  }
  return Math.floor(milliseconds / 1000) as UTCTimestamp;
}

function chartNumber(value: string): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    throw new Error("O candle contém valor incompatível com o gráfico.");
  }
  return parsed;
}

export function FinancialCandlestickChart({
  candles,
  fastPeriod,
  slowPeriod,
  annotations,
  selectedTradeId,
  resetKey,
}: FinancialCandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const fastSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const slowSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const markerPluginRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const fittedRef = useRef(false);
  const resetKeyRef = useRef(resetKey);

  const chartCandles = useMemo(
    () =>
      candles.map((candle) => ({
        time: utcTimestamp(candle.open_time),
        open: chartNumber(candle.open),
        high: chartNumber(candle.high),
        low: chartNumber(candle.low),
        close: chartNumber(candle.close),
      })),
    [candles],
  );

  const fastEma = useMemo(
    () =>
      fastPeriod === null
        ? []
        : calculateChartEma(candles, fastPeriod).map((point) => ({
            time: utcTimestamp(point.time),
            value: point.value,
          })),
    [candles, fastPeriod],
  );

  const slowEma = useMemo(
    () =>
      slowPeriod === null
        ? []
        : calculateChartEma(candles, slowPeriod).map((point) => ({
            time: utcTimestamp(point.time),
            value: point.value,
          })),
    [candles, slowPeriod],
  );

  const markers = useMemo<SeriesMarker<Time>[]>(
    () =>
      buildChartMarkers(candles, annotations, selectedTradeId).map(
        (marker): SeriesMarker<Time> => {
          const base = {
            time: utcTimestamp(marker.time),
            shape: marker.shape,
            color: marker.color,
            text: marker.text,
          };
          if (marker.kind === "price") {
            return {
              ...base,
              position: marker.position,
              price: marker.price,
            } satisfies SeriesMarkerPrice<Time>;
          }
          return {
            ...base,
            position: marker.position,
          } satisfies SeriesMarkerBar<Time>;
        },
      ),
    [annotations, candles, selectedTradeId],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      autoSize: true,
      layout: {
        attributionLogo: true,
        background: { type: ColorType.Solid, color: "#090d0b" },
        textColor: "#aebbb1",
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.04)" },
        horzLines: { color: "rgba(255,255,255,0.04)" },
      },
      rightPriceScale: {
        borderColor: "rgba(255,255,255,0.12)",
      },
      timeScale: {
        borderColor: "rgba(255,255,255,0.12)",
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        vertLine: { color: "rgba(103,242,138,0.35)" },
        horzLine: { color: "rgba(103,242,138,0.35)" },
      },
    });
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#67f28a",
      downColor: "#ff716b",
      borderVisible: false,
      wickUpColor: "#67f28a",
      wickDownColor: "#ff716b",
    });
    const fastSeries = chart.addSeries(LineSeries, {
      color: "#67f28a",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const slowSeries = chart.addSeries(LineSeries, {
      color: "#f5b84b",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    fastSeriesRef.current = fastSeries;
    slowSeriesRef.current = slowSeries;
    markerPluginRef.current = createSeriesMarkers(candleSeries, []);

    return () => {
      markerPluginRef.current = null;
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      fastSeriesRef.current = null;
      slowSeriesRef.current = null;
      fittedRef.current = false;
    };
  }, []);

  useEffect(() => {
    candleSeriesRef.current?.setData(chartCandles);
    fastSeriesRef.current?.setData(fastEma);
    slowSeriesRef.current?.setData(slowEma);
    markerPluginRef.current?.setMarkers(markers);

    const shouldFit = !fittedRef.current || resetKeyRef.current !== resetKey;
    if (shouldFit && chartCandles.length > 0) {
      chartRef.current?.timeScale().fitContent();
      fittedRef.current = true;
      resetKeyRef.current = resetKey;
    }
  }, [chartCandles, fastEma, markers, resetKey, slowEma]);

  const hasEma = fastPeriod !== null && slowPeriod !== null;

  return (
    <section
      className="financial-chart-shell"
      aria-label="Gráfico financeiro interativo"
    >
      <div className="financial-chart-toolbar">
        <strong>UTC · candles fechados</strong>
        <div
          className="financial-chart-toolbar__legend"
          aria-label="Legenda do gráfico"
        >
          {hasEma ? (
            <>
              <span className="financial-chart-legend financial-chart-legend--fast">
                EMA {fastPeriod}
              </span>
              <span className="financial-chart-legend financial-chart-legend--slow">
                EMA {slowPeriod}
              </span>
            </>
          ) : (
            <span>Sem overlays</span>
          )}
          {annotations && (
            <span>
              {annotations.fills_count} fills ·{" "}
              {
                annotations.orders.filter(
                  (order) => order.is_engine_protective_stop,
                ).length
              }{" "}
              stops
            </span>
          )}
        </div>
      </div>
      <div ref={containerRef} className="financial-chart" />
    </section>
  );
}
