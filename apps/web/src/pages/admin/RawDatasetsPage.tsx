import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { EmptyState, InlineError, LoadingState } from "../../components/States";
import { apiClient } from "../../http/client";
import type {
  RawDatasetPageResponse,
  RawDatasetResponse,
} from "../../types/api";
import { getErrorMessage } from "../../utils/format";

const PAGE_SIZE = 25;

function formatUtc(value: string | null): string {
  if (!value) return "—";

  return new Intl.DateTimeFormat("pt-BR", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(new Date(value));
}

function shortVersion(value: string): string {
  if (value.length <= 24) return value;
  return `${value.slice(0, 12)}…${value.slice(-8)}`;
}

function integritySummary(dataset: RawDatasetResponse): string {
  if (!dataset.integrity.present) return "Manifesto não disponível";

  return `${dataset.integrity.partition_count} ${
    dataset.integrity.partition_count === 1 ? "partição" : "partições"
  }`;
}

export function RawDatasetsPage() {
  const [page, setPage] = useState(1);
  const [pageData, setPageData] = useState<RawDatasetPageResponse | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const [symbolInput, setSymbolInput] = useState("");
  const [timeframeInput, setTimeframeInput] = useState("");
  const [symbolFilter, setSymbolFilter] = useState("");
  const [timeframeFilter, setTimeframeFilter] = useState("");

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RawDatasetResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const mountedRef = useRef(true);
  const listSequenceRef = useRef(0);
  const detailSequenceRef = useRef(0);
  const selectedIdRef = useRef<string | null>(null);

  useEffect(() => {
    return () => {
      mountedRef.current = false;
      listSequenceRef.current += 1;
      detailSequenceRef.current += 1;
    };
  }, []);

  const loadList = useCallback(async () => {
    const sequence = ++listSequenceRef.current;
    setListLoading(true);
    setListError(null);

    try {
      const response = await apiClient.listRawDatasets({
        page,
        pageSize: PAGE_SIZE,
        symbol: symbolFilter || undefined,
        timeframe: timeframeFilter || undefined,
      });

      if (!mountedRef.current || sequence !== listSequenceRef.current) return;

      setPageData(response);

      if (
        selectedIdRef.current &&
        !response.items.some(
          (dataset) => dataset.dataset_id === selectedIdRef.current,
        )
      ) {
        selectedIdRef.current = null;
        detailSequenceRef.current += 1;
        setSelectedId(null);
        setDetail(null);
        setDetailError(null);
      }
    } catch (error) {
      if (!mountedRef.current || sequence !== listSequenceRef.current) return;

      setListError(
        getErrorMessage(
          error,
          "Não foi possível carregar os datasets RAW persistidos.",
        ),
      );
    } finally {
      if (mountedRef.current && sequence === listSequenceRef.current) {
        setListLoading(false);
      }
    }
  }, [page, symbolFilter, timeframeFilter]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  const loadDetail = useCallback(async (datasetId: string) => {
    const sequence = ++detailSequenceRef.current;
    setDetailLoading(true);
    setDetailError(null);

    try {
      const response = await apiClient.getRawDataset(datasetId);

      if (
        !mountedRef.current ||
        sequence !== detailSequenceRef.current ||
        selectedIdRef.current !== datasetId
      ) {
        return;
      }

      setDetail(response);
    } catch (error) {
      if (
        !mountedRef.current ||
        sequence !== detailSequenceRef.current ||
        selectedIdRef.current !== datasetId
      ) {
        return;
      }

      setDetailError(
        getErrorMessage(
          error,
          "Não foi possível carregar o detalhe do dataset RAW.",
        ),
      );
    } finally {
      if (
        mountedRef.current &&
        sequence === detailSequenceRef.current &&
        selectedIdRef.current === datasetId
      ) {
        setDetailLoading(false);
      }
    }
  }, []);

  const selectDataset = (dataset: RawDatasetResponse) => {
    selectedIdRef.current = dataset.dataset_id;
    detailSequenceRef.current += 1;

    setSelectedId(dataset.dataset_id);
    setDetail(dataset);
    setDetailError(null);

    void loadDetail(dataset.dataset_id);
  };

  const applyFilters = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    selectedIdRef.current = null;
    detailSequenceRef.current += 1;
    setSelectedId(null);
    setDetail(null);
    setDetailError(null);

    setPage(1);
    setSymbolFilter(symbolInput.trim());
    setTimeframeFilter(timeframeInput.trim());
  };

  const clearFilters = () => {
    selectedIdRef.current = null;
    detailSequenceRef.current += 1;

    setSymbolInput("");
    setTimeframeInput("");
    setSymbolFilter("");
    setTimeframeFilter("");
    setPage(1);
    setSelectedId(null);
    setDetail(null);
    setDetailError(null);
  };

  const totalPages = pageData?.total_pages ?? 0;

  return (
    <div className="market-operations-page raw-datasets-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Phase 7 · Persisted market data</p>
          <h1>Datasets RAW</h1>
          <p>
            Consulte o catálogo RAW persistido em modo somente leitura. A API
            expõe somente metadados sanitizados e identificadores canônicos.
          </p>
        </div>

        <button
          className="button button--ghost"
          type="button"
          onClick={() => void loadList()}
          disabled={listLoading}
        >
          {listLoading ? "Atualizando…" : "Atualizar catálogo"}
        </button>
      </div>

      <section aria-labelledby="raw-datasets-title">
        <div className="section-heading">
          <h2 id="raw-datasets-title">Catálogo persistido</h2>
          <span>Paginação limitada a {PAGE_SIZE} datasets</span>
        </div>

        <form className="operation-target-search" onSubmit={applyFilters}>
          <label>
            Símbolo
            <input
              value={symbolInput}
              onChange={(event) => setSymbolInput(event.target.value)}
              placeholder="BTC/USDT"
              autoComplete="off"
            />
          </label>

          <label>
            Timeframe
            <input
              value={timeframeInput}
              onChange={(event) => setTimeframeInput(event.target.value)}
              placeholder="1h"
              autoComplete="off"
            />
          </label>

          <button className="button" type="submit">
            Filtrar
          </button>

          <button
            className="button button--ghost"
            type="button"
            onClick={clearFilters}
          >
            Limpar
          </button>
        </form>

        {listLoading && !pageData ? (
          <LoadingState message="Carregando datasets RAW…" />
        ) : listError ? (
          <InlineError message={listError} />
        ) : !pageData || pageData.items.length === 0 ? (
          <EmptyState
            title="Nenhum dataset RAW encontrado"
            description="O catálogo não contém datasets compatíveis com os filtros atuais."
          />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Dataset</th>
                  <th>Cobertura UTC</th>
                  <th>Candles</th>
                  <th>Integridade</th>
                  <th>Versão</th>
                  <th>Atualizado</th>
                </tr>
              </thead>

              <tbody>
                {pageData.items.map((dataset) => (
                  <tr key={dataset.dataset_id}>
                    <td>
                      <button
                        className="operation-select"
                        type="button"
                        onClick={() => selectDataset(dataset)}
                        aria-label={`Inspecionar dataset ${dataset.symbol} ${dataset.timeframe}`}
                      >
                        {dataset.symbol}
                      </button>
                      <small>
                        {dataset.exchange} · {dataset.market_type} ·{" "}
                        {dataset.timeframe}
                      </small>
                    </td>

                    <td>
                      {formatUtc(dataset.coverage_start)}
                      <small>até {formatUtc(dataset.coverage_end)}</small>
                    </td>

                    <td>{dataset.candle_count.toLocaleString("pt-BR")}</td>

                    <td>{integritySummary(dataset)}</td>

                    <td title={dataset.version}>
                      {shortVersion(dataset.version)}
                    </td>

                    <td>{formatUtc(dataset.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div
          className="operation-pagination"
          aria-label="Paginação de datasets RAW"
        >
          <button
            className="button button--ghost button--compact"
            type="button"
            disabled={page <= 1 || listLoading}
            onClick={() => setPage((value) => Math.max(1, value - 1))}
          >
            Anterior
          </button>

          <span>
            {totalPages === 0
              ? "Nenhuma página"
              : `Página ${page} de ${totalPages}`}
          </span>

          <button
            className="button button--ghost button--compact"
            type="button"
            disabled={totalPages === 0 || page >= totalPages || listLoading}
            onClick={() => setPage((value) => value + 1)}
          >
            Próxima
          </button>
        </div>
      </section>

      {selectedId && (
        <section
          className="panel operation-detail"
          aria-labelledby="raw-dataset-detail-title"
        >
          <div className="section-heading">
            <h2 id="raw-dataset-detail-title">Detalhe do dataset</h2>

            <button
              className="button button--ghost button--compact"
              type="button"
              disabled={detailLoading}
              onClick={() => void loadDetail(selectedId)}
            >
              {detailLoading ? "Atualizando…" : "Atualizar detalhe"}
            </button>
          </div>

          {detailLoading && !detail ? (
            <LoadingState message="Carregando detalhe do dataset…" />
          ) : detailError ? (
            <InlineError message={detailError} />
          ) : detail ? (
            <>
              <div className="operation-detail__heading">
                <div>
                  <p className="eyebrow">RAW persistido</p>
                  <h3>
                    {detail.symbol} · {detail.timeframe}
                  </h3>
                  <small>ID {detail.dataset_id}</small>
                </div>

                <strong title={detail.version}>
                  {shortVersion(detail.version)}
                </strong>
              </div>

              <dl className="operation-detail-grid">
                <div>
                  <dt>Identidade</dt>
                  <dd>
                    {detail.exchange} · {detail.market_type}
                    <small>
                      {detail.base_asset} / {detail.quote_asset}
                    </small>
                  </dd>
                </div>

                <div>
                  <dt>Cobertura UTC</dt>
                  <dd>
                    {formatUtc(detail.coverage_start)} →{" "}
                    {formatUtc(detail.coverage_end)}
                  </dd>
                </div>

                <div>
                  <dt>Primeiro / último open time</dt>
                  <dd>
                    {formatUtc(detail.first_open_time)} /{" "}
                    {formatUtc(detail.last_open_time)}
                  </dd>
                </div>

                <div>
                  <dt>Candles persistidos</dt>
                  <dd>{detail.candle_count.toLocaleString("pt-BR")}</dd>
                </div>

                <div>
                  <dt>Versão lógica</dt>
                  <dd title={detail.version}>
                    {shortVersion(detail.version)}
                    <small>{detail.version_algorithm}</small>
                  </dd>
                </div>

                <div>
                  <dt>Integridade</dt>
                  <dd>
                    {integritySummary(detail)}
                    <small>
                      {detail.integrity.present
                        ? `schema ${detail.integrity.schema_version ?? "—"} · ${
                            detail.integrity.checksum_algorithm ?? "—"
                          }`
                        : "Resumo de manifesto não disponível"}
                    </small>
                  </dd>
                </div>

                <div>
                  <dt>Catálogo atualizado</dt>
                  <dd>{formatUtc(detail.updated_at)}</dd>
                </div>
              </dl>

              <p className="operation-notice" role="status">
                Esta superfície é somente leitura. Caminhos de armazenamento e
                entradas físicas de partição não são expostos pelo contrato
                administrativo.
              </p>
            </>
          ) : null}
        </section>
      )}
    </div>
  );
}
