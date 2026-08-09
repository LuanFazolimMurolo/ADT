import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { FinancialCandlestickChart } from "../../components/FinancialCandlestickChart";
import { EmptyState, InlineError, LoadingState } from "../../components/States";
import { Pagination } from "../../components/Pagination";
import { ApiError, apiClient } from "../../http/client";
import type {
  AppPaperChartAnnotationPage,
  AppPaperSessionDetail,
  AppPaperTradePage,
  MarketCandlePageResponse,
  PageMeta,
} from "../../types/api";

const CHART_LIMIT = 5_000;
const TRADE_PAGE_SIZE = 20;
const ACCESS_DENIED = "Esta conta não possui acesso a esta sessão.";
const NOT_FOUND = "A sessão solicitada não foi encontrada.";
const SAFE_DETAIL_ERROR =
  "Não foi possível carregar esta sessão. Tente novamente.";
const DATASET_MISMATCH =
  "Os candles e eventos pertencem a datasets incompatíveis. Atualize a sessão.";

function formatUtc(value: string | null): string {
  if (value === null) return "—";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "Horário UTC indisponível";
  return `${date.toISOString()} UTC`;
}

function annotationBefore(lastClose: string): string {
  const milliseconds = Date.parse(lastClose);
  if (!Number.isFinite(milliseconds)) throw new Error("invalid candle range");
  return new Date(milliseconds + 1).toISOString();
}

function detailError(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) return ACCESS_DENIED;
  if (error instanceof ApiError && error.status === 404) return NOT_FOUND;
  return SAFE_DETAIL_ERROR;
}

export function AppPaperSessionDetailPage() {
  const { sessionId = "" } = useParams();
  const [detail, setDetail] = useState<AppPaperSessionDetail | null>(null);
  const [candles, setCandles] = useState<MarketCandlePageResponse | null>(null);
  const [annotations, setAnnotations] =
    useState<AppPaperChartAnnotationPage | null>(null);
  const [trades, setTrades] = useState<AppPaperTradePage | null>(null);
  const [tradePage, setTradePage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [tradesLoading, setTradesLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tradesError, setTradesError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const mainSequence = useRef(0);
  const tradeSequence = useRef(0);

  const loadSession = useCallback(async () => {
    const sequence = ++mainSequence.current;
    setLoading(true);
    setDetail(null);
    setCandles(null);
    setAnnotations(null);
    setError(null);
    setTradePage(1);
    try {
      const nextDetail = await apiClient.getAppPaperSession(sessionId);
      if (sequence !== mainSequence.current) return;
      setDetail(nextDetail);

      const nextCandles = await apiClient.getAppMarketCandles(
        nextDetail.base_asset,
        nextDetail.quote_asset,
        { timeframe: nextDetail.timeframe, limit: CHART_LIMIT },
      );
      if (sequence !== mainSequence.current) return;

      const firstCandle = nextCandles.items[0];
      const lastCandle = nextCandles.items.at(-1);
      let nextAnnotations: AppPaperChartAnnotationPage | null = null;
      if (firstCandle && lastCandle) {
        nextAnnotations = await apiClient.getAppPaperChartAnnotations(
          sessionId,
          {
            start: firstCandle.open_time,
            before: annotationBefore(lastCandle.close_time),
            limit: CHART_LIMIT,
          },
        );
        if (sequence !== mainSequence.current) return;
        const identityMismatch =
          nextAnnotations.session_id !== nextDetail.session_id ||
          nextAnnotations.base_asset !== nextDetail.base_asset ||
          nextAnnotations.quote_asset !== nextDetail.quote_asset ||
          nextAnnotations.timeframe !== nextDetail.timeframe;
        const datasetMismatch =
          nextAnnotations.state_available &&
          nextAnnotations.dataset_version !== nextCandles.dataset_version;
        if (identityMismatch || datasetMismatch)
          throw new Error(DATASET_MISMATCH);
      }

      setCandles(nextCandles);
      setAnnotations(nextAnnotations);
    } catch (nextError) {
      if (sequence !== mainSequence.current) return;
      setCandles(null);
      setAnnotations(null);
      setError(
        nextError instanceof Error && nextError.message === DATASET_MISMATCH
          ? DATASET_MISMATCH
          : detailError(nextError),
      );
    } finally {
      if (sequence === mainSequence.current) setLoading(false);
    }
  }, [sessionId, revision]);

  useEffect(() => {
    void loadSession();
    return () => {
      mainSequence.current += 1;
      tradeSequence.current += 1;
    };
  }, [loadSession]);

  useEffect(() => {
    if (!detail) {
      setTrades(null);
      setTradesError(null);
      return;
    }
    const sequence = ++tradeSequence.current;
    setTradesLoading(true);
    void apiClient
      .getAppPaperTrades(sessionId, {}, tradePage, TRADE_PAGE_SIZE)
      .then((result) => {
        if (sequence !== tradeSequence.current) return;
        setTrades(result);
        setTradesError(null);
      })
      .catch(() => {
        if (sequence !== tradeSequence.current) return;
        setTradesError("Não foi possível carregar os trades desta sessão.");
      })
      .finally(() => {
        if (sequence === tradeSequence.current) setTradesLoading(false);
      });
    return () => {
      tradeSequence.current += 1;
    };
  }, [detail, sessionId, tradePage]);

  const entries =
    annotations?.fills.filter((fill) => fill.role === "ENTRY") ?? [];
  const exits = annotations?.fills.filter((fill) => fill.role === "EXIT") ?? [];
  const stops =
    annotations?.orders.filter((order) => order.is_engine_protective_stop) ??
    [];
  const pagination: PageMeta | null = trades
    ? {
        page: trades.page,
        page_size: trades.page_size,
        total: trades.total,
        total_pages: trades.total_pages,
      }
    : null;

  return (
    <section className="page-stack" aria-labelledby="session-detail-title">
      <header className="page-heading instrument-chart-heading">
        <div>
          <p className="eyebrow">Sessão autorizada · read-only</p>
          <h1 id="session-detail-title">Chart e trades da sessão</h1>
          <p>Eventos e accounting autoritativos, sempre em UTC.</p>
        </div>
        <div className="instrument-chart-actions">
          <Link className="button button--ghost" to="/app/sessions">
            Voltar às sessões
          </Link>
          <Link
            className="button button--ghost"
            to={`/app/sessions/${sessionId}/performance`}
          >
            Performance
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
        <LoadingState message="Carregando sessão autorizada…" />
      ) : error ? (
        <InlineError message={error} />
      ) : detail && candles ? (
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
            <div>
              <span>Último candle UTC</span>
              <strong>{formatUtc(detail.last_candle_open_time)}</strong>
            </div>
          </section>

          {candles.items.length === 0 ? (
            <EmptyState
              title="Nenhum candle disponível"
              description="Não há candles locais fechados para esta sessão."
            />
          ) : (
            <>
              <FinancialCandlestickChart
                candles={candles.items}
                fastPeriod={null}
                slowPeriod={null}
                annotations={annotations}
                selectedTradeId={null}
                resetKey={revision}
              />
              <section
                className="instrument-chart-summary"
                aria-label="Range UTC do gráfico"
              >
                <div>
                  <span>Início UTC</span>
                  <strong>
                    {formatUtc(annotations?.range_start ?? candles.range_start)}
                  </strong>
                </div>
                <div>
                  <span>Fim UTC exclusivo</span>
                  <strong>
                    {formatUtc(annotations?.range_end ?? candles.range_end)}
                  </strong>
                </div>
                <div>
                  <span>Entradas executadas</span>
                  <strong>{entries.length}</strong>
                </div>
                <div>
                  <span>Saídas executadas</span>
                  <strong>{exits.length}</strong>
                </div>
                <div>
                  <span>Stops protetivos</span>
                  <strong>{stops.length}</strong>
                </div>
              </section>
            </>
          )}

          <section aria-labelledby="session-events-title">
            <div className="section-heading">
              <div>
                <p className="eyebrow">Representação textual acessível</p>
                <h2 id="session-events-title">Execuções e stops protetivos</h2>
              </div>
            </div>
            {annotations && annotations.count > 0 ? (
              <ol className="instrument-chart-annotation-list">
                {annotations.fills.map((fill) => (
                  <li
                    className="instrument-chart-annotation"
                    key={fill.fill_id}
                  >
                    <div>
                      <strong>
                        {fill.role === "ENTRY"
                          ? "Entrada executada"
                          : "Saída executada"}
                      </strong>
                      <span>
                        {fill.side} · {fill.quantity} @ {fill.execution_price}
                      </span>
                    </div>
                    <time dateTime={fill.event_time}>
                      {formatUtc(fill.event_time)}
                    </time>
                  </li>
                ))}
                {stops.map((order) => (
                  <li
                    className="instrument-chart-annotation"
                    key={order.order_id}
                  >
                    <div>
                      <strong>Stop protetivo</strong>
                      <span>
                        {order.status} · {order.quantity} @{" "}
                        {order.stop_price ?? "—"}
                      </span>
                    </div>
                    <time dateTime={order.created_at}>
                      {formatUtc(order.created_at)}
                    </time>
                  </li>
                ))}
              </ol>
            ) : (
              <p>Nenhuma execução ou stop protetivo neste range UTC.</p>
            )}
          </section>

          <section aria-labelledby="session-trades-title">
            <div className="section-heading">
              <div>
                <p className="eyebrow">Journal session-scoped</p>
                <h2 id="session-trades-title">Trades autorizados</h2>
              </div>
              <span>{trades?.total ?? 0} trades</span>
            </div>
            {tradesError && <InlineError message={tradesError} />}
            {tradesLoading && !trades ? (
              <LoadingState message="Carregando trades autorizados…" />
            ) : trades && trades.items.length > 0 ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Status</th>
                      <th>Abertura UTC</th>
                      <th>Fechamento UTC</th>
                      <th>Quantidade aberta / fechada / restante</th>
                      <th>Preço médio entrada / saída</th>
                      <th>PnL realizado / não realizado / líquido</th>
                      <th>Fees</th>
                      <th>Slippage</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.items.map((trade) => (
                      <tr key={trade.trade_id}>
                        <td>{trade.status}</td>
                        <td>{formatUtc(trade.opened_at)}</td>
                        <td>{formatUtc(trade.closed_at)}</td>
                        <td>
                          {trade.opened_quantity} / {trade.closed_quantity} /{" "}
                          {trade.remaining_quantity}
                        </td>
                        <td>
                          {trade.average_entry_price} /{" "}
                          {trade.average_exit_price ?? "—"}
                        </td>
                        <td>
                          {trade.realized_pnl} / {trade.unrealized_pnl} /{" "}
                          {trade.net_pnl}
                        </td>
                        <td>{trade.total_fees}</td>
                        <td>{trade.total_slippage_cost}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p>Nenhum trade registrado para esta sessão.</p>
            )}
            {pagination && (
              <Pagination pagination={pagination} onChange={setTradePage} />
            )}
          </section>
        </>
      ) : null}
    </section>
  );
}
