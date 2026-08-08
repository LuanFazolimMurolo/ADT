import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { PeriodPerformanceCharts } from "../../components/PeriodPerformanceCharts";
import { EmptyState, InlineError, LoadingState } from "../../components/States";
import { apiClient, type PaperPeriodMetricsFilters } from "../../http/client";
import type {
  PaperPeriodGranularity,
  PaperPeriodMetricsBucket,
  PaperPeriodMetricsSeriesResponse,
} from "../../types/api";
import { formatMoney, getErrorMessage } from "../../utils/format";

interface PeriodMetricsFilterForm {
  quoteAsset: string;
  periodFrom: string;
  periodBefore: string;
  sessionId: string;
  baseAsset: string;
  timeframe: string;
  strategyName: string;
  strategyVersion: string;
}

const granularityLabels: Record<PaperPeriodGranularity, string> = {
  DAILY: "Diário",
  WEEKLY: "Semanal",
  MONTHLY: "Mensal",
};

function utcInputValue(value: Date): string {
  const year = value.getUTCFullYear();
  const month = String(value.getUTCMonth() + 1).padStart(2, "0");
  const day = String(value.getUTCDate()).padStart(2, "0");
  const hour = String(value.getUTCHours()).padStart(2, "0");
  const minute = String(value.getUTCMinutes()).padStart(2, "0");
  return `${year}-${month}-${day}T${hour}:${minute}`;
}

function initialFilterForm(): PeriodMetricsFilterForm {
  const before = new Date();
  before.setUTCSeconds(0, 0);
  before.setUTCMinutes(0);
  before.setUTCHours(0);
  before.setUTCDate(before.getUTCDate() + 1);

  const from = new Date(before);
  from.setUTCDate(from.getUTCDate() - 30);

  return {
    quoteAsset: "USDT",
    periodFrom: utcInputValue(from),
    periodBefore: utcInputValue(before),
    sessionId: "",
    baseAsset: "",
    timeframe: "",
    strategyName: "",
    strategyVersion: "",
  };
}

const INITIAL_FORM = initialFilterForm();

function utcIso(value: string): string {
  return new Date(`${value}:00Z`).toISOString();
}

function normalizedFilters(
  value: PeriodMetricsFilterForm,
): PaperPeriodMetricsFilters {
  return {
    quoteAsset: value.quoteAsset.trim().toUpperCase(),
    periodFrom: utcIso(value.periodFrom),
    periodBefore: utcIso(value.periodBefore),
    sessionId: value.sessionId.trim() || undefined,
    baseAsset: value.baseAsset.trim() || undefined,
    timeframe: value.timeframe.trim() || undefined,
    strategyName: value.strategyName.trim() || undefined,
    strategyVersion: value.strategyVersion.trim() || undefined,
  };
}

function formatExactDecimal(value: string, minimumFraction = 2): string {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return value;
  const [, sign, rawInteger, rawFraction = ""] = match;
  const integer = rawInteger
    .replace(/^0+(?=\d)/, "")
    .replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const fraction = rawFraction.replace(/0+$/, "").padEnd(minimumFraction, "0");
  return fraction ? `${sign}${integer},${fraction}` : `${sign}${integer}`;
}

function formatPercentage(value: string | null): string {
  return value === null ? "—" : `${formatExactDecimal(value)}%`;
}

function formatRatio(value: string | null): string {
  return value === null ? "—" : formatExactDecimal(value);
}

function isNegative(value: string): boolean {
  return value.startsWith("-");
}

function utcDate(value: string): string {
  return new Intl.DateTimeFormat("pt-BR", {
    timeZone: "UTC",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(new Date(value));
}

function periodLabel(bucket: PaperPeriodMetricsBucket): string {
  const inclusiveEnd = new Date(new Date(bucket.period_end).getTime() - 1);
  return `${utcDate(bucket.period_start)} → ${utcDate(inclusiveEnd.toISOString())}`;
}

export function PaperPeriodMetricsPage() {
  const [form, setForm] = useState<PeriodMetricsFilterForm>(INITIAL_FORM);
  const [granularity, setGranularity] =
    useState<PaperPeriodGranularity>("DAILY");
  const [appliedFilters, setAppliedFilters] =
    useState<PaperPeriodMetricsFilters>(() => normalizedFilters(INITIAL_FORM));
  const [appliedGranularity, setAppliedGranularity] =
    useState<PaperPeriodGranularity>("DAILY");
  const [requestVersion, setRequestVersion] = useState(0);
  const [series, setSeries] = useState<PaperPeriodMetricsSeriesResponse | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestSequence = useRef(0);

  const load = useCallback(
    async (initial: boolean) => {
      const sequence = ++requestSequence.current;
      if (initial) setLoading(true);
      else setRefreshing(true);

      try {
        const result = await apiClient.getPaperPeriodMetrics(
          appliedFilters,
          appliedGranularity,
        );
        if (sequence !== requestSequence.current) return;
        setSeries(result);
        setError(null);
      } catch (nextError) {
        if (sequence !== requestSequence.current) return;
        setError(
          getErrorMessage(
            nextError,
            "Não foi possível carregar as métricas por período.",
          ),
        );
      } finally {
        if (sequence === requestSequence.current) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [appliedFilters, appliedGranularity],
  );

  useEffect(() => {
    void load(series === null);
    // requestVersion intentionally permits an explicit refresh of an unchanged query.
  }, [load, requestVersion]);

  const activeItems = useMemo(
    () => series?.items.filter((item) => item.realizations_count > 0) ?? [],
    [series],
  );

  function updateField(
    field: keyof PeriodMetricsFilterForm,
    value: string,
  ): void {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function applyFilters(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();

    if (!form.quoteAsset.trim()) {
      setError("Informe uma moeda de cotação.");
      return;
    }
    if (!form.periodFrom || !form.periodBefore) {
      setError("Informe o início e o fim do intervalo UTC.");
      return;
    }

    const from = new Date(`${form.periodFrom}:00Z`);
    const before = new Date(`${form.periodBefore}:00Z`);
    if (
      Number.isNaN(from.getTime()) ||
      Number.isNaN(before.getTime()) ||
      from >= before
    ) {
      setError("O intervalo UTC deve ser válido e crescente.");
      return;
    }

    setAppliedFilters(normalizedFilters(form));
    setAppliedGranularity(granularity);
    setRequestVersion((current) => current + 1);
  }

  if (loading && !series) {
    return <LoadingState message="Carregando métricas por período…" />;
  }

  return (
    <>
      <div className="page-heading period-metrics-heading">
        <div>
          <p className="eyebrow">Paper trading · calendário UTC</p>
          <h1>Performance por período</h1>
          <p>
            Realizações contábeis agrupadas por dia, semana ISO ou mês, sem
            misturar moedas de cotação.
          </p>
        </div>
        <button
          className="button button--ghost"
          type="button"
          disabled={refreshing}
          onClick={() => setRequestVersion((current) => current + 1)}
        >
          {refreshing ? "Atualizando…" : "Atualizar"}
        </button>
      </div>

      <aside className="period-metrics-note">
        <strong>Escopo contábil realized-only</strong>
        <span>
          Esta superfície atribui PnL realizado, taxas e slippage ao instante de
          cada saída no calendário UTC. Ela não representa equity, PnL não
          realizado ou drawdown histórico; essas séries mark-to-market pertencem
          à página de Performance histórica da sessão.
        </span>
      </aside>

      <form
        className="panel form-panel period-metrics-filters"
        onSubmit={applyFilters}
        aria-label="Filtros de performance por período"
      >
        <div className="form-grid">
          <label>
            Moeda de cotação
            <input
              value={form.quoteAsset}
              maxLength={32}
              required
              onChange={(event) =>
                updateField("quoteAsset", event.target.value)
              }
            />
          </label>

          <label>
            Granularidade
            <select
              value={granularity}
              onChange={(event) =>
                setGranularity(event.target.value as PaperPeriodGranularity)
              }
            >
              {Object.entries(granularityLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>

          <label>
            Início UTC
            <input
              type="datetime-local"
              step={60}
              value={form.periodFrom}
              required
              onChange={(event) =>
                updateField("periodFrom", event.target.value)
              }
            />
          </label>

          <label>
            Fim exclusivo UTC
            <input
              type="datetime-local"
              step={60}
              value={form.periodBefore}
              required
              onChange={(event) =>
                updateField("periodBefore", event.target.value)
              }
            />
          </label>

          <label>
            Ativo base
            <input
              value={form.baseAsset}
              maxLength={32}
              placeholder="BTC"
              onChange={(event) => updateField("baseAsset", event.target.value)}
            />
          </label>

          <label>
            Timeframe
            <input
              value={form.timeframe}
              maxLength={128}
              placeholder="1m"
              onChange={(event) => updateField("timeframe", event.target.value)}
            />
          </label>

          <label>
            Estratégia
            <input
              value={form.strategyName}
              maxLength={128}
              onChange={(event) =>
                updateField("strategyName", event.target.value)
              }
            />
          </label>

          <label>
            Versão da estratégia
            <input
              value={form.strategyVersion}
              maxLength={128}
              onChange={(event) =>
                updateField("strategyVersion", event.target.value)
              }
            />
          </label>

          <label className="form-grid__wide">
            Session ID
            <input
              value={form.sessionId}
              maxLength={64}
              placeholder="SHA-256 opcional"
              onChange={(event) => updateField("sessionId", event.target.value)}
            />
          </label>
        </div>

        <div className="form-actions">
          <button className="button" type="submit" disabled={refreshing}>
            Aplicar período
          </button>
        </div>
      </form>

      {error && <InlineError message={error} />}

      {series && (
        <>
          <section
            className="metrics-grid period-metrics-totals"
            aria-label="Totais do período"
          >
            <article className="metric-card metric-card--primary">
              <span className="metric-label">PnL realizado</span>
              <strong
                className={
                  isNegative(series.totals.realized_pnl)
                    ? "value-negative"
                    : "value-positive"
                }
              >
                {formatMoney(
                  series.totals.realized_pnl,
                  series.totals.quote_asset,
                )}
              </strong>
              <small>
                {series.totals.active_periods_count} de{" "}
                {series.totals.periods_count} períodos ativos
              </small>
            </article>

            <article className="metric-card">
              <span className="metric-label">Realizações</span>
              <strong>{series.totals.realizations_count}</strong>
              <small>
                {series.totals.winning_realizations_count} positivas ·{" "}
                {series.totals.losing_realizations_count} negativas
              </small>
            </article>

            <article className="metric-card">
              <span className="metric-label">Win rate</span>
              <strong>{formatPercentage(series.totals.win_rate_pct)}</strong>
              <small>
                {series.totals.breakeven_realizations_count} no zero
              </small>
            </article>

            <article className="metric-card">
              <span className="metric-label">Profit factor</span>
              <strong>{formatRatio(series.totals.profit_factor)}</strong>
              <small>{series.totals.sessions_count} sessões verificadas</small>
            </article>
          </section>

          {series.items.length ? (
            <>
              <PeriodPerformanceCharts series={series} />
              <section
                className="period-metrics-series"
                aria-labelledby="period-metrics-series-title"
              >
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">
                      {granularityLabels[series.granularity]}
                    </p>
                    <h2 id="period-metrics-series-title">Série contínua</h2>
                  </div>
                  <span>
                    {activeItems.length} períodos com realizações ·{" "}
                    {series.filters.quote_asset}
                  </span>
                </div>

                <div className="table-wrap">
                  <table className="period-metrics-table">
                    <thead>
                      <tr>
                        <th>Período UTC</th>
                        <th>Saídas</th>
                        <th>Vitórias / derrotas</th>
                        <th>PnL realizado</th>
                        <th>Taxas</th>
                        <th>Lucro bruto</th>
                        <th>Prejuízo bruto</th>
                        <th>Profit factor</th>
                      </tr>
                    </thead>
                    <tbody>
                      {series.items.map((bucket) => (
                        <tr
                          key={bucket.period_start}
                          className={
                            bucket.realizations_count === 0
                              ? "period-metrics-zero"
                              : undefined
                          }
                        >
                          <td>
                            <strong>{periodLabel(bucket)}</strong>
                          </td>
                          <td>{bucket.realizations_count}</td>
                          <td>
                            {bucket.winning_realizations_count} /{" "}
                            {bucket.losing_realizations_count}
                          </td>
                          <td
                            className={
                              isNegative(bucket.realized_pnl)
                                ? "value-negative"
                                : "value-positive"
                            }
                          >
                            {formatMoney(
                              bucket.realized_pnl,
                              bucket.quote_asset,
                            )}
                          </td>
                          <td>
                            {formatMoney(
                              bucket.realized_fees,
                              bucket.quote_asset,
                            )}
                          </td>
                          <td>
                            {formatMoney(
                              bucket.gross_profit,
                              bucket.quote_asset,
                            )}
                          </td>
                          <td>
                            {formatMoney(bucket.gross_loss, bucket.quote_asset)}
                          </td>
                          <td>{formatRatio(bucket.profit_factor)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </>
          ) : (
            <EmptyState
              title="Nenhum período disponível"
              description="O intervalo selecionado não produziu buckets de calendário."
            />
          )}

          <details className="panel period-metrics-provenance">
            <summary>Proveniência e integridade</summary>
            <dl>
              <div>
                <dt>Estados-fonte</dt>
                <dd>{series.source_states.length}</dd>
              </div>
              <div>
                <dt>Query checksum</dt>
                <dd>
                  <code>{series.query_checksum}</code>
                </dd>
              </div>
              <div>
                <dt>Content checksum</dt>
                <dd>
                  <code>{series.content_checksum}</code>
                </dd>
              </div>
            </dl>
          </details>
        </>
      )}
    </>
  );
}
