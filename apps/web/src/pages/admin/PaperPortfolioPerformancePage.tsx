import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, useSearchParams } from "react-router-dom";
import { PortfolioPerformanceCharts } from "../../components/PortfolioPerformanceCharts";
import { EmptyState, InlineError, LoadingState } from "../../components/States";
import { apiClient } from "../../http/client";
import type {
  PaperDashboardSession,
  PaperPortfolioTimelinePageResponse,
} from "../../types/api";
import {
  summarizePortfolioPerformance,
  type PortfolioPerformanceSummary,
} from "../../utils/portfolioPerformance";
import { getErrorMessage } from "../../utils/format";

const TIMELINE_LIMIT = 5_000;
const SESSION_ID = /^[0-9a-f]{64}$/;
const ACCESSIBLE_POINTS = 20;

function initialSession(params: URLSearchParams, key: string): string {
  const value = params.get(key)?.trim() ?? "";
  return SESSION_ID.test(value) ? value : "";
}

function sessionOptionLabel(session: PaperDashboardSession): string {
  return `${session.symbol} · ${session.timeframe} · ${session.strategy_name}@${session.strategy_version} · ${session.session_id.slice(0, 12)}`;
}

function timelineLabel(series: PaperPortfolioTimelinePageResponse): string {
  return `${series.symbol} · ${series.timeframe} · ${series.session_id.slice(0, 12)}`;
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

function money(value: string, quoteAsset: string): string {
  return `${quoteAsset} ${formatExactDecimal(value)}`;
}

function percentage(value: string): string {
  return `${formatExactDecimal(value)}%`;
}

function utcDateTime(value: string): string {
  return new Intl.DateTimeFormat("pt-BR", {
    timeZone: "UTC",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function metricTone(value: string): string {
  return value.startsWith("-") ? "value-negative" : "value-positive";
}

function SummaryCards({
  series,
  summary,
}: {
  series: PaperPortfolioTimelinePageResponse;
  summary: PortfolioPerformanceSummary;
}) {
  const latest = summary.latest;
  return (
    <section
      className="metrics-grid portfolio-performance-metrics"
      aria-label="Resumo da timeline carregada"
    >
      <article className="metric-card metric-card--primary">
        <span className="metric-label">Equity</span>
        <strong>{money(latest.equity, series.quote_asset)}</strong>
        <small>
          Capital inicial {money(series.initial_capital, series.quote_asset)}
        </small>
      </article>

      <article className="metric-card">
        <span className="metric-label">PnL realizado</span>
        <strong className={metricTone(latest.realized_pnl)}>
          {money(latest.realized_pnl, series.quote_asset)}
        </strong>
        <small>Acumulado até o último candle carregado</small>
      </article>

      <article className="metric-card">
        <span className="metric-label">PnL não realizado</span>
        <strong className={metricTone(latest.unrealized_pnl)}>
          {money(latest.unrealized_pnl, series.quote_asset)}
        </strong>
        <small>Mark-to-market no fechamento do candle</small>
      </article>

      <article className="metric-card">
        <span className="metric-label">Drawdown atual</span>
        <strong>{percentage(latest.drawdown_pct)}</strong>
        <small>
          Maior no recorte {percentage(summary.maxDrawdownPct)} ·{" "}
          {utcDateTime(summary.maxDrawdownTime)} UTC
        </small>
      </article>

      <article className="metric-card">
        <span className="metric-label">Taxas acumuladas</span>
        <strong>{money(latest.total_fees, series.quote_asset)}</strong>
        <small>Persistidas pela contabilidade determinística</small>
      </article>

      <article className="metric-card">
        <span className="metric-label">Slippage acumulado</span>
        <strong>{money(latest.total_slippage_cost, series.quote_asset)}</strong>
        <small>Custo acumulado de execução simulada</small>
      </article>
    </section>
  );
}

function ComparisonTable({
  primary,
  comparison,
}: {
  primary: PaperPortfolioTimelinePageResponse;
  comparison: PaperPortfolioTimelinePageResponse;
}) {
  const primarySummary = summarizePortfolioPerformance(primary.items);
  const comparisonSummary = summarizePortfolioPerformance(comparison.items);
  if (!primarySummary || !comparisonSummary) return null;

  const rows = [
    [
      "Equity atual",
      money(primarySummary.latest.equity, primary.quote_asset),
      money(comparisonSummary.latest.equity, comparison.quote_asset),
    ],
    [
      "PnL realizado",
      money(primarySummary.latest.realized_pnl, primary.quote_asset),
      money(comparisonSummary.latest.realized_pnl, comparison.quote_asset),
    ],
    [
      "PnL não realizado",
      money(primarySummary.latest.unrealized_pnl, primary.quote_asset),
      money(comparisonSummary.latest.unrealized_pnl, comparison.quote_asset),
    ],
    [
      "Drawdown atual",
      percentage(primarySummary.latest.drawdown_pct),
      percentage(comparisonSummary.latest.drawdown_pct),
    ],
    [
      "Maior drawdown no recorte",
      percentage(primarySummary.maxDrawdownPct),
      percentage(comparisonSummary.maxDrawdownPct),
    ],
    [
      "Pontos carregados",
      `${primary.count}/${primary.total_observations}`,
      `${comparison.count}/${comparison.total_observations}`,
    ],
  ] as const;

  return (
    <section
      className="portfolio-performance-comparison"
      aria-labelledby="portfolio-performance-comparison-title"
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">Comparação bounded</p>
          <h2 id="portfolio-performance-comparison-title">
            Sessões lado a lado
          </h2>
        </div>
        <span>Máximo 2 sessões · {TIMELINE_LIMIT} pontos por sessão</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Métrica</th>
              <th>{timelineLabel(primary)}</th>
              <th>{timelineLabel(comparison)}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, left, right]) => (
              <tr key={label}>
                <td>
                  <strong>{label}</strong>
                </td>
                <td>{left}</td>
                <td>{right}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function PaperPortfolioPerformancePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initial = useMemo(() => initialSession(searchParams, "session_id"), []);
  const initialComparison = useMemo(
    () => initialSession(searchParams, "compare_session_id"),
    [],
  );
  const [draftSessionId, setDraftSessionId] = useState(initial);
  const [sessionId, setSessionId] = useState(initial);
  const [comparisonSessionId, setComparisonSessionId] =
    useState(initialComparison);
  const [sessions, setSessions] = useState<PaperDashboardSession[]>([]);
  const [series, setSeries] =
    useState<PaperPortfolioTimelinePageResponse | null>(null);
  const [comparisonSeries, setComparisonSeries] =
    useState<PaperPortfolioTimelinePageResponse | null>(null);
  const [sessionOptionsError, setSessionOptionsError] = useState<string | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [comparisonError, setComparisonError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(initial));
  const [refreshing, setRefreshing] = useState(false);
  const [comparisonLoading, setComparisonLoading] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const requestSequence = useRef(0);
  const comparisonSequence = useRef(0);

  useEffect(() => {
    let active = true;
    void apiClient
      .getPaperTradingDashboard(1, 100)
      .then((result) => {
        if (!active) return;
        setSessions(result.items);
        setSessionOptionsError(null);
      })
      .catch((nextError) => {
        if (!active) return;
        setSessionOptionsError(
          getErrorMessage(
            nextError,
            "Não foi possível listar as sessões de paper trading.",
          ),
        );
      });
    return () => {
      active = false;
    };
  }, []);

  const load = useCallback(
    async (initialLoad: boolean) => {
      if (!sessionId) {
        setSeries(null);
        setLoading(false);
        setRefreshing(false);
        return;
      }

      const sequence = ++requestSequence.current;
      if (initialLoad) setLoading(true);
      else setRefreshing(true);

      try {
        const result = await apiClient.getPaperPortfolioTimeline(sessionId, {
          limit: TIMELINE_LIMIT,
        });
        if (sequence !== requestSequence.current) return;
        if (result.session_id !== sessionId) {
          throw new Error("A API retornou uma timeline de outra sessão.");
        }
        setSeries(result);
        setError(null);
        setUpdatedAt(new Date());
      } catch (nextError) {
        if (sequence !== requestSequence.current) return;
        setSeries(null);
        setError(
          getErrorMessage(
            nextError,
            "Não foi possível carregar a performance histórica persistida.",
          ),
        );
      } finally {
        if (sequence === requestSequence.current) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [sessionId],
  );

  useEffect(() => {
    void load(true);
    return () => {
      requestSequence.current += 1;
    };
  }, [load]);

  useEffect(() => {
    if (
      !comparisonSessionId ||
      comparisonSessionId === sessionId ||
      !SESSION_ID.test(comparisonSessionId)
    ) {
      comparisonSequence.current += 1;
      setComparisonSeries(null);
      setComparisonError(null);
      setComparisonLoading(false);
      return;
    }

    const sequence = ++comparisonSequence.current;
    setComparisonLoading(true);
    void apiClient
      .getPaperPortfolioTimeline(comparisonSessionId, {
        limit: TIMELINE_LIMIT,
      })
      .then((result) => {
        if (sequence !== comparisonSequence.current) return;
        if (result.session_id !== comparisonSessionId) {
          throw new Error("A API retornou uma timeline comparativa inválida.");
        }
        setComparisonSeries(result);
        setComparisonError(null);
      })
      .catch((nextError) => {
        if (sequence !== comparisonSequence.current) return;
        setComparisonSeries(null);
        setComparisonError(
          getErrorMessage(
            nextError,
            "Não foi possível carregar a sessão comparativa.",
          ),
        );
      })
      .finally(() => {
        if (sequence === comparisonSequence.current) {
          setComparisonLoading(false);
        }
      });

    return () => {
      comparisonSequence.current += 1;
    };
  }, [comparisonSessionId, sessionId]);

  const summary = useMemo(
    () => summarizePortfolioPerformance(series?.items ?? []),
    [series],
  );

  const initializedSessions = useMemo(
    () => sessions.filter((item) => item.state_available),
    [sessions],
  );

  const selectedSessionKnown = initializedSessions.some(
    (item) => item.session_id === draftSessionId,
  );

  const comparisonCompatible =
    series !== null &&
    comparisonSeries !== null &&
    series.quote_asset === comparisonSeries.quote_asset;

  function applySession(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const normalized = draftSessionId.trim();
    if (!SESSION_ID.test(normalized)) {
      setError("Informe um session ID SHA-256 minúsculo de 64 caracteres.");
      return;
    }
    const params = new URLSearchParams();
    params.set("session_id", normalized);
    if (
      comparisonSessionId &&
      comparisonSessionId !== normalized &&
      SESSION_ID.test(comparisonSessionId)
    ) {
      params.set("compare_session_id", comparisonSessionId);
    }
    setSearchParams(params);
    setSessionId(normalized);
  }

  function changeComparison(value: string): void {
    const normalized = value.trim();
    setComparisonSessionId(normalized);
    const params = new URLSearchParams();
    if (sessionId) params.set("session_id", sessionId);
    if (normalized && normalized !== sessionId) {
      params.set("compare_session_id", normalized);
    }
    setSearchParams(params);
  }

  const recentPoints = series?.items.slice(-ACCESSIBLE_POINTS).reverse() ?? [];

  return (
    <>
      <div className="page-heading portfolio-performance-heading">
        <div>
          <p className="eyebrow">Paper trading · mark-to-market persistido</p>
          <h1>Performance histórica</h1>
          <p>
            Equity, PnL, custos e drawdown derivados exclusivamente da timeline
            determinística publicada pelo replay.
          </p>
        </div>
        <div className="portfolio-performance-actions">
          <small>
            {updatedAt
              ? `Atualizado às ${updatedAt.toLocaleTimeString("pt-BR")}`
              : "Aguardando seleção"}
          </small>
          <button
            className="button button--ghost"
            type="button"
            disabled={!sessionId || refreshing}
            onClick={() => void load(false)}
          >
            {refreshing ? "Atualizando…" : "Atualizar"}
          </button>
        </div>
      </div>

      <aside className="portfolio-performance-note">
        <strong>Fonte autoritativa</strong>
        <span>
          A página não executa estratégia, não consulta exchange e não
          reconstrói contabilidade no navegador. O backend projeta apenas o
          artefato Parquet content-addressed ligado ao estado atual.
        </span>
      </aside>

      <form
        className="panel portfolio-performance-selector"
        aria-label="Selecionar sessão para performance histórica"
        onSubmit={applySession}
      >
        <label>
          Sessão executada
          <select
            value={selectedSessionKnown ? draftSessionId : ""}
            onChange={(event) => setDraftSessionId(event.target.value)}
          >
            <option value="">Selecione uma sessão</option>
            {initializedSessions.map((session) => (
              <option key={session.session_id} value={session.session_id}>
                {sessionOptionLabel(session)}
              </option>
            ))}
          </select>
        </label>

        <label>
          Session ID
          <input
            value={draftSessionId}
            maxLength={64}
            placeholder="SHA-256"
            onChange={(event) => setDraftSessionId(event.target.value.trim())}
          />
        </label>

        <button className="button" type="submit">
          Abrir performance
        </button>
      </form>

      {sessionId && (
        <section
          className="panel portfolio-performance-compare-selector"
          aria-label="Selecionar sessão comparativa"
        >
          <label>
            Comparar com
            <select
              value={comparisonSessionId}
              onChange={(event) => changeComparison(event.target.value)}
            >
              <option value="">Sem comparação</option>
              {initializedSessions
                .filter((session) => session.session_id !== sessionId)
                .map((session) => (
                  <option key={session.session_id} value={session.session_id}>
                    {sessionOptionLabel(session)}
                  </option>
                ))}
            </select>
          </label>
          <span>
            {comparisonLoading
              ? "Carregando comparação…"
              : "No máximo duas timelines, 5.000 pontos por sessão."}
          </span>
        </section>
      )}

      {sessionOptionsError && <InlineError message={sessionOptionsError} />}
      {error && <InlineError message={error} />}
      {comparisonError && <InlineError message={comparisonError} />}

      {comparisonSeries && series && !comparisonCompatible && (
        <aside className="portfolio-performance-limit" role="status">
          Comparação visual de equity bloqueada: {series.quote_asset} e{" "}
          {comparisonSeries.quote_asset} são moedas de cotação diferentes.
          Nenhuma conversão cambial é inventada no frontend.
        </aside>
      )}

      {!sessionId ? (
        <EmptyState
          title="Selecione uma sessão"
          description="Escolha uma sessão executada para abrir sua timeline histórica persistida."
        />
      ) : loading ? (
        <LoadingState message="Carregando timeline persistida…" />
      ) : series && summary ? (
        <>
          <section
            className="instrument-chart-meta portfolio-performance-meta"
            aria-label="Integridade e cobertura da timeline"
          >
            <div>
              <span>Instrumento</span>
              <strong>{series.symbol}</strong>
            </div>
            <div>
              <span>Timeframe</span>
              <strong>{series.timeframe} · UTC</strong>
            </div>
            <div>
              <span>Pontos carregados</span>
              <strong>
                {series.count}/{series.total_observations}
              </strong>
            </div>
            <div>
              <span>Timeline</span>
              <code>{series.timeline_id.slice(0, 16)}</code>
            </div>
          </section>

          {series.has_more_before && (
            <aside className="portfolio-performance-limit" role="status">
              Exibindo os {series.count} pontos mais recentes de{" "}
              {series.total_observations}. O recorte visual permanece limitado a{" "}
              {TIMELINE_LIMIT} observações.
            </aside>
          )}

          <SummaryCards series={series} summary={summary} />

          <PortfolioPerformanceCharts
            observations={series.items}
            quoteAsset={series.quote_asset}
            primaryLabel={timelineLabel(series)}
            comparison={
              comparisonCompatible && comparisonSeries
                ? {
                    label: timelineLabel(comparisonSeries),
                    observations: comparisonSeries.items,
                  }
                : null
            }
          />

          {comparisonCompatible && comparisonSeries && (
            <ComparisonTable primary={series} comparison={comparisonSeries} />
          )}

          <section
            className="portfolio-performance-accessible"
            aria-labelledby="portfolio-performance-points-title"
          >
            <div className="section-heading">
              <div>
                <p className="eyebrow">Leitura textual</p>
                <h2 id="portfolio-performance-points-title">
                  Últimos pontos persistidos
                </h2>
              </div>
              <span>Até {ACCESSIBLE_POINTS} pontos · UTC</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Candle</th>
                    <th>Equity</th>
                    <th>Realizado</th>
                    <th>Não realizado</th>
                    <th>Drawdown</th>
                    <th>Taxas</th>
                    <th>Slippage</th>
                  </tr>
                </thead>
                <tbody>
                  {recentPoints.map((point) => (
                    <tr key={point.candle_index}>
                      <td>{utcDateTime(point.candle_open_time)} UTC</td>
                      <td>{money(point.equity, series.quote_asset)}</td>
                      <td>{money(point.realized_pnl, series.quote_asset)}</td>
                      <td>{money(point.unrealized_pnl, series.quote_asset)}</td>
                      <td>{percentage(point.drawdown_pct)}</td>
                      <td>{money(point.total_fees, series.quote_asset)}</td>
                      <td>
                        {money(point.total_slippage_cost, series.quote_asset)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <div className="portfolio-performance-links">
            <Link
              className="button button--ghost"
              to={`/admin/paper-trading/chart?session_id=${encodeURIComponent(series.session_id)}&base=${encodeURIComponent(series.base_asset)}&quote=${encodeURIComponent(series.quote_asset)}&timeframe=${encodeURIComponent(series.timeframe)}`}
            >
              Abrir gráfico de mercado
            </Link>
            <Link
              className="button button--ghost"
              to={`/admin/paper-trading/journal?session_id=${encodeURIComponent(series.session_id)}`}
            >
              Abrir trade journal
            </Link>
          </div>

          <details className="panel period-metrics-provenance">
            <summary>Proveniência e integridade</summary>
            <dl>
              <div>
                <dt>State checksum</dt>
                <dd>
                  <code>{series.state_checksum}</code>
                </dd>
              </div>
              <div>
                <dt>Timeline ID</dt>
                <dd>
                  <code>{series.timeline_id}</code>
                </dd>
              </div>
              <div>
                <dt>Timeline checksum</dt>
                <dd>
                  <code>{series.timeline_content_checksum}</code>
                </dd>
              </div>
              <div>
                <dt>Page checksum</dt>
                <dd>
                  <code>{series.content_checksum}</code>
                </dd>
              </div>
            </dl>
          </details>
        </>
      ) : (
        <EmptyState
          title="Timeline indisponível"
          description="A sessão não possui pontos históricos disponíveis para este estado."
        />
      )}
    </>
  );
}
