import { useEffect, useMemo, useRef } from "react";
import {
  ColorType,
  createChart,
  LineSeries,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { PaperPortfolioObservation } from "../types/api";
import {
  buildPortfolioPerformanceSeries,
  type PortfolioChartPoint,
} from "../utils/portfolioPerformance";

interface PortfolioComparisonSeries {
  label: string;
  observations: readonly PaperPortfolioObservation[];
}

interface PortfolioPerformanceChartsProps {
  observations: readonly PaperPortfolioObservation[];
  quoteAsset: string;
  primaryLabel?: string;
  comparison?: PortfolioComparisonSeries | null;
}

interface LineDefinition {
  label: string;
  points: PortfolioChartPoint[];
  color: string;
}

interface PerformanceLineChartProps {
  title: string;
  subtitle: string;
  series: readonly LineDefinition[];
  percentage?: boolean;
}

function utcTimestamp(value: string): UTCTimestamp {
  const milliseconds = Date.parse(value);
  if (!Number.isFinite(milliseconds)) {
    throw new Error("A timeline contém timestamp inválido.");
  }
  return Math.floor(milliseconds / 1000) as UTCTimestamp;
}

function PerformanceLineChart({
  title,
  subtitle,
  series,
  percentage = false,
}: PerformanceLineChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);

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
      localization: percentage
        ? {
            priceFormatter: (value: number) =>
              `${new Intl.NumberFormat("pt-BR", {
                maximumFractionDigits: 4,
              }).format(value)}%`,
          }
        : undefined,
    });

    for (const definition of series) {
      const line = chart.addSeries(LineSeries, {
        color: definition.color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
      });
      line.setData(
        definition.points.map((point) => ({
          time: utcTimestamp(point.time),
          value: point.value,
        })),
      );
    }

    if (series.some((definition) => definition.points.length > 0)) {
      chart.timeScale().fitContent();
    }

    chartRef.current = chart;
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [percentage, series]);

  return (
    <section className="portfolio-performance-chart">
      <div className="portfolio-performance-chart__heading">
        <div>
          <h3>{title}</h3>
          <p>{subtitle}</p>
        </div>
        <div
          className="portfolio-performance-chart__legend"
          aria-label={`Legenda: ${title}`}
        >
          {series.map((definition) => (
            <span key={definition.label}>
              <i style={{ backgroundColor: definition.color }} />
              {definition.label}
            </span>
          ))}
        </div>
      </div>
      <div
        ref={containerRef}
        className="portfolio-performance-chart__canvas"
        aria-label={title}
      />
    </section>
  );
}

export function PortfolioPerformanceCharts({
  observations,
  quoteAsset,
  primaryLabel = "Sessão principal",
  comparison = null,
}: PortfolioPerformanceChartsProps) {
  const data = useMemo(
    () => buildPortfolioPerformanceSeries(observations),
    [observations],
  );
  const comparisonData = useMemo(
    () =>
      comparison
        ? buildPortfolioPerformanceSeries(comparison.observations)
        : null,
    [comparison],
  );

  const equitySeries = useMemo<LineDefinition[]>(
    () => [
      {
        label: `${primaryLabel} · ${quoteAsset}`,
        points: data.equity,
        color: "#67f28a",
      },
      ...(comparisonData && comparison
        ? [
            {
              label: `${comparison.label} · ${quoteAsset}`,
              points: comparisonData.equity,
              color: "#7aa7ff",
            },
          ]
        : []),
    ],
    [comparison, comparisonData, data.equity, primaryLabel, quoteAsset],
  );

  const pnlSeries = useMemo<LineDefinition[]>(
    () => [
      {
        label: "PnL realizado",
        points: data.realizedPnl,
        color: "#67f28a",
      },
      {
        label: "PnL não realizado",
        points: data.unrealizedPnl,
        color: "#f5b84b",
      },
      {
        label: "Taxas acumuladas",
        points: data.fees,
        color: "#7aa7ff",
      },
      {
        label: "Slippage acumulado",
        points: data.slippage,
        color: "#cf8cff",
      },
    ],
    [data.fees, data.realizedPnl, data.slippage, data.unrealizedPnl],
  );

  const drawdownSeries = useMemo<LineDefinition[]>(
    () => [
      {
        label: primaryLabel,
        points: data.drawdownPct,
        color: "#ff716b",
      },
      ...(comparisonData && comparison
        ? [
            {
              label: comparison.label,
              points: comparisonData.drawdownPct,
              color: "#f5b84b",
            },
          ]
        : []),
    ],
    [comparison, comparisonData, data.drawdownPct, primaryLabel],
  );

  return (
    <div className="portfolio-performance-charts">
      <PerformanceLineChart
        title={
          comparison ? "Comparação de equity histórica" : "Equity histórica"
        }
        subtitle="Mark-to-market no fechamento de cada candle persistido; comparação nominal somente na mesma moeda de cotação."
        series={equitySeries}
      />
      <PerformanceLineChart
        title="PnL e custos acumulados"
        subtitle={`Sessão principal em ${quoteAsset}; coordenadas visuais derivadas dos Decimals persistidos.`}
        series={pnlSeries}
      />
      <PerformanceLineChart
        title={
          comparison ? "Comparação de drawdown histórico" : "Drawdown histórico"
        }
        subtitle="Magnitude percentual em relação ao peak equity de cada replay."
        series={drawdownSeries}
        percentage
      />
    </div>
  );
}
