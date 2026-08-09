import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { PeriodPerformanceCharts } from "../../components/PeriodPerformanceCharts";
import { PortfolioPerformanceCharts } from "../../components/PortfolioPerformanceCharts";
import { EmptyState, InlineError, LoadingState } from "../../components/States";
import { ApiError, apiClient } from "../../http/client";
import type {
  AppPaperPeriodMetricsSeries,
  AppPaperPortfolioTimelinePage,
  AppPaperSessionDetail,
  PaperPeriodGranularity,
} from "../../types/api";

const TIMELINE_PAGE_SIZE = 1_000;
const MAX_OBSERVATIONS = 5_000;
const ACCESSIBLE_OBSERVATIONS = 20;
const ACCESS_DENIED = "Esta conta não possui acesso a esta sessão.";
const NOT_FOUND = "A sessão solicitada não foi encontrada.";
const SAFE_DETAIL_ERROR =
  "Não foi possível carregar a performance desta sessão. Tente novamente.";
const TIMELINE_ERROR =
  "Não foi possível carregar a timeline histórica persistida desta sessão.";
const TIMELINE_MISMATCH =
  "A timeline persistida mudou durante a paginação. Atualize a página antes de continuar.";
const PERIOD_ERROR =
  "Não foi possível carregar a performance realizada por período.";

const granularityLabels: Record<PaperPeriodGranularity, string> = {
  DAILY: "Diária",
  WEEKLY: "Semanal",
  MONTHLY: "Mensal",
};

interface PeriodRange {
  from: string;
  before: string;
}

function formatUtc(value: string): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "Horário UTC indisponível";
  return `${date.toISOString()} UTC`;
}

function abbreviated(value: string): string {
  return `${value.slice(0, 12)}…`;
}

function utcInput(value: Date): string {
  return value.toISOString().slice(0, 16);
}

function inputIso(value: string): string {
  return new Date(`${value}:00Z`).toISOString();
}

function periodFloor(value: Date, granularity: PaperPeriodGranularity): Date {
  const result = new Date(value);
  result.setUTCHours(0, 0, 0, 0);
  if (granularity === "WEEKLY") {
    const weekday = result.getUTCDay();
    result.setUTCDate(result.getUTCDate() - (weekday === 0 ? 6 : weekday - 1));
  } else if (granularity === "MONTHLY") {
    result.setUTCDate(1);
  }
  return result;
}

function nextPeriod(value: Date, granularity: PaperPeriodGranularity): Date {
  const result = new Date(value);
  if (granularity === "DAILY") result.setUTCDate(result.getUTCDate() + 1);
  if (granularity === "WEEKLY") result.setUTCDate(result.getUTCDate() + 7);
  if (granularity === "MONTHLY") result.setUTCMonth(result.getUTCMonth() + 1);
  return result;
}

function alignedRange(
  timeline: AppPaperPortfolioTimelinePage,
  granularity: PaperPeriodGranularity,
): PeriodRange | null {
  const availableStart = new Date(timeline.available_start);
  const availableEnd = new Date(timeline.available_end);
  if (
    !Number.isFinite(availableStart.getTime()) ||
    !Number.isFinite(availableEnd.getTime())
  )
    return null;
  const floorStart = periodFloor(availableStart, granularity);
  const from =
    floorStart.getTime() < availableStart.getTime()
      ? nextPeriod(floorStart, granularity)
      : floorStart;
  const before = periodFloor(availableEnd, granularity);
  if (from >= before) return null;
  return { from: utcInput(from), before: utcInput(before) };
}

function detailError(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) return ACCESS_DENIED;
  if (error instanceof ApiError && error.status === 404) return NOT_FOUND;
  return SAFE_DETAIL_ERROR;
}

function sameTimeline(
  current: AppPaperPortfolioTimelinePage,
  candidate: AppPaperPortfolioTimelinePage,
): boolean {
  return (
    current.session_id === candidate.session_id &&
    current.base_asset === candidate.base_asset &&
    current.quote_asset === candidate.quote_asset &&
    current.timeframe === candidate.timeframe &&
    current.dataset_version === candidate.dataset_version &&
    current.state_checksum === candidate.state_checksum &&
    current.timeline_id === candidate.timeline_id &&
    current.timeline_content_checksum === candidate.timeline_content_checksum &&
    current.initial_capital === candidate.initial_capital &&
    current.available_start === candidate.available_start &&
    current.available_end === candidate.available_end &&
    current.total_observations === candidate.total_observations
  );
}

export function AppPaperSessionPerformancePage() {
  const { sessionId = "" } = useParams();
  const [detail, setDetail] = useState<AppPaperSessionDetail | null>(null);
  const [timeline, setTimeline] =
    useState<AppPaperPortfolioTimelinePage | null>(null);
  const [periodSeries, setPeriodSeries] =
    useState<AppPaperPeriodMetricsSeries | null>(null);
  const [granularity, setGranularity] =
    useState<PaperPeriodGranularity>("DAILY");
  const [periodFrom, setPeriodFrom] = useState("");
  const [periodBefore, setPeriodBefore] = useState("");
  const [loading, setLoading] = useState(true);
  const [olderLoading, setOlderLoading] = useState(false);
  const [periodLoading, setPeriodLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [timelineError, setTimelineError] = useState<string | null>(null);
  const [periodError, setPeriodError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const mainSequence = useRef(0);
  const timelineSequence = useRef(0);
  const periodSequence = useRef(0);

  async function loadPeriod(
    range: PeriodRange,
    nextGranularity: PaperPeriodGranularity,
    expectedQuoteAsset: string,
  ): Promise<void> {
    const sequence = ++periodSequence.current;
    setPeriodLoading(true);
    try {
      const result = await apiClient.getAppPaperPeriodMetrics(sessionId, {
        periodFrom: inputIso(range.from),
        periodBefore: inputIso(range.before),
        granularity: nextGranularity,
      });
      if (sequence !== periodSequence.current) return;
      if (
        result.session_id !== sessionId ||
        result.quote_asset !== expectedQuoteAsset ||
        result.granularity !== nextGranularity
      )
        throw new Error("invalid period session");
      setPeriodSeries(result);
      setPeriodError(null);
    } catch {
      if (sequence !== periodSequence.current) return;
      setPeriodSeries(null);
      setPeriodError(PERIOD_ERROR);
    } finally {
      if (sequence === periodSequence.current) setPeriodLoading(false);
    }
  }

  useEffect(() => {
    const sequence = ++mainSequence.current;
    timelineSequence.current += 1;
    periodSequence.current += 1;
    setLoading(true);
    setDetail(null);
    setTimeline(null);
    setPeriodSeries(null);
    setError(null);
    setTimelineError(null);
    setPeriodError(null);

    void (async () => {
      try {
        const nextDetail = await apiClient.getAppPaperSession(sessionId);
        if (sequence !== mainSequence.current) return;
        setDetail(nextDetail);

        let nextTimeline: AppPaperPortfolioTimelinePage;
        try {
          nextTimeline = await apiClient.getAppPaperPortfolioTimeline(
            sessionId,
            {
              limit: TIMELINE_PAGE_SIZE,
            },
          );
        } catch {
          if (sequence !== mainSequence.current) return;
          setTimelineError(TIMELINE_ERROR);
          return;
        }
        if (sequence !== mainSequence.current) return;
        const identityMismatch =
          nextTimeline.session_id !== nextDetail.session_id ||
          nextTimeline.base_asset !== nextDetail.base_asset ||
          nextTimeline.quote_asset !== nextDetail.quote_asset ||
          nextTimeline.timeframe !== nextDetail.timeframe;
        if (identityMismatch) {
          setTimelineError(TIMELINE_MISMATCH);
          return;
        }
        const initialItems = nextTimeline.items.slice(-MAX_OBSERVATIONS);
        nextTimeline = {
          ...nextTimeline,
          count: initialItems.length,
          has_more_before:
            nextTimeline.has_more_before &&
            initialItems.length < MAX_OBSERVATIONS,
          next_before:
            nextTimeline.has_more_before &&
            initialItems.length < MAX_OBSERVATIONS
              ? nextTimeline.next_before
              : null,
          items: initialItems,
        };
        setTimeline(nextTimeline);

        const range = alignedRange(nextTimeline, "DAILY");
        if (range) {
          setPeriodFrom(range.from);
          setPeriodBefore(range.before);
          await loadPeriod(range, "DAILY", nextDetail.quote_asset);
        } else {
          setPeriodError(
            "A cobertura persistida ainda não contém um período UTC completo.",
          );
        }
      } catch (nextError) {
        if (sequence !== mainSequence.current) return;
        setError(detailError(nextError));
      } finally {
        if (sequence === mainSequence.current) setLoading(false);
      }
    })();

    return () => {
      mainSequence.current += 1;
      timelineSequence.current += 1;
      periodSequence.current += 1;
    };
  }, [revision, sessionId]);

  const recentObservations = useMemo(
    () => timeline?.items.slice(-ACCESSIBLE_OBSERVATIONS).reverse() ?? [],
    [timeline],
  );

  async function loadOlder(): Promise<void> {
    if (!timeline?.has_more_before || !timeline.next_before) return;
    const remaining = MAX_OBSERVATIONS - timeline.items.length;
    if (remaining <= 0) return;
    const sequence = ++timelineSequence.current;
    setOlderLoading(true);
    try {
      const older = await apiClient.getAppPaperPortfolioTimeline(sessionId, {
        before: timeline.next_before,
        limit: Math.min(TIMELINE_PAGE_SIZE, remaining),
      });
      if (sequence !== timelineSequence.current) return;
      if (!sameTimeline(timeline, older)) {
        setTimelineError(TIMELINE_MISMATCH);
        return;
      }
      const byIndex = new Map(
        [...older.items, ...timeline.items].map((item) => [
          item.candle_index,
          item,
        ]),
      );
      const items = [...byIndex.values()]
        .sort((left, right) => left.candle_index - right.candle_index)
        .slice(-MAX_OBSERVATIONS);
      setTimeline({
        ...timeline,
        range_start: older.range_start,
        count: items.length,
        has_more_before:
          older.has_more_before && items.length < MAX_OBSERVATIONS,
        next_before:
          older.has_more_before && items.length < MAX_OBSERVATIONS
            ? older.next_before
            : null,
        content_checksum: older.content_checksum,
        items,
      });
      setTimelineError(null);
    } catch {
      if (sequence !== timelineSequence.current) return;
      setTimelineError(TIMELINE_ERROR);
    } finally {
      if (sequence === timelineSequence.current) setOlderLoading(false);
    }
  }

  function changeGranularity(value: PaperPeriodGranularity): void {
    setGranularity(value);
    if (!timeline) return;
    const range = alignedRange(timeline, value);
    setPeriodFrom(range?.from ?? "");
    setPeriodBefore(range?.before ?? "");
  }

  function applyPeriod(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (!periodFrom || !periodBefore) {
      setPeriodError("Informe um período UTC completo dentro da cobertura.");
      return;
    }
    const from = new Date(`${periodFrom}:00Z`);
    const before = new Date(`${periodBefore}:00Z`);
    if (
      !Number.isFinite(from.getTime()) ||
      !Number.isFinite(before.getTime()) ||
      from >= before
    ) {
      setPeriodError("O período UTC deve ser válido e crescente.");
      return;
    }
    if (!detail) return;
    void loadPeriod(
      { from: periodFrom, before: periodBefore },
      granularity,
      detail.quote_asset,
    );
  }

  return (
    <section className="page-stack" aria-labelledby="app-performance-title">
      <header className="page-heading portfolio-performance-heading">
        <div>
          <p className="eyebrow">Sessão autorizada · timeline persistida</p>
          <h1 id="app-performance-title">Performance da sessão</h1>
          <p>
            Mark-to-market histórico e métricas realizadas, read-only e em UTC.
          </p>
        </div>
        <div className="portfolio-performance-actions">
          <Link
            className="button button--ghost"
            to={`/app/sessions/${sessionId}`}
          >
            Voltar à sessão
          </Link>
          <button
            className="button button--ghost"
            type="button"
            disabled={loading}
            onClick={() => setRevision((value) => value + 1)}
          >
            Atualizar
          </button>
        </div>
      </header>

      {loading ? (
        <LoadingState message="Carregando performance autorizada…" />
      ) : error ? (
        <InlineError message={error} />
      ) : detail ? (
        <>
          <section
            className="instrument-chart-meta"
            aria-label="Identificação da sessão"
          >
            <div>
              <span>Instrumento</span>
              <strong>
                {detail.base_asset}/{detail.quote_asset}
              </strong>
            </div>
            <div>
              <span>Timeframe</span>
              <strong>{detail.timeframe} · UTC</strong>
            </div>
            <div>
              <span>Estratégia</span>
              <strong>
                {detail.strategy_name}@{detail.strategy_version}
              </strong>
            </div>
          </section>

          {timelineError && <InlineError message={timelineError} />}
          {timeline ? (
            <>
              <section
                className="instrument-chart-summary"
                aria-label="Proveniência da timeline"
              >
                <div>
                  <span>Timeline ID</span>
                  <strong>{abbreviated(timeline.timeline_id)}</strong>
                </div>
                <div>
                  <span>Timeline checksum</span>
                  <strong>
                    {abbreviated(timeline.timeline_content_checksum)}
                  </strong>
                </div>
                <div>
                  <span>Content checksum</span>
                  <strong>{abbreviated(timeline.content_checksum)}</strong>
                </div>
                <div>
                  <span>Observações carregadas</span>
                  <strong>
                    {timeline.items.length}/{timeline.total_observations}
                  </strong>
                </div>
                <div>
                  <span>Range UTC</span>
                  <strong>
                    {formatUtc(timeline.range_start)} →{" "}
                    {formatUtc(timeline.range_end)}
                  </strong>
                </div>
              </section>

              {timeline.items.length === 0 ? (
                <EmptyState
                  title="Timeline sem observações"
                  description="O artefato persistido ainda não possui observações disponíveis."
                />
              ) : (
                <>
                  <PortfolioPerformanceCharts
                    observations={timeline.items}
                    quoteAsset={timeline.quote_asset}
                    primaryLabel="Sessão autorizada"
                  />

                  <section aria-labelledby="timeline-table-title">
                    <div className="section-heading">
                      <div>
                        <p className="eyebrow">
                          Representação textual acessível
                        </p>
                        <h2 id="timeline-table-title">
                          Observações históricas recentes
                        </h2>
                      </div>
                      <span>Máximo de {MAX_OBSERVATIONS} no browser</span>
                    </div>
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>Candle close UTC</th>
                            <th>Equity</th>
                            <th>PnL realizado</th>
                            <th>PnL não realizado</th>
                            <th>Fees</th>
                            <th>Slippage</th>
                            <th>Drawdown</th>
                            <th>Drawdown %</th>
                            <th>Risk halt</th>
                          </tr>
                        </thead>
                        <tbody>
                          {recentObservations.map((item) => (
                            <tr key={item.candle_index}>
                              <td>{formatUtc(item.candle_close_time)}</td>
                              <td>{item.equity}</td>
                              <td>{item.realized_pnl}</td>
                              <td>{item.unrealized_pnl}</td>
                              <td>{item.total_fees}</td>
                              <td>{item.total_slippage_cost}</td>
                              <td>{item.drawdown}</td>
                              <td>{item.drawdown_pct}</td>
                              <td>{item.risk_halt ? "Ativo" : "Inativo"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {timeline.has_more_before &&
                      timeline.items.length < MAX_OBSERVATIONS && (
                        <button
                          className="button button--ghost"
                          type="button"
                          disabled={olderLoading}
                          onClick={() => void loadOlder()}
                        >
                          {olderLoading
                            ? "Carregando histórico…"
                            : "Carregar histórico anterior"}
                        </button>
                      )}
                  </section>
                </>
              )}

              <section aria-labelledby="period-performance-title">
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">Realized-only · calendário UTC</p>
                    <h2 id="period-performance-title">
                      Performance realizada por período
                    </h2>
                  </div>
                </div>
                <aside className="period-metrics-note">
                  Esta visão inclui apenas resultados realizados. Equity e PnL
                  não realizado são exibidos na timeline histórica.
                </aside>
                <form
                  className="panel form-panel period-metrics-filters"
                  aria-label="Período realizado da sessão"
                  onSubmit={applyPeriod}
                >
                  <div className="form-grid">
                    <label>
                      Granularidade
                      <select
                        value={granularity}
                        onChange={(event) =>
                          changeGranularity(
                            event.target.value as PaperPeriodGranularity,
                          )
                        }
                      >
                        {Object.entries(granularityLabels).map(
                          ([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ),
                        )}
                      </select>
                    </label>
                    <label>
                      Início UTC
                      <input
                        type="datetime-local"
                        value={periodFrom}
                        required
                        onChange={(event) => setPeriodFrom(event.target.value)}
                      />
                    </label>
                    <label>
                      Fim exclusivo UTC
                      <input
                        type="datetime-local"
                        value={periodBefore}
                        required
                        onChange={(event) =>
                          setPeriodBefore(event.target.value)
                        }
                      />
                    </label>
                  </div>
                  <button
                    className="button"
                    type="submit"
                    disabled={periodLoading}
                  >
                    {periodLoading ? "Carregando período…" : "Aplicar período"}
                  </button>
                </form>

                {periodError && <InlineError message={periodError} />}
                {periodSeries && (
                  <>
                    <section
                      className="instrument-chart-summary"
                      aria-label="Proveniência das métricas por período"
                    >
                      <div>
                        <span>Query checksum</span>
                        <strong>
                          {abbreviated(periodSeries.query_checksum)}
                        </strong>
                      </div>
                      <div>
                        <span>Content checksum</span>
                        <strong>
                          {abbreviated(periodSeries.content_checksum)}
                        </strong>
                      </div>
                      <div>
                        <span>Buckets</span>
                        <strong>{periodSeries.items.length}</strong>
                      </div>
                      <div>
                        <span>Range UTC</span>
                        <strong>
                          {formatUtc(periodSeries.period_from)} →{" "}
                          {formatUtc(periodSeries.period_before)}
                        </strong>
                      </div>
                    </section>
                    <PeriodPerformanceCharts
                      series={periodSeries}
                      quoteAsset={periodSeries.quote_asset}
                    />
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>Período UTC</th>
                            <th>Realizações</th>
                            <th>PnL realizado</th>
                            <th>Fees realizadas</th>
                            <th>Slippage realizado</th>
                            <th>Win rate</th>
                            <th>Profit factor</th>
                          </tr>
                        </thead>
                        <tbody>
                          {periodSeries.items.map((bucket) => (
                            <tr key={bucket.period_start}>
                              <td>
                                {formatUtc(bucket.period_start)} →{" "}
                                {formatUtc(bucket.period_end)}
                              </td>
                              <td>{bucket.realizations_count}</td>
                              <td>{bucket.realized_pnl}</td>
                              <td>{bucket.realized_fees}</td>
                              <td>{bucket.realized_slippage_cost}</td>
                              <td>{bucket.win_rate_pct ?? "—"}</td>
                              <td>{bucket.profit_factor ?? "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
              </section>
            </>
          ) : timelineError ? null : (
            <LoadingState message="Carregando timeline persistida…" />
          )}
        </>
      ) : null}
    </section>
  );
}
