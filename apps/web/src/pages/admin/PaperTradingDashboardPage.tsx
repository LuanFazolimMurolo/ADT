import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { EmptyState, InlineError, LoadingState } from "../../components/States";
import { Pagination } from "../../components/Pagination";
import { apiClient } from "../../http/client";
import type {
  PageMeta,
  PaperDashboardResponse,
  PaperDashboardSession,
  PaperMarketRegime,
  PaperRunnerCycleStatus,
  PaperRunnerSessionStatus,
  PaperTrendDirection,
} from "../../types/api";
import { formatDate, formatMoney, getErrorMessage } from "../../utils/format";

const POLL_INTERVAL_MS = 30_000;
const PAGE_SIZE = 20;

const runnerCycleLabels: Record<PaperRunnerCycleStatus, string> = {
  COMPLETED: "Concluído",
  PARTIALLY_FAILED: "Falha parcial",
  FAILED: "Falhou",
};

const runnerSessionLabels: Record<PaperRunnerSessionStatus, string> = {
  UPDATED: "Atualizada",
  NOOP: "Sem mudanças",
  FAILED: "Falhou",
};

const regimeLabels: Record<PaperMarketRegime, string> = {
  warmup: "Aquecimento",
  trend: "Tendência",
  range: "Lateral",
  volatile: "Volátil",
};

const trendLabels: Record<PaperTrendDirection, string> = {
  none: "Sem direção",
  up: "Alta",
  down: "Baixa",
};

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

function formatPercentage(value: string): string {
  return `${formatExactDecimal(value)}%`;
}

function isNegativeDecimal(value: string): boolean {
  return value.startsWith("-");
}

function sessionLabel(session: PaperDashboardSession): string {
  return `${session.symbol} · ${session.timeframe} · ${session.strategy_name}@${session.strategy_version}`;
}

function runnerClass(
  status: PaperRunnerCycleStatus | PaperRunnerSessionStatus,
): string {
  if (status === "FAILED") return "paper-status paper-status--danger";
  if (status === "PARTIALLY_FAILED")
    return "paper-status paper-status--warning";
  if (status === "UPDATED" || status === "COMPLETED")
    return "paper-status paper-status--success";
  return "paper-status";
}

function sessionState(session: PaperDashboardSession): {
  label: string;
  className: string;
} {
  if (!session.state_available)
    return { label: "Pendente", className: "paper-status" };
  if (session.risk_halt) {
    return {
      label: "Risco interrompido",
      className: "paper-status paper-status--danger",
    };
  }
  if (session.runner?.status === "FAILED") {
    return {
      label: "Runner falhou",
      className: "paper-status paper-status--danger",
    };
  }
  return {
    label: "Inicializada",
    className: "paper-status paper-status--success",
  };
}

function SessionCard({
  session,
  selected,
  selectionDisabled,
  onToggle,
}: {
  session: PaperDashboardSession;
  selected: boolean;
  selectionDisabled: boolean;
  onToggle(): void;
}) {
  const state = sessionState(session);
  const metrics = session.metrics;
  const regime = session.latest_market_regime;
  const position = session.position;

  return (
    <article
      className={
        selected ? "paper-session paper-session--selected" : "paper-session"
      }
    >
      <header className="paper-session__header">
        <div>
          <p className="eyebrow">
            {session.timeframe} · {session.strategy_name}@
            {session.strategy_version}
          </p>
          <h3>{session.symbol}</h3>
          <small>ID {session.session_id.slice(0, 12)}</small>
        </div>
        <label className="paper-compare-toggle">
          <input
            type="checkbox"
            checked={selected}
            disabled={!selected && selectionDisabled}
            onChange={onToggle}
          />
          Comparar
        </label>
      </header>

      <div className="paper-session__badges">
        <span className={state.className}>{state.label}</span>
        {session.runner && (
          <span className={runnerClass(session.runner.status)}>
            {runnerSessionLabels[session.runner.status]}
          </span>
        )}
        {session.runner &&
          !session.runner.matches_current_state &&
          session.runner.status !== "FAILED" && (
            <span className="paper-status paper-status--warning">
              Estado defasado
            </span>
          )}
      </div>

      {metrics ? (
        <div className="paper-session__metrics">
          <div>
            <span>Equity</span>
            <strong>{formatMoney(metrics.equity, session.quote_asset)}</strong>
          </div>
          <div>
            <span>PnL total</span>
            <strong
              className={
                isNegativeDecimal(metrics.total_pnl)
                  ? "value-negative"
                  : "value-positive"
              }
            >
              {formatMoney(metrics.total_pnl, session.quote_asset)}
            </strong>
          </div>
          <div>
            <span>Retorno</span>
            <strong
              className={
                isNegativeDecimal(metrics.return_pct)
                  ? "value-negative"
                  : "value-positive"
              }
            >
              {formatPercentage(metrics.return_pct)}
            </strong>
          </div>
          <div>
            <span>Drawdown</span>
            <strong>{formatPercentage(metrics.drawdown_pct)}</strong>
          </div>
        </div>
      ) : (
        <div className="paper-session__pending">
          <strong>Estado ainda não inicializado</strong>
          <span>
            Capital configurado:{" "}
            {formatMoney(session.initial_capital, session.quote_asset)}
          </span>
        </div>
      )}

      <dl className="paper-session__details">
        <div>
          <dt>Posição</dt>
          <dd>
            {position?.is_open
              ? `${formatExactDecimal(position.base_quantity, 0)} ${session.base_asset}`
              : "Fechada"}
          </dd>
        </div>
        <div>
          <dt>Ordens abertas</dt>
          <dd>{session.open_orders_count}</dd>
        </div>
        <div>
          <dt>Candles</dt>
          <dd>{session.candles_processed ?? "—"}</dd>
        </div>
        <div>
          <dt>Regime</dt>
          <dd>
            {regime
              ? `${regimeLabels[regime.regime]} · ${trendLabels[regime.trend_direction]}`
              : "Não disponível"}
          </dd>
        </div>
      </dl>

      <footer className="paper-session__footer">
        <span>Última vela: {formatDate(session.last_candle_open_time)}</span>
        <span>Replay: {formatDate(session.replayed_at)}</span>
        <Link
          className="button button--ghost"
          to={`/admin/paper-trading/chart?session_id=${session.session_id}&base=${session.base_asset}&quote=${session.quote_asset}&timeframe=${session.timeframe}`}
        >
          Abrir gráfico
        </Link>
      </footer>
    </article>
  );
}

function ComparisonPanel({ sessions }: { sessions: PaperDashboardSession[] }) {
  if (sessions.length === 0) return null;

  const rows = [
    [
      "Equity",
      (session: PaperDashboardSession) =>
        session.metrics
          ? formatMoney(session.metrics.equity, session.quote_asset)
          : "—",
    ],
    [
      "PnL total",
      (session: PaperDashboardSession) =>
        session.metrics
          ? formatMoney(session.metrics.total_pnl, session.quote_asset)
          : "—",
    ],
    [
      "Retorno",
      (session: PaperDashboardSession) =>
        session.metrics ? formatPercentage(session.metrics.return_pct) : "—",
    ],
    [
      "Drawdown",
      (session: PaperDashboardSession) =>
        session.metrics ? formatPercentage(session.metrics.drawdown_pct) : "—",
    ],
    [
      "Posição",
      (session: PaperDashboardSession) =>
        session.position
          ? session.position.is_open
            ? "Aberta"
            : "Fechada"
          : "—",
    ],
    [
      "Ordens abertas",
      (session: PaperDashboardSession) => String(session.open_orders_count),
    ],
    [
      "Regime",
      (session: PaperDashboardSession) =>
        session.latest_market_regime
          ? regimeLabels[session.latest_market_regime.regime]
          : "—",
    ],
  ] as const;

  return (
    <section
      className="paper-comparison"
      aria-labelledby="paper-comparison-title"
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">Comparação local</p>
          <h2 id="paper-comparison-title">Sessões selecionadas</h2>
        </div>
        <span>
          {sessions.length === 1
            ? "Selecione mais uma sessão"
            : "Duas sessões selecionadas"}
        </span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Métrica</th>
              {sessions.map((session) => (
                <th key={session.session_id}>{sessionLabel(session)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, value]) => (
              <tr key={label}>
                <td>
                  <strong>{label}</strong>
                </td>
                {sessions.map((session) => (
                  <td key={session.session_id}>{value(session)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function PaperTradingDashboardPage() {
  const [page, setPage] = useState(1);
  const [dashboard, setDashboard] = useState<PaperDashboardResponse | null>(
    null,
  );
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const requestSequence = useRef(0);

  const load = useCallback(
    async (initial: boolean) => {
      const sequence = ++requestSequence.current;
      if (initial) setLoading(true);
      else setRefreshing(true);
      try {
        const result = await apiClient.getPaperTradingDashboard(
          page,
          PAGE_SIZE,
        );
        if (sequence !== requestSequence.current) return;
        setDashboard(result);
        setSelectedIds((current) =>
          current.filter((id) =>
            result.items.some((item) => item.session_id === id),
          ),
        );
        setUpdatedAt(new Date());
        setError(null);
      } catch (nextError) {
        if (sequence !== requestSequence.current) return;
        setError(
          getErrorMessage(
            nextError,
            "Não foi possível carregar o dashboard de paper trading.",
          ),
        );
      } finally {
        if (sequence === requestSequence.current) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [page],
  );

  useEffect(() => {
    void load(true);
    const interval = window.setInterval(
      () => void load(false),
      POLL_INTERVAL_MS,
    );
    return () => {
      window.clearInterval(interval);
      requestSequence.current += 1;
    };
  }, [load]);

  const selectedSessions = useMemo(() => {
    if (!dashboard) return [];
    return selectedIds
      .map((id) => dashboard.items.find((item) => item.session_id === id))
      .filter((item): item is PaperDashboardSession => item !== undefined);
  }, [dashboard, selectedIds]);

  const toggleSelected = (sessionId: string) => {
    setSelectedIds((current) => {
      if (current.includes(sessionId))
        return current.filter((id) => id !== sessionId);
      if (current.length >= 2) return current;
      return [...current, sessionId];
    });
  };

  const pagination: PageMeta | null = dashboard
    ? {
        page: dashboard.page,
        page_size: dashboard.page_size,
        total: dashboard.total,
        total_pages: dashboard.total_pages,
      }
    : null;

  return (
    <div>
      <div className="page-heading paper-dashboard-heading">
        <div>
          <p className="eyebrow">Monitoramento read-only</p>
          <h1>Paper trading</h1>
          <p>
            Performance, posição e execução das sessões persistidas pelo runner.
          </p>
        </div>
        <div className="paper-dashboard-actions">
          <small>
            {updatedAt
              ? `Atualizado às ${updatedAt.toLocaleTimeString("pt-BR")}`
              : "Aguardando atualização"}
          </small>
          <button
            className="button button--ghost"
            type="button"
            disabled={refreshing}
            onClick={() => void load(false)}
          >
            {refreshing ? "Atualizando…" : "Atualizar agora"}
          </button>
        </div>
      </div>

      {loading ? (
        <LoadingState message="Carregando performance do paper trading…" />
      ) : error && !dashboard ? (
        <InlineError message={error} />
      ) : dashboard ? (
        <>
          {error && <InlineError message={error} />}
          <section className="paper-runner-strip" aria-label="Estado do runner">
            <div>
              <span
                className={
                  dashboard.runner
                    ? runnerClass(dashboard.runner.status)
                    : "paper-status"
                }
              >
                {dashboard.runner
                  ? runnerCycleLabels[dashboard.runner.status]
                  : "Aguardando"}
              </span>
              <div>
                <small>RUNNER</small>
                <strong>
                  {dashboard.runner
                    ? `Ciclo ${dashboard.runner.cycle_index}`
                    : "Nenhum ciclo persistido"}
                </strong>
              </div>
            </div>
            <div>
              <small>ÚLTIMA EXECUÇÃO</small>
              <strong>
                {formatDate(dashboard.runner?.finished_at ?? null)}
              </strong>
            </div>
            <div>
              <small>PRÓXIMO CICLO</small>
              <strong>
                {formatDate(dashboard.runner?.next_cycle_at ?? null)}
              </strong>
            </div>
            <div>
              <small>ATUALIZAÇÃO</small>
              <strong>Polling a cada 30 segundos</strong>
            </div>
          </section>

          <section className="section-heading">
            <div>
              <p className="eyebrow">Totais da página</p>
              <h2>Performance agregada</h2>
            </div>
            <span>
              Valores nominais; as sessões podem usar ativos de cotação
              diferentes.
            </span>
          </section>

          <section className="paper-totals-grid">
            <article className="metric-card">
              <span className="metric-label">SESSÕES</span>
              <strong>{dashboard.totals.sessions_count}</strong>
              <small>
                {dashboard.totals.initialized_count} inicializadas ·{" "}
                {dashboard.totals.pending_count} pendentes
              </small>
            </article>
            <article className="metric-card metric-card--primary">
              <span className="metric-label">EQUITY NOMINAL</span>
              <strong>{formatExactDecimal(dashboard.totals.equity)}</strong>
              <small>Somente sessões inicializadas nesta página</small>
            </article>
            <article
              className={
                isNegativeDecimal(dashboard.totals.total_pnl)
                  ? "metric-card metric-card--negative"
                  : "metric-card"
              }
            >
              <span className="metric-label">PNL NOMINAL</span>
              <strong>{formatExactDecimal(dashboard.totals.total_pnl)}</strong>
              <small>
                Retorno {formatPercentage(dashboard.totals.return_pct)}
              </small>
            </article>
            <article className="metric-card">
              <span className="metric-label">DRAWDOWN MÁXIMO</span>
              <strong>
                {formatPercentage(dashboard.totals.maximum_drawdown_pct)}
              </strong>
              <small>Maior percentual entre as sessões da página</small>
            </article>
            <article className="metric-card">
              <span className="metric-label">POSIÇÕES ABERTAS</span>
              <strong>{dashboard.totals.open_positions_count}</strong>
              <small>{dashboard.totals.open_orders_count} ordens abertas</small>
            </article>
            <article
              className={
                dashboard.totals.runner_failed_count ||
                dashboard.totals.risk_halted_count
                  ? "metric-card metric-card--negative"
                  : "metric-card"
              }
            >
              <span className="metric-label">ALERTAS</span>
              <strong>
                {dashboard.totals.runner_failed_count +
                  dashboard.totals.risk_halted_count}
              </strong>
              <small>
                {dashboard.totals.runner_failed_count} falhas ·{" "}
                {dashboard.totals.risk_halted_count} risk halts
              </small>
            </article>
          </section>

          <ComparisonPanel sessions={selectedSessions} />

          <section className="section-heading paper-sessions-heading">
            <div>
              <p className="eyebrow">Sessões persistidas</p>
              <h2>Performance por sessão</h2>
            </div>
            <span>Selecione até duas sessões para comparar.</span>
          </section>

          {dashboard.items.length === 0 ? (
            <EmptyState
              title="Nenhuma sessão de paper trading"
              description="As sessões aparecerão aqui depois que forem configuradas no backend."
            />
          ) : (
            <div className="paper-session-grid">
              {dashboard.items.map((session) => (
                <SessionCard
                  key={session.session_id}
                  session={session}
                  selected={selectedIds.includes(session.session_id)}
                  selectionDisabled={selectedIds.length >= 2}
                  onToggle={() => toggleSelected(session.session_id)}
                />
              ))}
            </div>
          )}

          {pagination && (
            <Pagination pagination={pagination} onChange={setPage} />
          )}
        </>
      ) : null}
    </div>
  );
}
