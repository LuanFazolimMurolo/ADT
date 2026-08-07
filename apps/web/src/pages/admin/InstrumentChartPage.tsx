import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, useSearchParams } from "react-router-dom";
import { FinancialCandlestickChart } from "../../components/FinancialCandlestickChart";
import { EmptyState, InlineError, LoadingState } from "../../components/States";
import { apiClient } from "../../http/client";
import type {
  MarketCandle,
  MarketCandlePageResponse,
  PaperChartAnnotationPageResponse,
  PaperDashboardSession,
} from "../../types/api";
import { formatDate, getErrorMessage } from "../../utils/format";

const PAGE_LIMIT = 1_000;
const MAX_LOADED_CANDLES = 5_000;
const ANNOTATION_LIMIT = 5_000;
const ACCESSIBLE_ANNOTATION_LIMIT = 100;
const POLL_INTERVAL_MS = 30_000;
const TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"] as const;
const SESSION_ID = /^[0-9a-f]{64}$/;

interface InstrumentSelection {
  sessionId: string;
  baseAsset: string;
  quoteAsset: string;
  timeframe: string;
}

interface LoadedChartData {
  items: MarketCandle[];
  datasetVersion: string;
  contentChecksum: string;
  nextBefore: string | null;
  hasMoreBefore: boolean;
  availableStart: string;
  availableEnd: string;
}

interface AccessibleAnnotation {
  id: string;
  eventTime: string;
  label: string;
  detail: string;
  tradeId: string | null;
}

function normalizedAsset(value: string): string {
  return value.trim().toUpperCase();
}

function initialSelection(params: URLSearchParams): InstrumentSelection {
  const timeframe = params.get("timeframe") ?? "15m";
  return {
    sessionId: params.get("session_id")?.trim() ?? "",
    baseAsset: normalizedAsset(params.get("base") ?? "BTC"),
    quoteAsset: normalizedAsset(params.get("quote") ?? "USDT"),
    timeframe: TIMEFRAMES.includes(timeframe as (typeof TIMEFRAMES)[number])
      ? timeframe
      : "15m",
  };
}

function mergeCandles(
  current: readonly MarketCandle[],
  incoming: readonly MarketCandle[],
): MarketCandle[] {
  const byOpenTime = new Map<string, MarketCandle>();
  for (const candle of current) byOpenTime.set(candle.open_time, candle);
  for (const candle of incoming) byOpenTime.set(candle.open_time, candle);
  return [...byOpenTime.values()]
    .sort((left, right) => left.open_time.localeCompare(right.open_time))
    .slice(-MAX_LOADED_CANDLES);
}

function fromPage(page: MarketCandlePageResponse): LoadedChartData {
  return {
    items: page.items,
    datasetVersion: page.dataset_version,
    contentChecksum: page.content_checksum,
    nextBefore: page.next_before,
    hasMoreBefore: page.has_more_before,
    availableStart: page.available_start,
    availableEnd: page.available_end,
  };
}

function formatExact(value: string): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return value;
  return new Intl.NumberFormat("pt-BR", {
    maximumFractionDigits: 8,
  }).format(parsed);
}

function sessionOptionLabel(session: PaperDashboardSession): string {
  return `${session.symbol} · ${session.timeframe} · ${session.strategy_name}@${session.strategy_version} · ${session.session_id.slice(0, 12)}`;
}

function accessibleAnnotations(
  annotations: PaperChartAnnotationPageResponse | null,
): AccessibleAnnotation[] {
  if (!annotations) return [];

  const values: AccessibleAnnotation[] = [
    ...annotations.fills.map((fill) => ({
      id: fill.fill_id,
      eventTime: fill.event_time,
      label: fill.role === "ENTRY" ? "Entrada executada" : "Saída executada",
      detail: `${fill.side} ${formatExact(fill.quantity)} a ${formatExact(fill.execution_price)} · trade #${fill.trade_sequence}`,
      tradeId: fill.trade_id,
    })),
    ...annotations.orders
      .filter((order) => order.is_engine_protective_stop)
      .map((order) => ({
        id: order.order_id,
        eventTime: order.created_at,
        label: "Stop protetivo",
        detail: `${order.status} · preço ${formatExact(order.stop_price ?? "0")} · quantidade ${formatExact(order.quantity)}`,
        tradeId: null,
      })),
  ];

  return values
    .sort((left, right) => right.eventTime.localeCompare(left.eventTime))
    .slice(0, ACCESSIBLE_ANNOTATION_LIMIT);
}

export function InstrumentChartPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initial = useMemo(() => initialSelection(searchParams), []);
  const selectedTradeId = searchParams.get("trade_id")?.trim() || null;
  const [draft, setDraft] = useState<InstrumentSelection>(initial);
  const [selection, setSelection] = useState<InstrumentSelection>(initial);
  const [sessions, setSessions] = useState<PaperDashboardSession[]>([]);
  const [sessionOptionsError, setSessionOptionsError] = useState<string | null>(
    null,
  );
  const [data, setData] = useState<LoadedChartData | null>(null);
  const [annotations, setAnnotations] =
    useState<PaperChartAnnotationPageResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [loadingAnnotations, setLoadingAnnotations] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [annotationError, setAnnotationError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [resetKey, setResetKey] = useState(0);
  const requestSequence = useRef(0);
  const annotationSequence = useRef(0);

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
            "Não foi possível listar as sessões disponíveis.",
          ),
        );
      });
    return () => {
      active = false;
    };
  }, []);

  const loadLatest = useCallback(
    async (initialLoad: boolean) => {
      const sequence = ++requestSequence.current;
      if (initialLoad) setLoading(true);
      else setRefreshing(true);

      try {
        const page = await apiClient.getMarketCandles(
          selection.baseAsset,
          selection.quoteAsset,
          {
            timeframe: selection.timeframe,
            limit: PAGE_LIMIT,
          },
        );
        if (sequence !== requestSequence.current) return;

        setData((current) => {
          if (
            current === null ||
            current.datasetVersion !== page.dataset_version
          ) {
            return fromPage(page);
          }
          return {
            ...current,
            items: mergeCandles(current.items, page.items),
            contentChecksum: page.content_checksum,
            availableStart: page.available_start,
            availableEnd: page.available_end,
          };
        });
        setError(null);
        setUpdatedAt(new Date());
      } catch (nextError) {
        if (sequence !== requestSequence.current) return;
        setError(
          getErrorMessage(
            nextError,
            "Não foi possível carregar os candles persistidos.",
          ),
        );
      } finally {
        if (sequence === requestSequence.current) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [selection],
  );

  useEffect(() => {
    setData(null);
    setAnnotations(null);
    setError(null);
    setAnnotationError(null);
    setResetKey((value) => value + 1);
    void loadLatest(true);
    const interval = window.setInterval(
      () => void loadLatest(false),
      POLL_INTERVAL_MS,
    );
    return () => {
      window.clearInterval(interval);
      requestSequence.current += 1;
    };
  }, [loadLatest]);

  const rangeStart = data?.items[0]?.open_time ?? null;
  const rangeEnd =
    data && data.items.length > 0
      ? data.items[data.items.length - 1].close_time
      : null;
  const datasetVersion = data?.datasetVersion ?? null;

  useEffect(() => {
    const sessionId = selection.sessionId;
    if (!sessionId || !rangeStart || !rangeEnd || !datasetVersion) {
      setAnnotations(null);
      setAnnotationError(null);
      setLoadingAnnotations(false);
      annotationSequence.current += 1;
      return;
    }

    const sequence = ++annotationSequence.current;
    setLoadingAnnotations(true);
    void apiClient
      .getPaperChartAnnotations(sessionId, {
        start: rangeStart,
        before: rangeEnd,
        limit: ANNOTATION_LIMIT,
      })
      .then((result) => {
        if (sequence !== annotationSequence.current) return;
        if (
          result.base_asset !== selection.baseAsset ||
          result.quote_asset !== selection.quoteAsset ||
          result.timeframe !== selection.timeframe
        ) {
          throw new Error(
            "A sessão selecionada pertence a outro instrumento ou timeframe.",
          );
        }
        if (result.dataset_version !== datasetVersion) {
          throw new Error(
            "As anotações pertencem a outra versão do dataset. Atualize o gráfico.",
          );
        }
        setAnnotations(result);
        setAnnotationError(null);
      })
      .catch((nextError) => {
        if (sequence !== annotationSequence.current) return;
        setAnnotations(null);
        setAnnotationError(
          getErrorMessage(
            nextError,
            "Não foi possível carregar as anotações verificadas.",
          ),
        );
      })
      .finally(() => {
        if (sequence === annotationSequence.current) {
          setLoadingAnnotations(false);
        }
      });

    return () => {
      annotationSequence.current += 1;
    };
  }, [
    datasetVersion,
    rangeEnd,
    rangeStart,
    selection.baseAsset,
    selection.quoteAsset,
    selection.sessionId,
    selection.timeframe,
  ]);

  const loadHistory = async () => {
    if (
      !data?.hasMoreBefore ||
      !data.nextBefore ||
      data.items.length >= MAX_LOADED_CANDLES
    ) {
      return;
    }

    setLoadingHistory(true);
    try {
      const page = await apiClient.getMarketCandles(
        selection.baseAsset,
        selection.quoteAsset,
        {
          timeframe: selection.timeframe,
          before: data.nextBefore,
          limit: Math.min(PAGE_LIMIT, MAX_LOADED_CANDLES - data.items.length),
        },
      );
      if (data.datasetVersion !== page.dataset_version) {
        throw new Error(
          "O dataset mudou durante a paginação. Atualize o gráfico.",
        );
      }
      setData((current) =>
        current
          ? {
              ...current,
              items: mergeCandles(page.items, current.items),
              nextBefore: page.next_before,
              hasMoreBefore: page.has_more_before,
              contentChecksum: page.content_checksum,
            }
          : fromPage(page),
      );
      setError(null);
    } catch (nextError) {
      setError(
        getErrorMessage(
          nextError,
          "Não foi possível carregar o histórico anterior.",
        ),
      );
    } finally {
      setLoadingHistory(false);
    }
  };

  const applySelection = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const next = {
      sessionId: draft.sessionId.trim(),
      baseAsset: normalizedAsset(draft.baseAsset),
      quoteAsset: normalizedAsset(draft.quoteAsset),
      timeframe: draft.timeframe,
    };
    if (!next.baseAsset || !next.quoteAsset) {
      setError("Informe os ativos base e de cotação.");
      return;
    }
    if (next.sessionId && !SESSION_ID.test(next.sessionId)) {
      setError("A sessão deve ser um SHA-256 minúsculo de 64 caracteres.");
      return;
    }
    const params: Record<string, string> = {
      base: next.baseAsset,
      quote: next.quoteAsset,
      timeframe: next.timeframe,
    };
    if (next.sessionId) params.session_id = next.sessionId;
    if (selectedTradeId) params.trade_id = selectedTradeId;
    setSearchParams(params);
    setSelection(next);
  };

  const chooseSession = (sessionId: string) => {
    const session = sessions.find((item) => item.session_id === sessionId);
    setDraft((current) =>
      session
        ? {
            sessionId,
            baseAsset: session.base_asset,
            quoteAsset: session.quote_asset,
            timeframe: session.timeframe,
          }
        : { ...current, sessionId: "" },
    );
  };

  const lastCandle =
    data && data.items.length > 0 ? data.items[data.items.length - 1] : null;
  const reachedLimit = (data?.items.length ?? 0) >= MAX_LOADED_CANDLES;
  const fastPeriod = selection.sessionId
    ? (annotations?.ema_fast_period ?? null)
    : 9;
  const slowPeriod = selection.sessionId
    ? (annotations?.ema_slow_period ?? null)
    : 21;
  const textualAnnotations = accessibleAnnotations(annotations);
  const protectiveStops =
    annotations?.orders.filter((order) => order.is_engine_protective_stop) ??
    [];
  const selectedSessionKnown = sessions.some(
    (session) => session.session_id === draft.sessionId,
  );

  return (
    <div>
      <div className="page-heading instrument-chart-heading">
        <div>
          <p className="eyebrow">Projeção visual read-only</p>
          <h1>Gráfico de mercado</h1>
          <p>
            Candles RAW fechados e eventos verificados de uma sessão de paper
            trading.
          </p>
        </div>
        <div className="instrument-chart-actions">
          <small>
            {updatedAt
              ? `Atualizado às ${updatedAt.toLocaleTimeString("pt-BR")}`
              : "Aguardando atualização"}
          </small>
          <button
            className="button button--ghost"
            type="button"
            disabled={refreshing}
            onClick={() => void loadLatest(false)}
          >
            {refreshing ? "Atualizando…" : "Atualizar"}
          </button>
        </div>
      </div>

      <form
        className="instrument-chart-form"
        aria-label="Seleção do instrumento e sessão"
        onSubmit={applySelection}
      >
        <label className="instrument-chart-form__session">
          Sessão de paper trading
          <select
            value={selectedSessionKnown ? draft.sessionId : ""}
            onChange={(event) => chooseSession(event.target.value)}
          >
            <option value="">Somente instrumento</option>
            {sessions.map((session) => (
              <option key={session.session_id} value={session.session_id}>
                {sessionOptionLabel(session)}
              </option>
            ))}
          </select>
        </label>
        <label>
          ID da sessão
          <input
            value={draft.sessionId}
            maxLength={64}
            placeholder="Opcional · SHA-256"
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                sessionId: event.target.value.trim(),
              }))
            }
          />
        </label>
        <label>
          Ativo base
          <input
            value={draft.baseAsset}
            maxLength={32}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                baseAsset: event.target.value,
              }))
            }
          />
        </label>
        <label>
          Ativo de cotação
          <input
            value={draft.quoteAsset}
            maxLength={32}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                quoteAsset: event.target.value,
              }))
            }
          />
        </label>
        <label>
          Timeframe
          <select
            value={draft.timeframe}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                timeframe: event.target.value,
              }))
            }
          >
            {TIMEFRAMES.map((timeframe) => (
              <option key={timeframe} value={timeframe}>
                {timeframe}
              </option>
            ))}
          </select>
        </label>
        <label>
          Limite local
          <input value={MAX_LOADED_CANDLES} readOnly />
        </label>
        <div className="instrument-chart-form__actions">
          <button className="button" type="submit">
            Abrir seleção
          </button>
          <button
            className="button button--ghost"
            type="button"
            disabled={!data?.hasMoreBefore || loadingHistory || reachedLimit}
            onClick={() => void loadHistory()}
          >
            {loadingHistory ? "Carregando…" : "Carregar histórico anterior"}
          </button>
          <button
            className="button button--ghost"
            type="button"
            disabled={!data?.items.length}
            onClick={() => setResetKey((value) => value + 1)}
          >
            Ajustar visualização
          </button>
        </div>
      </form>

      {sessionOptionsError && <InlineError message={sessionOptionsError} />}
      {error && <InlineError message={error} />}
      {annotationError && <InlineError message={annotationError} />}

      {loading ? (
        <LoadingState message="Carregando candles persistidos…" />
      ) : !data || data.items.length === 0 ? (
        <EmptyState
          title="Nenhum candle disponível"
          description="O catálogo local não possui candles fechados para este instrumento e timeframe."
        />
      ) : (
        <>
          <section
            className="instrument-chart-meta"
            aria-label="Metadados do dataset"
          >
            <div>
              <span>Instrumento</span>
              <strong>
                {selection.baseAsset}/{selection.quoteAsset}
              </strong>
            </div>
            <div>
              <span>Timeframe</span>
              <strong>{selection.timeframe} · UTC</strong>
            </div>
            <div>
              <span>Candles carregados</span>
              <strong>
                {data.items.length}/{MAX_LOADED_CANDLES}
              </strong>
            </div>
            <div>
              <span>Dataset</span>
              <code>{data.datasetVersion.slice(0, 16)}</code>
            </div>
          </section>

          {selection.sessionId && (
            <section
              className="instrument-chart-session"
              aria-label="Sessão selecionada"
            >
              <div>
                <span>Sessão</span>
                <code>{selection.sessionId}</code>
              </div>
              <div>
                <span>Estratégia</span>
                <strong>
                  {annotations
                    ? `${annotations.strategy_name}@${annotations.strategy_version}`
                    : loadingAnnotations
                      ? "Carregando…"
                      : "Não verificada"}
                </strong>
              </div>
              <div>
                <span>Estado</span>
                <code>{annotations?.state_checksum?.slice(0, 16) ?? "—"}</code>
              </div>
              <div className="instrument-chart-session__links">
                <Link
                  className="button button--ghost"
                  to={`/admin/paper-trading/journal?session_id=${selection.sessionId}${selectedTradeId ? `&trade_id=${selectedTradeId}` : ""}`}
                >
                  Abrir journal da sessão
                </Link>
              </div>
            </section>
          )}

          <FinancialCandlestickChart
            candles={data.items}
            fastPeriod={fastPeriod}
            slowPeriod={slowPeriod}
            annotations={annotations}
            selectedTradeId={selectedTradeId}
            resetKey={resetKey}
          />

          {lastCandle && (
            <section
              className="instrument-chart-summary"
              aria-label="Resumo textual do último candle"
            >
              <div>
                <span>Abertura UTC</span>
                <strong>{formatDate(lastCandle.open_time)}</strong>
              </div>
              <div>
                <span>Open</span>
                <strong>{formatExact(lastCandle.open)}</strong>
              </div>
              <div>
                <span>High</span>
                <strong>{formatExact(lastCandle.high)}</strong>
              </div>
              <div>
                <span>Low</span>
                <strong>{formatExact(lastCandle.low)}</strong>
              </div>
              <div>
                <span>Close</span>
                <strong>{formatExact(lastCandle.close)}</strong>
              </div>
            </section>
          )}

          {selection.sessionId && (
            <section
              className="instrument-chart-annotations"
              aria-labelledby="instrument-chart-annotations-title"
            >
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Representação textual acessível</p>
                  <h2 id="instrument-chart-annotations-title">
                    Eventos da sessão
                  </h2>
                </div>
                <span>
                  {annotations
                    ? `${annotations.fills_count} fills · ${protectiveStops.length} stops`
                    : loadingAnnotations
                      ? "Carregando eventos…"
                      : "Sem eventos verificados"}
                </span>
              </div>

              {textualAnnotations.length > 0 ? (
                <>
                  <ol className="instrument-chart-annotation-list">
                    {textualAnnotations.map((item) => (
                      <li
                        key={item.id}
                        className={
                          item.tradeId === selectedTradeId
                            ? "instrument-chart-annotation instrument-chart-annotation--selected"
                            : "instrument-chart-annotation"
                        }
                      >
                        <div>
                          <strong>{item.label}</strong>
                          <span>{item.detail}</span>
                        </div>
                        <time dateTime={item.eventTime}>
                          {formatDate(item.eventTime)}
                        </time>
                      </li>
                    ))}
                  </ol>
                  {annotations &&
                    annotations.count > ACCESSIBLE_ANNOTATION_LIMIT && (
                      <small>
                        Mostrando os {ACCESSIBLE_ANNOTATION_LIMIT} eventos mais
                        recentes de {annotations.count}.
                      </small>
                    )}
                </>
              ) : (
                <p className="instrument-chart-annotations__empty">
                  A sessão não possui entradas, saídas ou stops dentro do
                  intervalo carregado.
                </p>
              )}
            </section>
          )}

          <p className="instrument-chart-notice">
            <strong>Semântica:</strong> candles, eventos e checksums vêm das
            APIs verificadas. As linhas EMA são projeções visuais em IEEE-754;
            quando uma sessão EMA é selecionada, os períodos vêm do contrato
            persistido da estratégia. O gráfico mantém a atribuição da
            TradingView.
            {reachedLimit ? " O teto local de 5.000 candles foi atingido." : ""}
          </p>
        </>
      )}
    </div>
  );
}
