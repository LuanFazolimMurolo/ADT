import { useEffect, useMemo, useRef } from "react";
import {
  ColorType,
  createChart,
  HistogramSeries,
  LineSeries,
  type HistogramData,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { PaperPeriodGranularity } from "../types/api";
import {
  buildPeriodHeatmap,
  buildPeriodPerformanceProjection,
  distributionPercentage,
  PERIOD_HEATMAP_MAX_BUCKETS,
  type PeriodChartPoint,
  type PeriodPerformanceSeriesData,
} from "../utils/periodPerformance";

interface PeriodPerformanceChartsProps {
  series: PeriodPerformanceSeriesData;
  quoteAsset: string;
}

interface LineDefinition {
  label: string;
  points: PeriodChartPoint[];
  color: string;
}

const granularityLabels: Record<PaperPeriodGranularity, string> = {
  DAILY: "diária",
  WEEKLY: "semanal",
  MONTHLY: "mensal",
};

function utcTimestamp(value: string): UTCTimestamp {
  const milliseconds = Date.parse(value);
  if (!Number.isFinite(milliseconds)) {
    throw new Error("A série por período contém timestamp inválido.");
  }
  return Math.floor(milliseconds / 1000) as UTCTimestamp;
}

function periodCellLabel(
  start: string,
  value: string,
  realizations: number,
): string {
  const label = new Intl.DateTimeFormat("pt-BR", {
    timeZone: "UTC",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(new Date(start));
  return `${label} UTC · PnL realizado ${value} · ${realizations} realizações`;
}

function baseChart(container: HTMLDivElement): IChartApi {
  return createChart(container, {
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
      timeVisible: false,
      secondsVisible: false,
    },
    crosshair: {
      vertLine: { color: "rgba(103,242,138,0.35)" },
      horzLine: { color: "rgba(103,242,138,0.35)" },
    },
  });
}

function RealizedPeriodChart({
  series,
  quoteAsset,
}: {
  series: PeriodPerformanceSeriesData;
  quoteAsset: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const projection = useMemo(
    () => buildPeriodPerformanceProjection(series),
    [series],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = baseChart(container);
    const histogram = chart.addSeries(HistogramSeries, {
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });
    const values: HistogramData<UTCTimestamp>[] = projection.realizedPnl.map(
      (point) => ({
        time: utcTimestamp(point.time),
        value: point.value,
        color: point.value < 0 ? "#ff716b" : "#67f28a",
      }),
    );
    histogram.setData(values);
    chart.timeScale().fitContent();

    return () => chart.remove();
  }, [projection.realizedPnl]);

  return (
    <section className="period-performance-chart">
      <div className="period-performance-chart__heading">
        <div>
          <h3>PnL realizado por período</h3>
          <p>
            Série {granularityLabels[series.granularity]} · {quoteAsset}
          </p>
        </div>
        <span className="period-performance-badge">realized-only</span>
      </div>
      <div
        ref={containerRef}
        className="period-performance-chart__canvas"
        aria-label="PnL realizado por período"
      />
    </section>
  );
}

function MultiLineChart({
  title,
  subtitle,
  definitions,
}: {
  title: string;
  subtitle: string;
  definitions: readonly LineDefinition[];
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = baseChart(container);
    for (const definition of definitions) {
      const line = chart.addSeries(LineSeries, {
        color: definition.color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      line.setData(
        definition.points.map((point) => ({
          time: utcTimestamp(point.time),
          value: point.value,
        })),
      );
    }
    if (definitions.some((definition) => definition.points.length > 0)) {
      chart.timeScale().fitContent();
    }

    return () => chart.remove();
  }, [definitions]);

  return (
    <section className="period-performance-chart">
      <div className="period-performance-chart__heading">
        <div>
          <h3>{title}</h3>
          <p>{subtitle}</p>
        </div>
        <div
          className="period-performance-chart__legend"
          aria-label={`Legenda: ${title}`}
        >
          {definitions.map((definition) => (
            <span key={definition.label}>
              <i style={{ backgroundColor: definition.color }} />
              {definition.label}
            </span>
          ))}
        </div>
      </div>
      <div
        ref={containerRef}
        className="period-performance-chart__canvas"
        aria-label={title}
      />
    </section>
  );
}

function PeriodHeatmap({ series }: { series: PeriodPerformanceSeriesData }) {
  const heatmap = useMemo(() => buildPeriodHeatmap(series), [series]);

  return (
    <section
      className="period-performance-chart period-performance-heatmap"
      aria-labelledby="period-performance-heatmap-title"
    >
      <div className="period-performance-chart__heading">
        <div>
          <h3 id="period-performance-heatmap-title">Heatmap realized-only</h3>
          <p>
            Intensidade visual relativa ao maior |PnL| do recorte visível; os
            valores exatos permanecem no tooltip acessível e na tabela.
          </p>
        </div>
        <span className="period-performance-badge">
          {heatmap.visibleBuckets}/{heatmap.totalBuckets}
        </span>
      </div>

      {heatmap.truncated && (
        <p className="period-performance-heatmap__limit" role="status">
          Heatmap limitado aos {PERIOD_HEATMAP_MAX_BUCKETS} buckets mais
          recentes, sem agregação ou downsampling silencioso.
        </p>
      )}

      <div
        className="period-performance-heatmap__grid"
        aria-label={`Heatmap de PnL realizado: ${heatmap.visibleBuckets} períodos`}
      >
        {heatmap.cells.map((cell) => (
          <span
            key={cell.periodStart}
            role="img"
            aria-label={periodCellLabel(
              cell.periodStart,
              cell.realizedPnl,
              cell.realizationsCount,
            )}
            title={periodCellLabel(
              cell.periodStart,
              cell.realizedPnl,
              cell.realizationsCount,
            )}
            className={`period-performance-heatmap__cell period-performance-heatmap__cell--${cell.sign}`}
            style={{
              opacity: cell.sign === "zero" ? 0.22 : 0.3 + cell.intensity * 0.7,
            }}
          />
        ))}
      </div>
    </section>
  );
}

export function PeriodPerformanceCharts({
  series,
  quoteAsset,
}: PeriodPerformanceChartsProps) {
  const projection = useMemo(
    () => buildPeriodPerformanceProjection(series),
    [series],
  );

  const costDefinitions = useMemo<LineDefinition[]>(
    () => [
      {
        label: "Taxas realizadas",
        points: projection.fees,
        color: "#7aa7ff",
      },
      {
        label: "Slippage realizado",
        points: projection.slippage,
        color: "#cf8cff",
      },
    ],
    [projection.fees, projection.slippage],
  );

  const factorDefinitions = useMemo<LineDefinition[]>(
    () => [
      {
        label: "Profit factor",
        points: projection.profitFactor,
        color: "#f5b84b",
      },
      {
        label: "Saídas",
        points: projection.realizations,
        color: "#67f28a",
      },
    ],
    [projection.profitFactor, projection.realizations],
  );

  const outcomes = projection.outcomes;
  const outcomeRows = [
    {
      label: "Vitórias",
      value: outcomes.wins,
      className: "period-outcome--win",
    },
    {
      label: "Derrotas",
      value: outcomes.losses,
      className: "period-outcome--loss",
    },
    {
      label: "Breakeven",
      value: outcomes.breakeven,
      className: "period-outcome--flat",
    },
  ];

  return (
    <section
      className="period-performance-visuals"
      aria-labelledby="period-performance-visuals-title"
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">Visualizações realizadas</p>
          <h2 id="period-performance-visuals-title">
            Performance contábil por período
          </h2>
        </div>
        <span>
          {series.granularity} · {quoteAsset}
        </span>
      </div>

      <aside className="period-performance-scope">
        <strong>Realized-only</strong>
        <span>
          Estes gráficos representam somente eventos de realização atribuídos ao
          calendário UTC. Não são uma curva de equity, PnL não realizado ou
          drawdown histórico.
        </span>
      </aside>

      <div className="period-performance-grid">
        <RealizedPeriodChart series={series} quoteAsset={quoteAsset} />
        <MultiLineChart
          title="Custos realizados por período"
          subtitle={`Taxas e slippage atribuídos às saídas em ${quoteAsset}.`}
          definitions={costDefinitions}
        />
        <MultiLineChart
          title="Profit factor e atividade"
          subtitle="Profit factor aparece apenas quando definido; saídas mostram a atividade do bucket."
          definitions={factorDefinitions}
        />

        <section
          className="period-performance-chart period-outcome-distribution"
          aria-label="Distribuição de resultados realizados"
        >
          <div className="period-performance-chart__heading">
            <div>
              <h3>Distribuição win/loss</h3>
              <p>{outcomes.total} realizações no recorte carregado.</p>
            </div>
          </div>
          <div className="period-outcome-list">
            {outcomeRows.map((row) => (
              <div key={row.label} className="period-outcome-row">
                <div>
                  <strong>{row.label}</strong>
                  <span>
                    {row.value} ·{" "}
                    {distributionPercentage(row.value, outcomes.total).toFixed(
                      1,
                    )}
                    %
                  </span>
                </div>
                <div className="period-outcome-track" aria-hidden="true">
                  <span
                    className={row.className}
                    style={{
                      width: `${distributionPercentage(
                        row.value,
                        outcomes.total,
                      )}%`,
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        </section>

        <PeriodHeatmap series={series} />
      </div>
    </section>
  );
}
