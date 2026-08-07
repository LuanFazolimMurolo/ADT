import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, useSearchParams } from "react-router-dom";
import { EmptyState, InlineError, LoadingState } from "../../components/States";
import { Pagination } from "../../components/Pagination";
import { apiClient, type PaperTradeJournalFilters } from "../../http/client";
import type {
  PageMeta,
  PaperTradeJournalPageResponse,
  PaperTradeJournalRecord,
  PaperTradeStatus,
} from "../../types/api";
import { formatDate, formatMoney, getErrorMessage } from "../../utils/format";

const PAGE_SIZE = 20;

interface JournalFilterForm {
  sessionId: string;
  baseAsset: string;
  quoteAsset: string;
  timeframe: string;
  strategyName: string;
  strategyVersion: string;
  status: "" | PaperTradeStatus;
  openedFrom: string;
  openedBefore: string;
  closedFrom: string;
  closedBefore: string;
}

const emptyFilters: JournalFilterForm = {
  sessionId: "",
  baseAsset: "",
  quoteAsset: "",
  timeframe: "",
  strategyName: "",
  strategyVersion: "",
  status: "",
  openedFrom: "",
  openedBefore: "",
  closedFrom: "",
  closedBefore: "",
};

const statusLabels: Record<PaperTradeStatus, string> = {
  OPEN: "Aberta",
  CLOSED: "Fechada",
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

function dateTimeFilter(value: string): string | undefined {
  if (!value) return undefined;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString();
}

function normalizedFilters(value: JournalFilterForm): PaperTradeJournalFilters {
  return {
    sessionId: value.sessionId.trim() || undefined,
    baseAsset: value.baseAsset.trim() || undefined,
    quoteAsset: value.quoteAsset.trim() || undefined,
    timeframe: value.timeframe.trim() || undefined,
    strategyName: value.strategyName.trim() || undefined,
    strategyVersion: value.strategyVersion.trim() || undefined,
    status: value.status || undefined,
    openedFrom: dateTimeFilter(value.openedFrom),
    openedBefore: dateTimeFilter(value.openedBefore),
    closedFrom: dateTimeFilter(value.closedFrom),
    closedBefore: dateTimeFilter(value.closedBefore),
  };
}

function statusClass(status: PaperTradeStatus): string {
  return status === "OPEN"
    ? "paper-status paper-status--warning"
    : "paper-status paper-status--success";
}

function executionTags(record: PaperTradeJournalRecord): string {
  const executions = [
    ...record.trade.entry_executions,
    ...record.trade.exit_executions,
  ];
  const tags = executions
    .map((execution) => execution.client_tag)
    .filter((tag): tag is string => Boolean(tag));
  return tags.length ? tags.join(" · ") : "Sem client tags";
}

function TradeCard({
  record,
  selectedTradeId,
}: {
  record: PaperTradeJournalRecord;
  selectedTradeId: string | null;
}) {
  const trade = record.trade;
  const selected = trade.trade_id === selectedTradeId;
  const chartParams = new URLSearchParams({
    session_id: record.session_id,
    base: record.base_asset,
    quote: record.quote_asset,
    timeframe: record.timeframe,
    trade_id: trade.trade_id,
  });

  return (
    <article
      className={
        selected
          ? "journal-trade-card journal-trade-card--selected"
          : "journal-trade-card"
      }
    >
      <header className="journal-trade-card__header">
        <div>
          <p className="eyebrow">
            {record.timeframe} · {record.strategy_name}@
            {record.strategy_version}
          </p>
          <h3>{record.symbol}</h3>
          <small>Trade {trade.trade_id.slice(0, 12)}</small>
        </div>
        <span className={statusClass(trade.status)}>
          {statusLabels[trade.status]}
        </span>
      </header>

      <div className="journal-trade-grid">
        <div>
          <span>Aberta em</span>
          <strong>{formatDate(trade.opened_at)}</strong>
        </div>
        <div>
          <span>Fechada em</span>
          <strong>{formatDate(trade.closed_at)}</strong>
        </div>
        <div>
          <span>Quantidade aberta</span>
          <strong>
            {formatExactDecimal(trade.opened_quantity, 0)} {record.base_asset}
          </strong>
        </div>
        <div>
          <span>Quantidade restante</span>
          <strong>
            {formatExactDecimal(trade.remaining_quantity, 0)}{" "}
            {record.base_asset}
          </strong>
        </div>
        <div>
          <span>Preço médio de entrada</span>
          <strong>
            {formatMoney(trade.average_entry_price, record.quote_asset)}
          </strong>
        </div>
        <div>
          <span>Preço médio de saída</span>
          <strong>
            {trade.average_exit_price
              ? formatMoney(trade.average_exit_price, record.quote_asset)
              : "—"}
          </strong>
        </div>
        <div>
          <span>PnL realizado</span>
          <strong>{formatMoney(trade.realized_pnl, record.quote_asset)}</strong>
        </div>
        <div>
          <span>PnL não realizado</span>
          <strong>
            {formatMoney(trade.unrealized_pnl, record.quote_asset)}
          </strong>
        </div>
        <div>
          <span>PnL líquido</span>
          <strong
            className={
              trade.net_pnl.startsWith("-")
                ? "value-negative"
                : "value-positive"
            }
          >
            {formatMoney(trade.net_pnl, record.quote_asset)}
          </strong>
        </div>
        <div>
          <span>Taxas</span>
          <strong>{formatMoney(trade.total_fees, record.quote_asset)}</strong>
        </div>
      </div>

      <div className="journal-trade-actions">
        <Link
          className="button button--ghost"
          to={`/admin/paper-trading/chart?${chartParams.toString()}`}
        >
          Abrir no gráfico
        </Link>
      </div>

      <details className="journal-trade-details">
        <summary>Execuções e auditoria</summary>
        <dl>
          <div>
            <dt>Entradas</dt>
            <dd>{trade.entry_executions.length}</dd>
          </div>
          <div>
            <dt>Saídas</dt>
            <dd>{trade.exit_executions.length}</dd>
          </div>
          <div>
            <dt>Client tags</dt>
            <dd>{executionTags(record)}</dd>
          </div>
          <div>
            <dt>State ID</dt>
            <dd>
              <code>{record.state_id}</code>
            </dd>
          </div>
          <div>
            <dt>State checksum</dt>
            <dd>
              <code>{record.state_checksum}</code>
            </dd>
          </div>
        </dl>
      </details>
    </article>
  );
}

export function PaperTradeJournalPage() {
  const [searchParams] = useSearchParams();
  const initialSessionId = searchParams.get("session_id")?.trim() ?? "";
  const selectedTradeId = searchParams.get("trade_id")?.trim() || null;
  const initialForm = {
    ...emptyFilters,
    sessionId: initialSessionId,
  };
  const [draftFilters, setDraftFilters] =
    useState<JournalFilterForm>(initialForm);
  const [filters, setFilters] = useState<PaperTradeJournalFilters>(
    initialSessionId ? { sessionId: initialSessionId } : {},
  );
  const [page, setPage] = useState(1);
  const [journal, setJournal] = useState<PaperTradeJournalPageResponse | null>(
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
        const result = await apiClient.getPaperTradeJournal(
          filters,
          page,
          PAGE_SIZE,
        );
        if (sequence !== requestSequence.current) return;
        setJournal(result);
        setError(null);
      } catch (nextError) {
        if (sequence !== requestSequence.current) return;
        setError(
          getErrorMessage(
            nextError,
            "Não foi possível carregar o trade journal.",
          ),
        );
      } finally {
        if (sequence === requestSequence.current) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [filters, page],
  );

  useEffect(() => {
    void load(true);
    return () => {
      requestSequence.current += 1;
    };
  }, [load]);

  const applyFilters = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setPage(1);
    setFilters(normalizedFilters(draftFilters));
  };

  const clearFilters = () => {
    setDraftFilters(emptyFilters);
    setPage(1);
    setFilters({});
  };

  const pagination: PageMeta | null = journal
    ? {
        page: journal.page,
        page_size: journal.page_size,
        total: journal.total,
        total_pages: journal.total_pages,
      }
    : null;

  const activeFilterCount = useMemo(
    () => Object.values(filters).filter((value) => value !== undefined).length,
    [filters],
  );

  return (
    <div>
      <div className="page-heading journal-heading">
        <div>
          <p className="eyebrow">Reconstrução determinística</p>
          <h1>Trade journal</h1>
          <p>
            Operações verificadas, ciclos flat-to-flat e vínculo com o estado
            persistido.
          </p>
        </div>
        <button
          className="button button--ghost"
          type="button"
          disabled={refreshing}
          onClick={() => void load(false)}
        >
          {refreshing ? "Atualizando…" : "Atualizar"}
        </button>
      </div>

      <form
        className="journal-filters"
        aria-label="Filtros do trade journal"
        onSubmit={applyFilters}
      >
        <label>
          Sessão
          <input
            value={draftFilters.sessionId}
            maxLength={64}
            placeholder="SHA-256 da sessão"
            onChange={(event) =>
              setDraftFilters((current) => ({
                ...current,
                sessionId: event.target.value,
              }))
            }
          />
        </label>
        <label>
          Ativo base
          <input
            value={draftFilters.baseAsset}
            placeholder="BTC"
            onChange={(event) =>
              setDraftFilters((current) => ({
                ...current,
                baseAsset: event.target.value,
              }))
            }
          />
        </label>
        <label>
          Ativo de cotação
          <input
            value={draftFilters.quoteAsset}
            placeholder="USDT"
            onChange={(event) =>
              setDraftFilters((current) => ({
                ...current,
                quoteAsset: event.target.value,
              }))
            }
          />
        </label>
        <label>
          Timeframe
          <input
            value={draftFilters.timeframe}
            placeholder="1m"
            onChange={(event) =>
              setDraftFilters((current) => ({
                ...current,
                timeframe: event.target.value,
              }))
            }
          />
        </label>
        <label>
          Estratégia
          <input
            value={draftFilters.strategyName}
            placeholder="no-op"
            onChange={(event) =>
              setDraftFilters((current) => ({
                ...current,
                strategyName: event.target.value,
              }))
            }
          />
        </label>
        <label>
          Versão
          <input
            value={draftFilters.strategyVersion}
            placeholder="1"
            onChange={(event) =>
              setDraftFilters((current) => ({
                ...current,
                strategyVersion: event.target.value,
              }))
            }
          />
        </label>
        <label>
          Status
          <select
            value={draftFilters.status}
            onChange={(event) =>
              setDraftFilters((current) => ({
                ...current,
                status: event.target.value as "" | PaperTradeStatus,
              }))
            }
          >
            <option value="">Todos</option>
            <option value="OPEN">Aberta</option>
            <option value="CLOSED">Fechada</option>
          </select>
        </label>
        <label>
          Abertura inicial
          <input
            type="datetime-local"
            value={draftFilters.openedFrom}
            onChange={(event) =>
              setDraftFilters((current) => ({
                ...current,
                openedFrom: event.target.value,
              }))
            }
          />
        </label>
        <label>
          Abertura final
          <input
            type="datetime-local"
            value={draftFilters.openedBefore}
            onChange={(event) =>
              setDraftFilters((current) => ({
                ...current,
                openedBefore: event.target.value,
              }))
            }
          />
        </label>
        <label>
          Fechamento inicial
          <input
            type="datetime-local"
            value={draftFilters.closedFrom}
            onChange={(event) =>
              setDraftFilters((current) => ({
                ...current,
                closedFrom: event.target.value,
              }))
            }
          />
        </label>
        <label>
          Fechamento final
          <input
            type="datetime-local"
            value={draftFilters.closedBefore}
            onChange={(event) =>
              setDraftFilters((current) => ({
                ...current,
                closedBefore: event.target.value,
              }))
            }
          />
        </label>
        <div className="journal-filter-actions">
          <button className="button button--primary" type="submit">
            Aplicar filtros
          </button>
          <button
            className="button button--ghost"
            type="button"
            onClick={clearFilters}
          >
            Limpar
          </button>
          <small>{activeFilterCount} filtros ativos</small>
        </div>
      </form>

      {loading ? (
        <LoadingState message="Carregando trade journal…" />
      ) : error && !journal ? (
        <InlineError message={error} />
      ) : journal ? (
        <>
          {error && <InlineError message={error} />}

          <section className="journal-totals" aria-label="Totais do journal">
            <article className="metric-card">
              <span className="metric-label">OPERAÇÕES</span>
              <strong>{journal.totals.trades_count}</strong>
              <small>
                {journal.totals.closed_trades_count} fechadas ·{" "}
                {journal.totals.open_trades_count} abertas
              </small>
            </article>
            <article className="metric-card">
              <span className="metric-label">PNL REALIZADO NOMINAL</span>
              <strong>
                {formatExactDecimal(journal.totals.total_realized_pnl)}
              </strong>
              <small>Somatório exato dos filtros aplicados</small>
            </article>
            <article className="metric-card">
              <span className="metric-label">PNL NÃO REALIZADO NOMINAL</span>
              <strong>
                {formatExactDecimal(journal.totals.total_unrealized_pnl)}
              </strong>
              <small>Posições ainda abertas</small>
            </article>
            <article className="metric-card">
              <span className="metric-label">TAXAS NOMINAIS</span>
              <strong>{formatExactDecimal(journal.totals.total_fees)}</strong>
              <small>Valores podem usar ativos de cotação diferentes</small>
            </article>
          </section>

          {journal.items.length ? (
            <section className="journal-trade-list" aria-label="Operações">
              {journal.items.map((record) => (
                <TradeCard
                  key={record.trade.trade_id}
                  record={record}
                  selectedTradeId={selectedTradeId}
                />
              ))}
            </section>
          ) : (
            <EmptyState
              title="Nenhuma operação encontrada"
              description="Altere os filtros ou aguarde uma sessão produzir fills verificados."
            />
          )}

          {pagination && (
            <Pagination pagination={pagination} onChange={setPage} />
          )}
        </>
      ) : null}
    </div>
  );
}
