import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { EmptyState, InlineError, LoadingState } from "../../components/States";
import { ApiError, apiClient } from "../../http/client";
import type {
  MarketOperation,
  MarketOperationPlanPreview,
  MarketOperationState,
  MarketOperationSubmitRequest,
  MarketOperationTarget,
  MarketOperationType,
} from "../../types/api";
import { getErrorMessage } from "../../utils/format";

const POLL_INTERVAL_MS = 30_000;
const OPERATION_PAGE_SIZE = 20;
const TARGET_PAGE_SIZE = 25;

const stateLabels: Record<MarketOperationState, string> = {
  PENDING: "Pendente",
  CLAIMED: "Reivindicada",
  RUNNING: "Em execução",
  PAUSE_REQUESTED: "Pausa solicitada",
  PAUSED: "Pausada",
  CANCEL_REQUESTED: "Cancelamento solicitado",
  CANCELLED: "Cancelada",
  COMPLETED: "Concluída",
  FAILED: "Falhou",
  RECOVERING: "Em recuperação",
};

const operationLabels: Record<MarketOperationType, string> = {
  RAW_BACKFILL: "RAW backfill",
  RAW_INCREMENTAL_UPDATE: "RAW incremental",
};

const terminalStates = new Set<MarketOperationState>([
  "CANCELLED",
  "COMPLETED",
  "FAILED",
]);

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

function asUtc(value: string): string {
  return value.length === 16 ? `${value}:00Z` : value;
}

function shortChecksum(value: string): string {
  return `${value.slice(0, 12)}…${value.slice(-8)}`;
}

function progressLabel(operation: MarketOperation): string {
  const { chunks_completed: completed, chunks_planned: planned } =
    operation.progress;
  return planned > 0 ? `${completed}/${planned} chunks` : "0/0 chunks";
}

function stateClass(state: MarketOperationState): string {
  if (state === "FAILED" || state === "CANCELLED")
    return "operation-state operation-state--danger";
  if (state === "COMPLETED") return "operation-state operation-state--success";
  if (state.includes("REQUESTED") || state === "RECOVERING")
    return "operation-state operation-state--warning";
  return "operation-state";
}

function canPause(state: MarketOperationState): boolean {
  return state === "PENDING" || state === "CLAIMED" || state === "RUNNING";
}

function canResume(state: MarketOperationState): boolean {
  return state === "PAUSED";
}

function canCancel(state: MarketOperationState): boolean {
  return (
    state === "PENDING" ||
    state === "CLAIMED" ||
    state === "RUNNING" ||
    state === "PAUSED"
  );
}

function preferNewerOperation(
  current: MarketOperation,
  incoming: MarketOperation,
): MarketOperation {
  if (current.operation_id !== incoming.operation_id) return incoming;
  if (incoming.record_version > current.record_version) return incoming;
  if (incoming.record_version < current.record_version) return current;
  return Date.parse(incoming.observed_at) >= Date.parse(current.observed_at)
    ? incoming
    : current;
}

interface ConfirmedIntent {
  key: string;
  payload: MarketOperationSubmitRequest;
}

export function MarketOperationsPage() {
  const [targets, setTargets] = useState<MarketOperationTarget[]>([]);
  const [targetPage, setTargetPage] = useState(1);
  const [targetTotalPages, setTargetTotalPages] = useState(0);
  const [targetSearch, setTargetSearch] = useState("");
  const [targetSearchApplied, setTargetSearchApplied] = useState("");
  const [targetsLoading, setTargetsLoading] = useState(true);
  const [targetError, setTargetError] = useState<string | null>(null);

  const [selectedSymbol, setSelectedSymbol] = useState("");
  const [selectedTimeframe, setSelectedTimeframe] = useState("");
  const [operationType, setOperationType] =
    useState<MarketOperationType>("RAW_BACKFILL");
  const [rangeStart, setRangeStart] = useState("");
  const [rangeEnd, setRangeEnd] = useState("");
  const [incrementalStart, setIncrementalStart] = useState("");
  const [overlapCandles, setOverlapCandles] = useState(2);
  const [preview, setPreview] = useState<MarketOperationPlanPreview | null>(
    null,
  );
  const [incrementalNoop, setIncrementalNoop] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitPending, setSubmitPending] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [ambiguousSubmit, setAmbiguousSubmit] = useState(false);
  const [confirmedIntent, setConfirmedIntent] =
    useState<ConfirmedIntent | null>(null);
  const [submitSuccess, setSubmitSuccess] = useState<string | null>(null);

  const [operations, setOperations] = useState<MarketOperation[]>([]);
  const [operationOffset, setOperationOffset] = useState(0);
  const [operationHasMore, setOperationHasMore] = useState(false);
  const [stateFilter, setStateFilter] = useState<MarketOperationState | "">("");
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedOperation, setSelectedOperation] =
    useState<MarketOperation | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [controlPending, setControlPending] = useState<string | null>(null);
  const [controlError, setControlError] = useState<string | null>(null);
  const [conflictMessage, setConflictMessage] = useState<string | null>(null);

  const mountedRef = useRef(true);
  const selectedSymbolRef = useRef("");
  const targetSequenceRef = useRef(0);
  const listInFlightRef = useRef(false);
  const listSequenceRef = useRef(0);
  const detailInFlightRef = useRef(false);
  const detailSequenceRef = useRef(0);
  const selectedIdRef = useRef<string | null>(null);
  const ambiguousRetryRef = useRef<HTMLButtonElement>(null);

  const selectedTarget = useMemo(
    () => targets.find((target) => target.symbol === selectedSymbol) ?? null,
    [selectedSymbol, targets],
  );
  const selectedDatasetId = useMemo(
    () =>
      selectedTarget?.timeframes.find(
        (item) => item.timeframe === selectedTimeframe,
      )?.dataset_id ?? null,
    [selectedTarget, selectedTimeframe],
  );

  const invalidatePreview = useCallback(() => {
    setPreview(null);
    setIncrementalNoop(null);
    setPreviewError(null);
    setSubmitError(null);
    setAmbiguousSubmit(false);
    setConfirmedIntent(null);
    setSubmitSuccess(null);
    setConfirmOpen(false);
  }, []);

  const invalidateListRequest = useCallback(() => {
    listSequenceRef.current += 1;
    listInFlightRef.current = false;
  }, []);

  const invalidateDetailRequest = useCallback(() => {
    detailSequenceRef.current += 1;
    detailInFlightRef.current = false;
  }, []);

  const acceptOperationSnapshot = useCallback((incoming: MarketOperation) => {
    setOperations((current) =>
      current.map((item) =>
        item.operation_id === incoming.operation_id
          ? preferNewerOperation(item, incoming)
          : item,
      ),
    );
    setSelectedOperation((current) =>
      current?.operation_id === incoming.operation_id
        ? preferNewerOperation(current, incoming)
        : current,
    );
  }, []);

  const loadTargets = useCallback(async () => {
    const sequence = ++targetSequenceRef.current;
    setTargetsLoading(true);
    setTargetError(null);
    try {
      const response = await apiClient.listMarketOperationTargets({
        activeOnly: true,
        search: targetSearchApplied || undefined,
        page: targetPage,
        pageSize: TARGET_PAGE_SIZE,
      });
      if (!mountedRef.current || sequence !== targetSequenceRef.current) return;
      setTargets(response.items);
      setTargetTotalPages(response.total_pages);
      if (
        response.items.length > 0 &&
        !response.items.some(
          (item) => item.symbol === selectedSymbolRef.current,
        )
      ) {
        const first = response.items[0];
        selectedSymbolRef.current = first.symbol;
        setSelectedSymbol(first.symbol);
        setSelectedTimeframe(first.timeframes[0]?.timeframe ?? "");
        invalidatePreview();
      }
    } catch (error) {
      if (mountedRef.current && sequence === targetSequenceRef.current)
        setTargetError(
          getErrorMessage(error, "Não foi possível carregar os alvos."),
        );
    } finally {
      if (mountedRef.current && sequence === targetSequenceRef.current)
        setTargetsLoading(false);
    }
  }, [invalidatePreview, targetPage, targetSearchApplied]);

  const loadOperations = useCallback(async () => {
    if (listInFlightRef.current) return;
    listInFlightRef.current = true;
    const sequence = ++listSequenceRef.current;
    try {
      const response = await apiClient.listMarketOperations({
        limit: OPERATION_PAGE_SIZE,
        offset: operationOffset,
        state: stateFilter || undefined,
      });
      if (!mountedRef.current || sequence !== listSequenceRef.current) return;
      setOperations((current) =>
        response.items.map((incoming) => {
          const existing = current.find(
            (item) => item.operation_id === incoming.operation_id,
          );
          return existing ? preferNewerOperation(existing, incoming) : incoming;
        }),
      );
      setSelectedOperation((current) => {
        if (!current) return current;
        const incoming = response.items.find(
          (item) => item.operation_id === current.operation_id,
        );
        return incoming ? preferNewerOperation(current, incoming) : current;
      });
      setOperationHasMore(response.has_more);
      setListError(null);
    } catch (error) {
      if (mountedRef.current && sequence === listSequenceRef.current)
        setListError(
          getErrorMessage(error, "Não foi possível carregar as operações."),
        );
    } finally {
      if (sequence === listSequenceRef.current) {
        listInFlightRef.current = false;
        if (mountedRef.current) setListLoading(false);
      }
    }
  }, [operationOffset, stateFilter]);

  const loadDetail = useCallback(
    async (operationId: string) => {
      if (detailInFlightRef.current) return;
      detailInFlightRef.current = true;
      const sequence = ++detailSequenceRef.current;
      try {
        const response = await apiClient.getMarketOperation(operationId);
        if (
          !mountedRef.current ||
          sequence !== detailSequenceRef.current ||
          selectedIdRef.current !== operationId
        )
          return;
        acceptOperationSnapshot(response);
        setDetailError(null);
      } catch (error) {
        if (
          mountedRef.current &&
          sequence === detailSequenceRef.current &&
          selectedIdRef.current === operationId
        )
          setDetailError(
            getErrorMessage(error, "Não foi possível carregar o detalhe."),
          );
      } finally {
        if (sequence === detailSequenceRef.current) {
          detailInFlightRef.current = false;
          if (mountedRef.current) setDetailLoading(false);
        }
      }
    },
    [acceptOperationSnapshot],
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      targetSequenceRef.current += 1;
      listSequenceRef.current += 1;
      detailSequenceRef.current += 1;
      listInFlightRef.current = false;
      detailInFlightRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (ambiguousSubmit && confirmedIntent) ambiguousRetryRef.current?.focus();
  }, [ambiguousSubmit, confirmedIntent]);

  useEffect(() => {
    void loadTargets();
    return () => {
      targetSequenceRef.current += 1;
    };
  }, [loadTargets]);

  useEffect(() => {
    setListLoading(true);
    void loadOperations();
    const timer = window.setInterval(
      () => void loadOperations(),
      POLL_INTERVAL_MS,
    );
    return () => {
      window.clearInterval(timer);
      listSequenceRef.current += 1;
      listInFlightRef.current = false;
    };
  }, [loadOperations]);

  useEffect(() => {
    if (!selectedId) {
      selectedIdRef.current = null;
      setSelectedOperation(null);
      return;
    }
    selectedIdRef.current = selectedId;
    setDetailLoading(true);
    void loadDetail(selectedId);
    const timer = window.setInterval(
      () => void loadDetail(selectedId),
      POLL_INTERVAL_MS,
    );
    return () => {
      window.clearInterval(timer);
      detailSequenceRef.current += 1;
      detailInFlightRef.current = false;
    };
  }, [loadDetail, selectedId]);

  const requestPreview = async () => {
    invalidatePreview();
    if (!selectedDatasetId) {
      setPreviewError("Selecione um alvo e timeframe válidos.");
      return;
    }
    setPreviewLoading(true);
    try {
      if (operationType === "RAW_BACKFILL") {
        if (!rangeStart || !rangeEnd) {
          setPreviewError("Informe o início e o fim do intervalo UTC.");
          return;
        }
        const result = await apiClient.previewMarketOperationBackfill({
          dataset_id: selectedDatasetId,
          range_start: asUtc(rangeStart),
          range_end: asUtc(rangeEnd),
        });
        setPreview(result);
      } else {
        const result = await apiClient.previewMarketOperationIncremental({
          dataset_id: selectedDatasetId,
          overlap_candles: overlapCandles,
          ...(incrementalStart ? { start: asUtc(incrementalStart) } : {}),
        });
        if (result.action === "NOOP") {
          setIncrementalNoop(
            `Nenhuma atualização necessária. Último candle armazenado: ${formatUtc(result.last_open_time)}. Limite fechado disponível: ${formatUtc(result.latest_closed_end)}.`,
          );
        } else if (result.preview) {
          setPreview(result.preview);
        } else {
          setPreviewError("A API retornou uma prévia incremental inválida.");
        }
      }
    } catch (error) {
      setPreviewError(
        getErrorMessage(error, "Não foi possível gerar a prévia."),
      );
    } finally {
      setPreviewLoading(false);
    }
  };

  const submitIntent = async (intent: ConfirmedIntent) => {
    setSubmitPending(true);
    setSubmitError(null);
    setAmbiguousSubmit(false);
    try {
      const operation = await apiClient.submitMarketOperation(intent.payload);
      setSubmitSuccess(`Operação ${operation.operation_id} submetida.`);
      setConfirmedIntent(null);
      setPreview(null);
      setConfirmOpen(false);
      selectedIdRef.current = operation.operation_id;
      invalidateDetailRequest();
      setSelectedId(operation.operation_id);
      setSelectedOperation(operation);
      invalidateListRequest();
      await loadOperations();
    } catch (error) {
      if (error instanceof ApiError && error.status === 0) {
        setAmbiguousSubmit(true);
        setSubmitError(
          "O resultado do envio é desconhecido. Verifique a lista ou repita exatamente o mesmo envio.",
        );
      } else {
        if (error instanceof ApiError && error.status === 409) {
          setPreview(null);
          setConfirmedIntent(null);
        }
        setSubmitError(
          getErrorMessage(error, "Não foi possível submeter a operação."),
        );
      }
    } finally {
      setSubmitPending(false);
    }
  };

  const confirmAndSubmit = () => {
    if (!preview) return;
    const key = crypto.randomUUID();
    const intent: ConfirmedIntent = {
      key,
      payload: {
        operation_type: preview.operation_type,
        dataset_id: preview.dataset.dataset_id,
        range_start: preview.range_start,
        range_end: preview.range_end,
        plan_checksum: preview.plan.checksum,
        idempotency_key: key,
        confirmed: true,
      },
    };
    setConfirmedIntent(intent);
    setConfirmOpen(false);
    void submitIntent(intent);
  };

  const selectOperation = (operation: MarketOperation) => {
    selectedIdRef.current = operation.operation_id;
    invalidateDetailRequest();
    setSelectedId(operation.operation_id);
    setSelectedOperation((current) =>
      current?.operation_id === operation.operation_id
        ? preferNewerOperation(current, operation)
        : operation,
    );
    setDetailError(null);
    setControlError(null);
    setConflictMessage(null);
  };

  const controlOperation = async (action: "pause" | "resume" | "cancel") => {
    const operation = selectedOperation;
    if (!operation) return;
    setControlPending(action);
    setControlError(null);
    setConflictMessage(null);
    invalidateDetailRequest();
    try {
      const payload = { expected_version: operation.record_version };
      const updated =
        action === "pause"
          ? await apiClient.pauseMarketOperation(
              operation.operation_id,
              payload,
            )
          : action === "resume"
            ? await apiClient.resumeMarketOperation(
                operation.operation_id,
                payload,
              )
            : await apiClient.cancelMarketOperation(
                operation.operation_id,
                payload,
              );
      acceptOperationSnapshot(updated);
      invalidateListRequest();
      await loadOperations();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setConflictMessage(
          "A operação mudou no servidor. O estado foi recarregado; revise-o antes de tentar novamente.",
        );
        if (selectedIdRef.current === operation.operation_id)
          await loadDetail(operation.operation_id);
      } else {
        setControlError(
          getErrorMessage(error, "Não foi possível aplicar o controle."),
        );
      }
    } finally {
      setControlPending(null);
    }
  };

  const detail = selectedOperation;

  return (
    <div className="market-operations-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Phase 7 · Control plane</p>
          <h1>Operações de mercado</h1>
          <p>
            Planeje, confirme e acompanhe operações RAW. A execução física
            permanece no worker separado.
          </p>
        </div>
        <button
          className="button button--ghost"
          type="button"
          onClick={() => void loadOperations()}
          disabled={listInFlightRef.current}
        >
          Atualizar operações
        </button>
      </div>

      <section
        className="panel operation-create"
        aria-labelledby="new-operation-title"
      >
        <div className="section-heading">
          <h2 id="new-operation-title">Nova operação</h2>
          <span>Prévia obrigatória</span>
        </div>

        <form
          className="operation-target-search"
          onSubmit={(event) => {
            event.preventDefault();
            setTargetPage(1);
            setTargetSearchApplied(targetSearch.trim());
            invalidatePreview();
          }}
        >
          <label>
            Buscar alvo
            <input
              value={targetSearch}
              onChange={(event) => setTargetSearch(event.target.value)}
              placeholder="BTC ou USDT"
            />
          </label>
          <button className="button button--ghost" type="submit">
            Buscar
          </button>
        </form>

        {targetsLoading ? (
          <LoadingState message="Carregando alvos válidos…" />
        ) : targetError ? (
          <InlineError message={targetError} />
        ) : targets.length === 0 ? (
          <EmptyState
            title="Nenhum alvo encontrado"
            description="Ajuste a busca e consulte novamente o catálogo autorizado."
          />
        ) : (
          <div className="operation-form-grid">
            <label>
              Alvo
              <select
                value={selectedSymbol}
                onChange={(event) => {
                  const target = targets.find(
                    (item) => item.symbol === event.target.value,
                  );
                  selectedSymbolRef.current = event.target.value;
                  setSelectedSymbol(event.target.value);
                  setSelectedTimeframe(target?.timeframes[0]?.timeframe ?? "");
                  invalidatePreview();
                }}
              >
                {targets.map((target) => (
                  <option key={target.symbol} value={target.symbol}>
                    {target.symbol} · {target.exchange} {target.market_type}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Timeframe operacional
              <select
                value={selectedTimeframe}
                onChange={(event) => {
                  setSelectedTimeframe(event.target.value);
                  invalidatePreview();
                }}
              >
                {selectedTarget?.timeframes.map((item) => (
                  <option key={item.dataset_id} value={item.timeframe}>
                    {item.timeframe}
                  </option>
                ))}
              </select>
            </label>
            <fieldset className="operation-kind">
              <legend>Tipo de operação</legend>
              <label>
                <input
                  type="radio"
                  name="operation-type"
                  checked={operationType === "RAW_BACKFILL"}
                  onChange={() => {
                    setOperationType("RAW_BACKFILL");
                    invalidatePreview();
                  }}
                />
                RAW backfill
              </label>
              <label>
                <input
                  type="radio"
                  name="operation-type"
                  checked={operationType === "RAW_INCREMENTAL_UPDATE"}
                  onChange={() => {
                    setOperationType("RAW_INCREMENTAL_UPDATE");
                    invalidatePreview();
                  }}
                />
                RAW incremental
              </label>
            </fieldset>
            {operationType === "RAW_BACKFILL" ? (
              <>
                <label>
                  Início do intervalo (UTC)
                  <input
                    type="datetime-local"
                    value={rangeStart}
                    onChange={(event) => {
                      setRangeStart(event.target.value);
                      invalidatePreview();
                    }}
                  />
                </label>
                <label>
                  Fim do intervalo (UTC, exclusivo)
                  <input
                    type="datetime-local"
                    value={rangeEnd}
                    onChange={(event) => {
                      setRangeEnd(event.target.value);
                      invalidatePreview();
                    }}
                  />
                </label>
              </>
            ) : (
              <>
                <label>
                  Overlap de candles
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={overlapCandles}
                    onChange={(event) => {
                      setOverlapCandles(Number(event.target.value));
                      invalidatePreview();
                    }}
                  />
                </label>
                <label>
                  Início mínimo (UTC, opcional)
                  <input
                    type="datetime-local"
                    value={incrementalStart}
                    onChange={(event) => {
                      setIncrementalStart(event.target.value);
                      invalidatePreview();
                    }}
                  />
                </label>
              </>
            )}
          </div>
        )}

        <div className="form-actions">
          <button
            className="button"
            type="button"
            disabled={targetsLoading || previewLoading || !selectedDatasetId}
            onClick={() => void requestPreview()}
          >
            {previewLoading ? "Gerando prévia…" : "Gerar prévia"}
          </button>
        </div>

        {previewError && <InlineError message={previewError} />}
        {incrementalNoop && (
          <p className="operation-notice" role="status">
            <strong>Incremental sem trabalho.</strong> {incrementalNoop} Nenhuma
            operação será submetida.
          </p>
        )}
        {preview && (
          <article className="operation-preview" aria-live="polite">
            <div>
              <p className="eyebrow">Prévia do backend</p>
              <h3>
                {operationLabels[preview.operation_type]} ·{" "}
                {preview.dataset.symbol} · {preview.dataset.timeframe}
              </h3>
            </div>
            <dl>
              <div>
                <dt>Intervalo UTC</dt>
                <dd>
                  {formatUtc(preview.range_start)} →{" "}
                  {formatUtc(preview.range_end)}
                </dd>
              </div>
              <div>
                <dt>Chunks</dt>
                <dd>{preview.plan.chunks_planned}</dd>
              </div>
              <div>
                <dt>Candles estimados</dt>
                <dd>{preview.plan.estimated_candles}</dd>
              </div>
              <div>
                <dt>Requests estimados</dt>
                <dd>{preview.plan.estimated_requests}</dd>
              </div>
              <div>
                <dt>Checksum</dt>
                <dd title={preview.plan.checksum}>
                  {shortChecksum(preview.plan.checksum)}
                </dd>
              </div>
            </dl>
            {!(ambiguousSubmit && confirmedIntent) && (
              <button
                className="button"
                type="button"
                onClick={() => setConfirmOpen(true)}
                disabled={submitPending}
              >
                Confirmar e submeter
              </button>
            )}
          </article>
        )}
        {submitError && <InlineError message={submitError} />}
        {ambiguousSubmit && confirmedIntent && (
          <button
            ref={ambiguousRetryRef}
            className="button button--ghost"
            type="button"
            disabled={submitPending}
            onClick={() => void submitIntent(confirmedIntent)}
          >
            Repetir o mesmo envio
          </button>
        )}
        {submitSuccess && (
          <p className="form-message form-message--success" role="status">
            {submitSuccess}
          </p>
        )}
      </section>

      <section aria-labelledby="operations-title">
        <div className="section-heading">
          <h2 id="operations-title">Operações</h2>
          <span>Atualização automática a cada 30 segundos</span>
        </div>
        <div className="operation-list-toolbar">
          <label>
            Estado
            <select
              value={stateFilter}
              onChange={(event) => {
                setStateFilter(event.target.value as MarketOperationState | "");
                setOperationOffset(0);
              }}
            >
              <option value="">Todos</option>
              {Object.entries(stateLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </div>

        {listLoading ? (
          <LoadingState message="Carregando operações…" />
        ) : listError ? (
          <InlineError message={listError} />
        ) : operations.length === 0 ? (
          <EmptyState
            title="Nenhuma operação"
            description="As operações submetidas aparecerão nesta lista limitada."
          />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Operação</th>
                  <th>Alvo</th>
                  <th>Estado</th>
                  <th>Progresso</th>
                  <th>Resultado</th>
                  <th>Criada / atualizada (UTC)</th>
                </tr>
              </thead>
              <tbody>
                {operations.map((operation) => (
                  <tr key={operation.operation_id}>
                    <td>
                      <button
                        className="operation-select"
                        type="button"
                        onClick={() => selectOperation(operation)}
                        aria-label={`Inspecionar ${operationLabels[operation.operation_type]} ${operation.dataset.symbol} ${operation.dataset.timeframe}`}
                      >
                        {operationLabels[operation.operation_type]}
                      </button>
                      <small>{operation.operation_id.slice(0, 12)}</small>
                    </td>
                    <td>
                      <strong>{operation.dataset.symbol}</strong>
                      <small>{operation.dataset.timeframe}</small>
                    </td>
                    <td>
                      <span className={stateClass(operation.state)}>
                        {stateLabels[operation.state]}
                      </span>
                    </td>
                    <td>{progressLabel(operation)}</td>
                    <td>
                      {operation.failure
                        ? `Falha: ${operation.failure.code}`
                        : operation.result
                          ? "Resultado disponível"
                          : "Ainda não disponível"}
                    </td>
                    <td>
                      {formatUtc(operation.created_at)}
                      <small>{formatUtc(operation.updated_at)}</small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div
          className="operation-pagination"
          aria-label="Paginação de operações"
        >
          <button
            className="button button--ghost button--compact"
            type="button"
            disabled={operationOffset === 0}
            onClick={() =>
              setOperationOffset((value) =>
                Math.max(0, value - OPERATION_PAGE_SIZE),
              )
            }
          >
            Anterior
          </button>
          <span>
            Página {Math.floor(operationOffset / OPERATION_PAGE_SIZE) + 1}
          </span>
          <button
            className="button button--ghost button--compact"
            type="button"
            disabled={!operationHasMore}
            onClick={() =>
              setOperationOffset((value) => value + OPERATION_PAGE_SIZE)
            }
          >
            Próxima
          </button>
        </div>
      </section>

      {selectedId && (
        <section
          className="panel operation-detail"
          aria-labelledby="operation-detail-title"
        >
          <div className="section-heading">
            <h2 id="operation-detail-title">Detalhe da operação</h2>
            <button
              className="button button--ghost button--compact"
              type="button"
              onClick={() => void loadDetail(selectedId)}
              disabled={detailInFlightRef.current}
            >
              Atualizar detalhe
            </button>
          </div>
          {detailLoading && !detail ? (
            <LoadingState message="Carregando detalhe…" />
          ) : detailError ? (
            <InlineError message={detailError} />
          ) : detail ? (
            <>
              <div className="operation-detail__heading">
                <div>
                  <span className={stateClass(detail.state)}>
                    {stateLabels[detail.state]}
                  </span>
                  <h3>
                    {operationLabels[detail.operation_type]} ·{" "}
                    {detail.dataset.symbol} · {detail.dataset.timeframe}
                  </h3>
                  <small>ID {detail.operation_id}</small>
                </div>
                <strong>Versão {detail.record_version}</strong>
              </div>
              <dl className="operation-detail-grid">
                <div>
                  <dt>Intervalo UTC</dt>
                  <dd>
                    {formatUtc(detail.range_start)} →{" "}
                    {formatUtc(detail.range_end)}
                  </dd>
                </div>
                <div>
                  <dt>Plano</dt>
                  <dd title={detail.plan.checksum}>
                    {detail.plan.chunks_planned} chunks ·{" "}
                    {shortChecksum(detail.plan.checksum)}
                  </dd>
                </div>
                <div>
                  <dt>Progresso</dt>
                  <dd>
                    {progressLabel(detail)} ·{" "}
                    {detail.progress.candles_persisted} candles persistidos
                  </dd>
                </div>
                <div>
                  <dt>Criada / atualizada</dt>
                  <dd>
                    {formatUtc(detail.created_at)} /{" "}
                    {formatUtc(detail.updated_at)}
                  </dd>
                </div>
                <div>
                  <dt>Iniciada / finalizada</dt>
                  <dd>
                    {formatUtc(detail.started_at)} /{" "}
                    {formatUtc(detail.finished_at)}
                  </dd>
                </div>
                <div>
                  <dt>Resultado</dt>
                  <dd>
                    {detail.result ? (
                      <>
                        Dataset {shortChecksum(detail.result.dataset_version)}
                        <small>
                          Checksum{" "}
                          {shortChecksum(detail.result.dataset_checksum)}
                          {" · "}
                          concluído {formatUtc(detail.result.completed_at)}
                        </small>
                      </>
                    ) : detail.failure ? (
                      `Falha ${detail.failure.code} em ${formatUtc(detail.failure.failed_at)}`
                    ) : (
                      "Ainda não disponível"
                    )}
                  </dd>
                </div>
              </dl>
              {detail.lease && (
                <div className="operation-lease">
                  <h3>Lease operacional</h3>
                  <p>Reivindicada: {formatUtc(detail.lease.claimed_at)}</p>
                  <p>
                    Último heartbeat: {formatUtc(detail.lease.heartbeat_at)}
                  </p>
                  <p>
                    Lease expira em: {formatUtc(detail.lease.lease_expires_at)}
                  </p>
                  <p>
                    {Date.parse(detail.lease.lease_expires_at) >
                    Date.parse(detail.observed_at)
                      ? "Lease válida no instante observado pelo servidor."
                      : "Lease expirada no instante observado pelo servidor."}
                  </p>
                  <small>
                    Instante observado: {formatUtc(detail.observed_at)}
                  </small>
                </div>
              )}
              {conflictMessage && (
                <p
                  className="operation-notice operation-notice--warning"
                  role="alert"
                >
                  {conflictMessage}
                </p>
              )}
              {controlError && <InlineError message={controlError} />}
              <div className="operation-controls">
                {canPause(detail.state) && (
                  <button
                    className="button button--ghost"
                    type="button"
                    disabled={controlPending !== null}
                    onClick={() => void controlOperation("pause")}
                  >
                    {controlPending === "pause"
                      ? "Solicitando…"
                      : "Solicitar pausa"}
                  </button>
                )}
                {canResume(detail.state) && (
                  <button
                    className="button"
                    type="button"
                    disabled={controlPending !== null}
                    onClick={() => void controlOperation("resume")}
                  >
                    {controlPending === "resume" ? "Retomando…" : "Retomar"}
                  </button>
                )}
                {canCancel(detail.state) && (
                  <button
                    className="button button--danger"
                    type="button"
                    disabled={controlPending !== null}
                    onClick={() => void controlOperation("cancel")}
                  >
                    {controlPending === "cancel"
                      ? "Solicitando…"
                      : "Solicitar cancelamento"}
                  </button>
                )}
                {terminalStates.has(detail.state) && (
                  <span>Nenhum controle disponível em estado terminal.</span>
                )}
              </div>
            </>
          ) : null}
        </section>
      )}

      <div className="target-pagination" aria-label="Paginação de alvos">
        <button
          className="button button--ghost button--compact"
          type="button"
          disabled={targetPage <= 1}
          onClick={() => {
            setTargetPage((value) => Math.max(1, value - 1));
            invalidatePreview();
          }}
        >
          Alvos anteriores
        </button>
        <span>
          Página de alvos {targetPage} de {Math.max(targetTotalPages, 1)}
        </span>
        <button
          className="button button--ghost button--compact"
          type="button"
          disabled={targetTotalPages === 0 || targetPage >= targetTotalPages}
          onClick={() => {
            setTargetPage((value) => value + 1);
            invalidatePreview();
          }}
        >
          Próximos alvos
        </button>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        title="Confirmar submissão da operação?"
        description={
          preview
            ? `${operationLabels[preview.operation_type]} para ${preview.dataset.symbol} em ${preview.dataset.timeframe}, usando exatamente a prévia exibida.`
            : "A prévia não está mais disponível."
        }
        confirmLabel="Confirmar e submeter"
        busy={submitPending}
        onConfirm={confirmAndSubmit}
        onCancel={() => setConfirmOpen(false)}
      />
    </div>
  );
}
