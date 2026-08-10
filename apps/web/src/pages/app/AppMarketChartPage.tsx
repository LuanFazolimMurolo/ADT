import {
  FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { FinancialCandlestickChart } from "../../components/FinancialCandlestickChart";
import { EmptyState, InlineError, LoadingState } from "../../components/States";
import { apiClient } from "../../http/client";
import type { MarketCandle, MarketCandlePageResponse } from "../../types/api";

export const PAGE_LIMIT = 1_000;
export const MAX_LOADED_CANDLES = 5_000;
const POLL_INTERVAL_MS = 30_000;
const TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"] as const;
const ASSET_PATTERN = String.raw`\s*[A-Za-z0-9][A-Za-z0-9._\-]{0,31}\s*`;
const SAFE_ASSET_CODE = /^[A-Z0-9][A-Z0-9._-]{0,31}$/;
const SAFE_LOAD_ERROR =
  "Não foi possível carregar os candles locais. Tente atualizar o gráfico.";
const DATASET_MISMATCH_ERROR =
  "O dataset mudou durante a paginação. Atualize o gráfico antes de carregar mais histórico.";

interface InstrumentSelection {
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

const DEFAULT_SELECTION: InstrumentSelection = {
  baseAsset: "BTC",
  quoteAsset: "USDT",
  timeframe: "15m",
};

function normalizedAsset(value: string): string {
  return value.trim().toUpperCase();
}

function fromPage(page: MarketCandlePageResponse): LoadedChartData {
  return {
    items: page.items.slice(-MAX_LOADED_CANDLES),
    datasetVersion: page.dataset_version,
    contentChecksum: page.content_checksum,
    nextBefore: page.next_before,
    hasMoreBefore: page.has_more_before,
    availableStart: page.available_start,
    availableEnd: page.available_end,
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

function formatUtc(value: string): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "Horário UTC indisponível";
  return `${date.toISOString()} UTC`;
}

function abbreviatedDigest(value: string): string {
  if (value.length <= 24) return value;
  return `${value.slice(0, 12)}…${value.slice(-8)}`;
}

export function AppMarketChartPage() {
  const [draft, setDraft] = useState<InstrumentSelection>(DEFAULT_SELECTION);
  const [selection, setSelection] =
    useState<InstrumentSelection>(DEFAULT_SELECTION);
  const [data, setData] = useState<LoadedChartData | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resetKey, setResetKey] = useState(0);
  const generation = useRef(0);
  const latestRequest = useRef(0);
  const historyRequest = useRef(0);
  const dataRef = useRef<LoadedChartData | null>(null);

  const loadLatest = useCallback(
    async (mode: "reset" | "poll") => {
      const requestGeneration = generation.current;
      const requestId = ++latestRequest.current;
      if (mode === "reset") {
        historyRequest.current += 1;
        setRefreshing(dataRef.current !== null);
      }

      try {
        const page = await apiClient.getAppMarketCandles(
          selection.baseAsset,
          selection.quoteAsset,
          { timeframe: selection.timeframe, limit: PAGE_LIMIT },
        );
        if (
          generation.current !== requestGeneration ||
          latestRequest.current !== requestId
        )
          return;

        const current = dataRef.current;
        let next: LoadedChartData;
        if (
          mode === "poll" &&
          current !== null &&
          current.datasetVersion === page.dataset_version
        ) {
          next = {
            ...current,
            items: mergeCandles(current.items, page.items),
            contentChecksum: page.content_checksum,
            availableStart: page.available_start,
            availableEnd: page.available_end,
          };
        } else {
          next = fromPage(page);
          setResetKey((value) => value + 1);
        }
        dataRef.current = next;
        setData(next);
        setError(null);
      } catch {
        if (
          generation.current === requestGeneration &&
          latestRequest.current === requestId
        ) {
          setError(SAFE_LOAD_ERROR);
        }
      } finally {
        if (
          generation.current === requestGeneration &&
          latestRequest.current === requestId
        ) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [selection],
  );

  useEffect(() => {
    generation.current += 1;
    dataRef.current = null;
    setData(null);
    setError(null);
    setLoading(true);
    setLoadingHistory(false);
    void loadLatest("reset");
    const poller = window.setInterval(() => {
      void loadLatest("poll");
    }, POLL_INTERVAL_MS);

    return () => {
      generation.current += 1;
      latestRequest.current += 1;
      historyRequest.current += 1;
      window.clearInterval(poller);
    };
  }, [loadLatest]);

  function applySelection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = {
      baseAsset: normalizedAsset(draft.baseAsset),
      quoteAsset: normalizedAsset(draft.quoteAsset),
      timeframe: draft.timeframe,
    };
    if (
      !SAFE_ASSET_CODE.test(next.baseAsset) ||
      !SAFE_ASSET_CODE.test(next.quoteAsset)
    ) {
      setError("Informe códigos de ativo válidos, com até 32 caracteres.");
      return;
    }
    setDraft(next);
    setSelection(next);
  }

  async function loadHistory() {
    const snapshot = dataRef.current;
    if (
      snapshot === null ||
      snapshot.nextBefore === null ||
      !snapshot.hasMoreBefore ||
      snapshot.items.length >= MAX_LOADED_CANDLES
    )
      return;

    const requestGeneration = generation.current;
    const requestId = ++historyRequest.current;
    setLoadingHistory(true);
    setError(null);
    try {
      const remaining = MAX_LOADED_CANDLES - snapshot.items.length;
      const page = await apiClient.getAppMarketCandles(
        selection.baseAsset,
        selection.quoteAsset,
        {
          timeframe: selection.timeframe,
          before: snapshot.nextBefore,
          limit: Math.min(PAGE_LIMIT, remaining),
        },
      );
      if (
        generation.current !== requestGeneration ||
        historyRequest.current !== requestId
      )
        return;

      const current = dataRef.current;
      if (
        page.dataset_version !== snapshot.datasetVersion ||
        current === null ||
        current.datasetVersion !== snapshot.datasetVersion
      ) {
        setError(DATASET_MISMATCH_ERROR);
        return;
      }

      const next: LoadedChartData = {
        ...current,
        items: mergeCandles(current.items, page.items),
        contentChecksum: page.content_checksum,
        nextBefore: page.next_before,
        hasMoreBefore: page.has_more_before,
        availableStart: page.available_start,
        availableEnd: page.available_end,
      };
      dataRef.current = next;
      setData(next);
    } catch {
      if (
        generation.current === requestGeneration &&
        historyRequest.current === requestId
      ) {
        setError(SAFE_LOAD_ERROR);
      }
    } finally {
      if (
        generation.current === requestGeneration &&
        historyRequest.current === requestId
      ) {
        setLoadingHistory(false);
      }
    }
  }

  const lastCandle = data?.items.at(-1) ?? null;
  const atBrowserLimit = (data?.items.length ?? 0) >= MAX_LOADED_CANDLES;

  return (
    <section className="page-stack" aria-labelledby="app-market-title">
      <header className="page-heading instrument-chart-heading">
        <div>
          <p className="eyebrow">Mercado local verificado</p>
          <h1 id="app-market-title">Gráfico de mercado</h1>
          <p>
            Candles RAW fechados e persistidos, disponíveis somente após
            autenticação.
          </p>
        </div>
        <div className="instrument-chart-actions">
          <button
            className="button button--ghost button--compact"
            type="button"
            onClick={() => void loadLatest("reset")}
            disabled={loading || refreshing}
            aria-label="Atualizar gráfico de mercado"
          >
            {refreshing ? "Atualizando…" : "Atualizar"}
          </button>
        </div>
      </header>

      <form className="instrument-chart-form" onSubmit={applySelection}>
        <label>
          Ativo base
          <input
            name="base_asset"
            value={draft.baseAsset}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                baseAsset: event.target.value,
              }))
            }
            maxLength={32}
            pattern={ASSET_PATTERN}
            autoComplete="off"
            required
          />
        </label>
        <label>
          Ativo de cotação
          <input
            name="quote_asset"
            value={draft.quoteAsset}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                quoteAsset: event.target.value,
              }))
            }
            maxLength={32}
            pattern={ASSET_PATTERN}
            autoComplete="off"
            required
          />
        </label>
        <label>
          Timeframe
          <select
            name="timeframe"
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
        <div className="instrument-chart-form__actions">
          <button className="button button--primary" type="submit">
            Aplicar seleção
          </button>
        </div>
      </form>

      {error && <InlineError message={error} />}
      {loading && data === null ? (
        <LoadingState message="Carregando candles locais…" />
      ) : data === null || data.items.length === 0 ? (
        <EmptyState
          title="Nenhum candle disponível"
          description="Não há candles locais para a seleção informada."
        />
      ) : (
        <>
          <div className="instrument-chart-meta" aria-label="Proveniência do dataset">
            <div>
              <span>Instrumento</span>
              <strong>{`${selection.baseAsset}/${selection.quoteAsset}`}</strong>
            </div>
            <div>
              <span>Timeframe</span>
              <strong>{selection.timeframe}</strong>
            </div>
            <div>
              <span>Intervalo carregado (UTC)</span>
              <strong>
                {formatUtc(data.items[0].open_time)} até{" "}
                {formatUtc(data.items.at(-1)?.close_time ?? data.items[0].close_time)}
              </strong>
            </div>
            <div>
              <span>Candles carregados</span>
              <strong>{data.items.length}</strong>
            </div>
            <div>
              <span>Dataset version</span>
              <code title={data.datasetVersion}>
                {abbreviatedDigest(data.datasetVersion)}
              </code>
            </div>
            <div>
              <span>Checksum da página consultada</span>
              <code title={data.contentChecksum}>
                {abbreviatedDigest(data.contentChecksum)}
              </code>
            </div>
            <div>
              <span>Disponibilidade local (UTC)</span>
              <strong>
                {formatUtc(data.availableStart)} até {formatUtc(data.availableEnd)}
              </strong>
            </div>
          </div>

          <FinancialCandlestickChart
            candles={data.items}
            fastPeriod={null}
            slowPeriod={null}
            annotations={null}
            selectedTradeId={null}
            resetKey={resetKey}
          />

          {lastCandle && (
            <div className="instrument-chart-summary" aria-label="Último candle carregado">
              <div>
                <span>Horário (UTC)</span>
                <strong>{formatUtc(lastCandle.close_time)}</strong>
              </div>
              <div>
                <span>Open</span>
                <strong>{lastCandle.open}</strong>
              </div>
              <div>
                <span>High</span>
                <strong>{lastCandle.high}</strong>
              </div>
              <div>
                <span>Low</span>
                <strong>{lastCandle.low}</strong>
              </div>
              <div>
                <span>Close</span>
                <strong>{lastCandle.close}</strong>
              </div>
              <div>
                <span>Volume</span>
                <strong>{lastCandle.volume}</strong>
              </div>
            </div>
          )}

          <div className="instrument-chart-actions">
            <button
              className="button button--ghost"
              type="button"
              onClick={() => void loadHistory()}
              disabled={
                loadingHistory ||
                !data.hasMoreBefore ||
                data.nextBefore === null ||
                atBrowserLimit
              }
            >
              {loadingHistory ? "Carregando histórico…" : "Carregar histórico anterior"}
            </button>
            {atBrowserLimit && (
              <small>Limite local de {MAX_LOADED_CANDLES} candles atingido.</small>
            )}
          </div>
        </>
      )}
    </section>
  );
}
