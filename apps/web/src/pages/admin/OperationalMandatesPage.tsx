import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import {
  EmptyState,
  InlineError,
  LoadingState,
  SuccessMessage,
} from "../../components/States";
import { ApiError, apiClient } from "../../http/client";
import type {
  OperationalMandateApproveRequest,
  OperationalMandateArchiveRequest,
  OperationalMandateCreateRequest,
  OperationalMandateCurrent,
  OperationalMandateList,
  OperationalMandateReplaceRequest,
  OperationalMandateRevision,
  OperationalMandateRevisionList,
  OperationalMandateSpecificationRequest,
  OperationalMandateState,
} from "../../types/api";
import { formatDate, getErrorMessage } from "../../utils/format";

const PAGE_SIZE = 20;
const HISTORY_PAGE_SIZE = 20;
const CURRENT_SCHEMA_VERSION = 1;

const STATE_LABELS: Record<OperationalMandateState, string> = {
  DRAFT: "Rascunho",
  APPROVED: "Aprovado",
  ARCHIVED: "Arquivado",
};

interface InstrumentDraft {
  exchange: "binance";
  market_type: "spot";
  base_asset: string;
  quote_asset: string;
}

interface SpecificationDraft {
  name: string;
  description: string;
  instruments: InstrumentDraft[];
}

interface ConfirmedCreateIntent {
  payload: OperationalMandateCreateRequest;
}

interface MutationSnapshot {
  current: OperationalMandateCurrent;
  kind: "approve" | "archive";
}

const emptyInstrument = (): InstrumentDraft => ({
  exchange: "binance",
  market_type: "spot",
  base_asset: "",
  quote_asset: "USDT",
});

const emptySpecification = (): SpecificationDraft => ({
  name: "",
  description: "",
  instruments: [emptyInstrument()],
});

function toDraft(
  specification: OperationalMandateCurrent["revision"]["specification"],
): SpecificationDraft {
  return {
    name: specification.name,
    description: specification.description,
    instruments: specification.instruments.map((instrument) => ({
      exchange: "binance",
      market_type: "spot",
      base_asset: instrument.base_asset,
      quote_asset: instrument.quote_asset,
    })),
  };
}

function toSpecification(
  draft: SpecificationDraft,
): OperationalMandateSpecificationRequest {
  return {
    schema_version: CURRENT_SCHEMA_VERSION,
    name: draft.name.trim(),
    description: draft.description.trim(),
    instruments: draft.instruments.map((instrument) => ({
      exchange: instrument.exchange,
      market_type: instrument.market_type,
      base_asset: instrument.base_asset.trim().toUpperCase(),
      quote_asset: instrument.quote_asset.trim().toUpperCase(),
    })),
  };
}

function safeError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return "Acesso administrativo negado.";
    if (error.status === 404)
      return "O mandato ou a revisão selecionada não está mais disponível.";
    if (error.status === 409)
      return "O mandato mudou no servidor. Revise o estado recarregado antes de tentar novamente.";
    if (error.status === 422)
      return "O servidor rejeitou os dados informados. Revise o formulário.";
    if (error.status === 0 || error.status >= 500) return fallback;
  }
  return getErrorMessage(error, fallback);
}

function checksumSummary(checksum: string): string {
  return `${checksum.slice(0, 12)}…${checksum.slice(-8)}`;
}

function instrumentLabel(
  instrument: OperationalMandateRevision["specification"]["instruments"][number],
): string {
  return `${instrument.base_asset}/${instrument.quote_asset} · ${instrument.exchange} ${instrument.market_type}`;
}

function StateBadge({ state }: { state: OperationalMandateState }) {
  const modifier =
    state === "APPROVED"
      ? "operation-state--success"
      : state === "DRAFT"
        ? "operation-state--warning"
        : "";
  return (
    <span className={`operation-state ${modifier}`.trim()}>
      {STATE_LABELS[state]} ({state})
    </span>
  );
}

function Pagination({
  label,
  limit,
  offset,
  total,
  disabled,
  onChange,
}: {
  label: string;
  limit: number;
  offset: number;
  total: number;
  disabled: boolean;
  onChange(offset: number): void;
}) {
  const currentPage = total === 0 ? 0 : Math.floor(offset / limit) + 1;
  const totalPages = Math.ceil(total / limit);
  return (
    <nav className="operation-pagination" aria-label={label}>
      <button
        className="button button--ghost button--compact"
        type="button"
        disabled={disabled || offset === 0}
        onClick={() => onChange(Math.max(0, offset - limit))}
      >
        Anterior
      </button>
      <span>
        Página {currentPage} de {totalPages} · {total} registro(s)
      </span>
      <button
        className="button button--ghost button--compact"
        type="button"
        disabled={disabled || offset + limit >= total}
        onClick={() => onChange(offset + limit)}
      >
        Próxima
      </button>
    </nav>
  );
}

function SpecificationFields({
  draft,
  disabled,
  prefix,
  onChange,
}: {
  draft: SpecificationDraft;
  disabled: boolean;
  prefix: string;
  onChange(next: SpecificationDraft): void;
}) {
  const updateInstrument = (
    index: number,
    field: "base_asset" | "quote_asset",
    value: string,
  ) => {
    onChange({
      ...draft,
      instruments: draft.instruments.map((instrument, itemIndex) =>
        itemIndex === index ? { ...instrument, [field]: value } : instrument,
      ),
    });
  };

  return (
    <>
      <div className="form-grid mandate-form-grid">
        <label>
          Nome
          <input
            value={draft.name}
            required
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...draft, name: event.currentTarget.value })
            }
          />
        </label>
        <label className="form-grid__wide">
          Descrição
          <textarea
            value={draft.description}
            required
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...draft, description: event.currentTarget.value })
            }
          />
        </label>
      </div>
      <fieldset className="mandate-instruments" disabled={disabled}>
        <legend>Instrumentos canônicos</legend>
        {draft.instruments.map((instrument, index) => (
          <div className="mandate-instrument-row" key={`${prefix}-${index}`}>
            <label>
              Exchange
              <select value={instrument.exchange} disabled>
                <option value="binance">Binance</option>
              </select>
            </label>
            <label>
              Mercado
              <select value={instrument.market_type} disabled>
                <option value="spot">Spot</option>
              </select>
            </label>
            <label>
              Ativo base
              <input
                value={instrument.base_asset}
                required
                autoCapitalize="characters"
                onChange={(event) =>
                  updateInstrument(index, "base_asset", event.currentTarget.value)
                }
              />
            </label>
            <label>
              Ativo de cotação
              <input
                value={instrument.quote_asset}
                required
                autoCapitalize="characters"
                onChange={(event) =>
                  updateInstrument(index, "quote_asset", event.currentTarget.value)
                }
              />
            </label>
            <button
              className="button button--ghost button--compact"
              type="button"
              disabled={disabled || draft.instruments.length === 1}
              aria-label={`Remover instrumento ${index + 1}`}
              onClick={() =>
                onChange({
                  ...draft,
                  instruments: draft.instruments.filter(
                    (_, itemIndex) => itemIndex !== index,
                  ),
                })
              }
            >
              Remover
            </button>
          </div>
        ))}
        <button
          className="button button--ghost button--compact"
          type="button"
          disabled={disabled}
          onClick={() =>
            onChange({
              ...draft,
              instruments: [...draft.instruments, emptyInstrument()],
            })
          }
        >
          Adicionar instrumento
        </button>
      </fieldset>
    </>
  );
}

function RevisionDetails({
  revision,
  historical,
}: {
  revision: OperationalMandateRevision;
  historical: boolean;
}) {
  return (
    <section
      className={historical ? "mandate-revision mandate-revision--historical" : "mandate-revision"}
      aria-label={historical ? "Revisão histórica exata" : "Revisão atual exata"}
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">
            {historical ? "Registro histórico imutável" : "Especificação corrente"}
          </p>
          <h3>Revisão {revision.revision}</h3>
        </div>
        <code title={revision.specification_checksum}>
          {checksumSummary(revision.specification_checksum)}
        </code>
      </div>
      <p className="mandate-revision-name">
        <strong>Nome:</strong> {revision.specification.name}
      </p>
      <p>{revision.specification.description}</p>
      <ul className="mandate-instrument-list">
        {revision.specification.instruments.map((instrument) => (
          <li key={`${instrument.exchange}:${instrument.market_type}:${instrument.base_asset}:${instrument.quote_asset}`}>
            <strong>{instrument.base_asset}/{instrument.quote_asset}</strong>
            <span>{instrument.exchange} · {instrument.market_type}</span>
          </li>
        ))}
      </ul>
      <dl className="operation-detail-grid">
        <div>
          <dt>Checksum completo</dt>
          <dd><code className="mandate-code">{revision.specification_checksum}</code></dd>
        </div>
        <div>
          <dt>Schema</dt>
          <dd>{revision.specification.schema_version}</dd>
        </div>
        <div>
          <dt>Criado por (UUID)</dt>
          <dd><code className="mandate-code">{revision.created_by}</code></dd>
        </div>
        <div>
          <dt>Criado em</dt>
          <dd>{formatDate(revision.created_at)}</dd>
        </div>
      </dl>
      {historical && (
        <p className="operation-notice operation-notice--warning">
          Esta é somente a especificação histórica da revisão {revision.revision}.
          O estado de ciclo de vida pertence ao agregado atual e não foi projetado
          sobre este registro.
        </p>
      )}
    </section>
  );
}

export function OperationalMandatesPage() {
  const mountedRef = useRef(true);
  const listSequenceRef = useRef(0);
  const detailSequenceRef = useRef(0);
  const historySequenceRef = useRef(0);
  const revisionSequenceRef = useRef(0);
  const selectedIdRef = useRef<string | null>(null);

  const [stateFilter, setStateFilter] = useState<"" | OperationalMandateState>("");
  const [listOffset, setListOffset] = useState(0);
  const [catalog, setCatalog] = useState<OperationalMandateList | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [current, setCurrent] = useState<OperationalMandateCurrent | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [historyOffset, setHistoryOffset] = useState(0);
  const [history, setHistory] = useState<OperationalMandateRevisionList | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historicalRevision, setHistoricalRevision] = useState<OperationalMandateRevision | null>(null);
  const [revisionLoading, setRevisionLoading] = useState(false);
  const [revisionError, setRevisionError] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [createDraft, setCreateDraft] = useState<SpecificationDraft>(emptySpecification);
  const [createIntent, setCreateIntent] = useState<ConfirmedCreateIntent | null>(null);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);

  const [editing, setEditing] = useState(false);
  const [replaceDraft, setReplaceDraft] = useState<SpecificationDraft>(emptySpecification);
  const [mutationSnapshot, setMutationSnapshot] = useState<MutationSnapshot | null>(null);
  const [mutationBusy, setMutationBusy] = useState<"create" | "replace" | "approve" | "archive" | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [reviewRequired, setReviewRequired] = useState(false);

  const chooseMandate = useCallback((mandateId: string | null) => {
    selectedIdRef.current = mandateId;
    setSelectedId(mandateId);
    setCurrent(null);
    setDetailError(null);
    setHistory(null);
    setHistoryOffset(0);
    setHistoricalRevision(null);
    setRevisionError(null);
    setEditing(false);
    setReviewRequired(false);
  }, []);

  const loadCatalog = useCallback(async () => {
    const sequence = ++listSequenceRef.current;
    setListLoading(true);
    setListError(null);
    try {
      const response = await apiClient.listOperationalMandates({
        limit: PAGE_SIZE,
        offset: listOffset,
        state: stateFilter || undefined,
      });
      if (!mountedRef.current || sequence !== listSequenceRef.current) return;
      setCatalog(response);
      if (selectedIdRef.current === null && response.items[0]) {
        chooseMandate(response.items[0].mandate.mandate_id);
      }
    } catch (error) {
      if (!mountedRef.current || sequence !== listSequenceRef.current) return;
      setListError(safeError(error, "Não foi possível carregar os mandatos."));
    } finally {
      if (mountedRef.current && sequence === listSequenceRef.current)
        setListLoading(false);
    }
  }, [chooseMandate, listOffset, stateFilter]);

  const loadDetail = useCallback(async (mandateId: string) => {
    const sequence = ++detailSequenceRef.current;
    setDetailLoading(true);
    setDetailError(null);
    try {
      const response = await apiClient.getOperationalMandate(mandateId);
      if (
        !mountedRef.current ||
        sequence !== detailSequenceRef.current ||
        selectedIdRef.current !== mandateId
      ) return;
      setCurrent(response);
    } catch (error) {
      if (
        !mountedRef.current ||
        sequence !== detailSequenceRef.current ||
        selectedIdRef.current !== mandateId
      ) return;
      setDetailError(safeError(error, "Não foi possível carregar o mandato."));
    } finally {
      if (
        mountedRef.current &&
        sequence === detailSequenceRef.current &&
        selectedIdRef.current === mandateId
      ) setDetailLoading(false);
    }
  }, []);

  const loadHistory = useCallback(async (mandateId: string, offset: number) => {
    const sequence = ++historySequenceRef.current;
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const response = await apiClient.listOperationalMandateRevisions(mandateId, {
        limit: HISTORY_PAGE_SIZE,
        offset,
      });
      if (
        !mountedRef.current ||
        sequence !== historySequenceRef.current ||
        selectedIdRef.current !== mandateId
      ) return;
      setHistory(response);
    } catch (error) {
      if (
        !mountedRef.current ||
        sequence !== historySequenceRef.current ||
        selectedIdRef.current !== mandateId
      ) return;
      setHistoryError(safeError(error, "Não foi possível carregar o histórico."));
    } finally {
      if (
        mountedRef.current &&
        sequence === historySequenceRef.current &&
        selectedIdRef.current === mandateId
      ) setHistoryLoading(false);
    }
  }, []);

  const inspectRevision = async (revision: number) => {
    if (!selectedId) return;
    const mandateId = selectedId;
    const sequence = ++revisionSequenceRef.current;
    setHistoricalRevision(null);
    setRevisionLoading(true);
    setRevisionError(null);
    try {
      const response = await apiClient.getOperationalMandateRevision(
        mandateId,
        revision,
      );
      if (
        !mountedRef.current ||
        sequence !== revisionSequenceRef.current ||
        selectedIdRef.current !== mandateId
      ) return;
      setHistoricalRevision(response);
    } catch (error) {
      if (
        mountedRef.current &&
        sequence === revisionSequenceRef.current &&
        selectedIdRef.current === mandateId
      ) setRevisionError(safeError(error, "Não foi possível carregar a revisão."));
    } finally {
      if (
        mountedRef.current &&
        sequence === revisionSequenceRef.current &&
        selectedIdRef.current === mandateId
      ) setRevisionLoading(false);
    }
  };

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      listSequenceRef.current += 1;
      detailSequenceRef.current += 1;
      historySequenceRef.current += 1;
      revisionSequenceRef.current += 1;
    };
  }, []);

  useEffect(() => {
    void loadCatalog();
  }, [loadCatalog]);

  useEffect(() => {
    if (!selectedId) return;
    void loadDetail(selectedId);
  }, [loadDetail, selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    void loadHistory(selectedId, historyOffset);
  }, [historyOffset, loadHistory, selectedId]);

  const refreshAuthoritative = useCallback(async () => {
    const mandateId = selectedIdRef.current;
    await Promise.all([
      loadCatalog(),
      mandateId ? loadDetail(mandateId) : Promise.resolve(),
      mandateId ? loadHistory(mandateId, historyOffset) : Promise.resolve(),
    ]);
  }, [historyOffset, loadCatalog, loadDetail, loadHistory]);

  const clearFeedback = () => {
    setSuccess(null);
    setMutationError(null);
  };

  const handleConflict = async (error: unknown) => {
    setMutationError(safeError(error, "O mandato mudou no servidor."));
    setReviewRequired(true);
    setEditing(false);
    setMutationSnapshot(null);
    await refreshAuthoritative();
  };

  const updateCreateDraft = (next: SpecificationDraft) => {
    setCreateDraft(next);
    if (createIntent) setCreateIntent(null);
    setMutationError(null);
  };

  const prepareCreate = (event: FormEvent) => {
    event.preventDefault();
    clearFeedback();
    const intent: ConfirmedCreateIntent = {
      payload: {
        specification: toSpecification(createDraft),
        idempotency_key: crypto.randomUUID(),
      },
    };
    setCreateIntent(intent);
    setCreateDialogOpen(true);
  };

  const submitCreate = async (intent: ConfirmedCreateIntent) => {
    setMutationBusy("create");
    setMutationError(null);
    try {
      const response = await apiClient.createOperationalMandate(intent.payload);
      if (!mountedRef.current) return;
      setCreateIntent(null);
      setCreateDialogOpen(false);
      setCreateOpen(false);
      setCreateDraft(emptySpecification());
      setSuccess(`Mandato ${response.revision.specification.name} criado como DRAFT.`);
      setStateFilter("");
      setListOffset(0);
      chooseMandate(response.mandate.mandate_id);
      setCurrent(response);
      await refreshAuthoritative();
    } catch (error) {
      if (!mountedRef.current) return;
      setCreateDialogOpen(false);
      if (!(error instanceof ApiError && error.status === 0)) setCreateIntent(null);
      setMutationError(
        error instanceof ApiError && error.status === 0
          ? "A resposta da criação não foi confirmada. Repita somente este mesmo envio para reutilizar a chave de idempotência."
          : safeError(error, "Não foi possível criar o mandato."),
      );
    } finally {
      if (mountedRef.current) setMutationBusy(null);
    }
  };

  const submitReplace = async (event: FormEvent) => {
    event.preventDefault();
    if (!current || current.mandate.state !== "DRAFT" || reviewRequired) return;
    clearFeedback();
    const payload: OperationalMandateReplaceRequest = {
      specification: toSpecification(replaceDraft),
      expected_revision: current.mandate.current_revision,
      expected_record_version: current.mandate.record_version,
    };
    setMutationBusy("replace");
    try {
      const response = await apiClient.replaceOperationalMandateDraft(
        current.mandate.mandate_id,
        payload,
      );
      if (!mountedRef.current) return;
      setCurrent(response);
      setEditing(false);
      setSuccess(
        response.mandate.current_revision === current.mandate.current_revision
          ? "Especificação confirmada sem nova revisão (NOOP semântico)."
          : `Rascunho substituído pela revisão ${response.mandate.current_revision}.`,
      );
      await refreshAuthoritative();
    } catch (error) {
      if (!mountedRef.current) return;
      if (error instanceof ApiError && error.status === 409) {
        await handleConflict(error);
      } else {
        setMutationError(safeError(error, "Não foi possível substituir o rascunho."));
      }
    } finally {
      if (mountedRef.current) setMutationBusy(null);
    }
  };

  const confirmMutation = async () => {
    const snapshot = mutationSnapshot;
    if (!snapshot || reviewRequired) return;
    clearFeedback();
    setMutationBusy(snapshot.kind);
    try {
      if (snapshot.kind === "approve") {
        const payload: OperationalMandateApproveRequest = {
          expected_revision: snapshot.current.revision.revision,
          expected_checksum: snapshot.current.revision.specification_checksum,
          expected_record_version: snapshot.current.mandate.record_version,
        };
        await apiClient.approveOperationalMandate(
          snapshot.current.mandate.mandate_id,
          payload,
        );
        if (mountedRef.current) setSuccess("Mandato aprovado com os tokens revisados.");
      } else {
        const payload: OperationalMandateArchiveRequest = {
          expected_record_version: snapshot.current.mandate.record_version,
        };
        await apiClient.archiveOperationalMandate(
          snapshot.current.mandate.mandate_id,
          payload,
        );
        if (mountedRef.current) setSuccess("Mandato arquivado.");
      }
      if (!mountedRef.current) return;
      setMutationSnapshot(null);
      await refreshAuthoritative();
    } catch (error) {
      if (!mountedRef.current) return;
      if (error instanceof ApiError && error.status === 409) {
        await handleConflict(error);
      } else {
        setMutationSnapshot(null);
        setMutationError(safeError(error, "Não foi possível concluir a ação."));
      }
    } finally {
      if (mountedRef.current) setMutationBusy(null);
    }
  };

  const changeCatalogContext = (
    nextState: "" | OperationalMandateState,
    nextOffset: number,
  ) => {
    chooseMandate(null);
    setCatalog(null);
    setStateFilter(nextState);
    setListOffset(nextOffset);
  };

  const busy = mutationBusy !== null;

  return (
    <div className="operational-mandates-page">
      <header className="page-heading mandate-page-heading">
        <div>
          <p className="eyebrow">Phase 7 · controle administrativo</p>
          <h1>Mandatos operacionais</h1>
          <p>
            Revise especificações imutáveis e altere o ciclo de vida somente com
            os tokens exatos publicados pela API.
          </p>
        </div>
        <div className="mandate-heading-actions">
          <button
            className="button button--ghost"
            type="button"
            disabled={busy || listLoading || detailLoading || historyLoading}
            onClick={() => void refreshAuthoritative()}
          >
            Atualizar
          </button>
          <button
            className="button"
            type="button"
            disabled={busy}
            onClick={() => {
              clearFeedback();
              setCreateOpen((open) => !open);
            }}
          >
            {createOpen ? "Fechar criação" : "Novo mandato"}
          </button>
        </div>
      </header>

      {success && <SuccessMessage message={success} />}
      {mutationError && <InlineError message={mutationError} />}
      {reviewRequired && (
        <div className="operation-notice operation-notice--warning" role="alert">
          <p>
            O estado autoritativo foi recarregado. Confira novamente a revisão,
            o checksum e a versão antes de habilitar outra ação.
          </p>
          <button
            className="button button--ghost button--compact"
            type="button"
            disabled={detailLoading || historyLoading || busy}
            onClick={() => {
              setReviewRequired(false);
              setMutationError(null);
            }}
          >
            Marcar estado recarregado como revisado
          </button>
        </div>
      )}

      {createOpen && (
        <section className="panel mandate-create" aria-labelledby="create-mandate-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Novo rascunho</p>
              <h2 id="create-mandate-title">Criar mandato DRAFT</h2>
            </div>
            <span>Schema {CURRENT_SCHEMA_VERSION} · Binance Spot</span>
          </div>
          <form onSubmit={prepareCreate}>
            <SpecificationFields
              draft={createDraft}
              disabled={busy}
              prefix="create"
              onChange={updateCreateDraft}
            />
            <div className="form-actions">
              <button className="button" type="submit" disabled={busy}>
                Revisar criação
              </button>
            </div>
          </form>
          {createIntent && !createDialogOpen && (
            <div className="operation-notice operation-notice--warning" role="status">
              <p>Existe um envio de criação com resposta ambígua.</p>
              <button
                className="button button--ghost button--compact"
                type="button"
                disabled={busy}
                onClick={() => void submitCreate(createIntent)}
              >
                Repetir o mesmo envio
              </button>
            </div>
          )}
        </section>
      )}

      <section className="panel" aria-labelledby="mandate-catalog-title">
        <div className="section-heading mandate-catalog-heading">
          <div>
            <p className="eyebrow">Catálogo bounded</p>
            <h2 id="mandate-catalog-title">Mandatos cadastrados</h2>
          </div>
          <label>
            Estado
            <select
              value={stateFilter}
              disabled={listLoading || busy}
              onChange={(event) =>
                changeCatalogContext(
                  event.currentTarget.value as "" | OperationalMandateState,
                  0,
                )
              }
            >
              <option value="">Todos</option>
              <option value="DRAFT">DRAFT</option>
              <option value="APPROVED">APPROVED</option>
              <option value="ARCHIVED">ARCHIVED</option>
            </select>
          </label>
        </div>
        {listLoading && !catalog ? (
          <LoadingState message="Carregando mandatos…" />
        ) : listError ? (
          <InlineError message={listError} />
        ) : catalog?.items.length === 0 ? (
          <EmptyState
            title="Nenhum mandato encontrado"
            description="Altere o filtro ou crie um novo rascunho operacional."
          />
        ) : catalog ? (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Mandato</th>
                    <th>Estado</th>
                    <th>Concorrência</th>
                    <th>Instrumentos</th>
                    <th>Criado em</th>
                    <th>Ação</th>
                  </tr>
                </thead>
                <tbody>
                  {catalog.items.map((item) => (
                    <tr key={item.mandate.mandate_id}>
                      <td>
                        <strong>{item.revision.specification.name}</strong>
                        <small className="mandate-cell-note">
                          {item.mandate.approved_at
                            ? `Aprovado ${formatDate(item.mandate.approved_at)}`
                            : item.mandate.archived_at
                              ? `Arquivado ${formatDate(item.mandate.archived_at)}`
                              : "Ainda não aprovado"}
                        </small>
                      </td>
                      <td><StateBadge state={item.mandate.state} /></td>
                      <td>rev. {item.mandate.current_revision} · versão {item.mandate.record_version}</td>
                      <td>{item.revision.specification.instruments.map((instrument) => `${instrument.base_asset}/${instrument.quote_asset}`).join(", ")}</td>
                      <td>{formatDate(item.mandate.created_at)}</td>
                      <td>
                        <button
                          className="button button--ghost button--compact"
                          type="button"
                          aria-label={`Inspecionar mandato ${item.revision.specification.name}`}
                          onClick={() => chooseMandate(item.mandate.mandate_id)}
                        >
                          Inspecionar
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              label="Paginação do catálogo de mandatos"
              limit={catalog.limit}
              offset={catalog.offset}
              total={catalog.total}
              disabled={listLoading || busy}
              onChange={(offset) => changeCatalogContext(stateFilter, offset)}
            />
          </>
        ) : null}
      </section>

      {selectedId && (
        <section className="panel mandate-detail" aria-labelledby="mandate-detail-title">
          {detailLoading && !current ? (
            <LoadingState message="Carregando mandato selecionado…" />
          ) : detailError ? (
            <InlineError message={detailError} />
          ) : current ? (
            <>
              <div className="section-heading mandate-detail-heading">
                <div>
                  <p className="eyebrow">Agregado atual</p>
                  <h2 id="mandate-detail-title">{current.revision.specification.name}</h2>
                  <code className="mandate-code">{current.mandate.mandate_id}</code>
                </div>
                <StateBadge state={current.mandate.state} />
              </div>
              <dl className="operation-detail-grid">
                <div><dt>Revisão atual</dt><dd>{current.mandate.current_revision}</dd></div>
                <div><dt>Versão do registro</dt><dd>{current.mandate.record_version}</dd></div>
                <div><dt>Criado por (UUID)</dt><dd><code className="mandate-code">{current.mandate.created_by}</code></dd></div>
                <div><dt>Criado em</dt><dd>{formatDate(current.mandate.created_at)}</dd></div>
                <div><dt>Revisão aprovada</dt><dd>{current.mandate.approved_revision ?? "—"}</dd></div>
                <div><dt>Checksum aprovado</dt><dd>{current.mandate.approved_checksum ? <code title={current.mandate.approved_checksum}>{checksumSummary(current.mandate.approved_checksum)}</code> : "—"}</dd></div>
                <div><dt>Aprovado por (UUID)</dt><dd><code className="mandate-code">{current.mandate.approved_by ?? "—"}</code></dd></div>
                <div><dt>Aprovado em</dt><dd>{formatDate(current.mandate.approved_at)}</dd></div>
                <div><dt>Arquivado por (UUID)</dt><dd><code className="mandate-code">{current.mandate.archived_by ?? "—"}</code></dd></div>
                <div><dt>Arquivado em</dt><dd>{formatDate(current.mandate.archived_at)}</dd></div>
              </dl>
              <RevisionDetails revision={current.revision} historical={false} />

              <div className="operation-controls">
                {current.mandate.state === "DRAFT" && (
                  <button
                    className="button button--ghost"
                    type="button"
                    disabled={busy || reviewRequired}
                    onClick={() => {
                      clearFeedback();
                      setReplaceDraft(toDraft(current.revision.specification));
                      setEditing(true);
                    }}
                  >
                    Substituir rascunho
                  </button>
                )}
                {current.mandate.state === "DRAFT" && (
                  <button
                    className="button"
                    type="button"
                    disabled={busy || reviewRequired}
                    onClick={() => setMutationSnapshot({ current, kind: "approve" })}
                  >
                    Aprovar revisão atual
                  </button>
                )}
                {current.mandate.state !== "ARCHIVED" && (
                  <button
                    className="button button--danger"
                    type="button"
                    disabled={busy || reviewRequired}
                    onClick={() => setMutationSnapshot({ current, kind: "archive" })}
                  >
                    Arquivar mandato
                  </button>
                )}
              </div>

              {editing && current.mandate.state === "DRAFT" && (
                <form className="mandate-replace" onSubmit={submitReplace}>
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Substituição completa</p>
                      <h3>Editar especificação DRAFT</h3>
                    </div>
                    <span>rev. {current.mandate.current_revision} · versão {current.mandate.record_version}</span>
                  </div>
                  <SpecificationFields
                    draft={replaceDraft}
                    disabled={busy}
                    prefix="replace"
                    onChange={setReplaceDraft}
                  />
                  <div className="form-actions">
                    <button
                      className="button button--ghost"
                      type="button"
                      disabled={busy}
                      onClick={() => setEditing(false)}
                    >
                      Cancelar
                    </button>
                    <button className="button" type="submit" disabled={busy || reviewRequired}>
                      {mutationBusy === "replace" ? "Substituindo…" : "Substituir com tokens exibidos"}
                    </button>
                  </div>
                </form>
              )}
            </>
          ) : null}
        </section>
      )}

      {selectedId && current && (
        <section className="panel" aria-labelledby="mandate-history-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Histórico imutável</p>
              <h2 id="mandate-history-title">Revisões do mandato</h2>
            </div>
            <span>Ordem newest-first da API</span>
          </div>
          {historyLoading && !history ? (
            <LoadingState message="Carregando revisões…" />
          ) : historyError ? (
            <InlineError message={historyError} />
          ) : history?.items.length === 0 ? (
            <EmptyState title="Sem revisões" description="Nenhum registro imutável foi retornado." />
          ) : history ? (
            <>
              <ol className="mandate-history-list">
                {history.items.map((revision) => (
                  <li key={revision.revision}>
                    <div>
                      <strong>Revisão {revision.revision} · {revision.specification.name}</strong>
                      <small>{formatDate(revision.created_at)} · {checksumSummary(revision.specification_checksum)}</small>
                    </div>
                    <button
                      className="button button--ghost button--compact"
                      type="button"
                      aria-label={`Inspecionar revisão histórica ${revision.revision}`}
                      disabled={revisionLoading}
                      onClick={() => void inspectRevision(revision.revision)}
                    >
                      Inspecionar revisão {revision.revision}
                    </button>
                  </li>
                ))}
              </ol>
              <Pagination
                label="Paginação do histórico de revisões"
                limit={history.limit}
                offset={history.offset}
                total={history.total}
                disabled={historyLoading || busy}
                onChange={(offset) => {
                  setHistoricalRevision(null);
                  setHistoryOffset(offset);
                }}
              />
            </>
          ) : null}
          {revisionLoading && <LoadingState message="Carregando revisão exata…" />}
          {revisionError && <InlineError message={revisionError} />}
          {historicalRevision && (
            <RevisionDetails revision={historicalRevision} historical />
          )}
        </section>
      )}

      <ConfirmDialog
        open={createDialogOpen && createIntent !== null}
        title="Criar novo mandato DRAFT?"
        description={
          createIntent
            ? `${createIntent.payload.specification.name} · ${createIntent.payload.specification.instruments.map((instrument) => `${instrument.base_asset}/${instrument.quote_asset}`).join(", ")}. A criação usará uma chave exclusiva deste intento.`
            : "Revise a especificação antes de criar."
        }
        confirmLabel="Criar mandato DRAFT"
        busy={mutationBusy === "create"}
        onCancel={() => {
          if (!busy) {
            setCreateDialogOpen(false);
            setCreateIntent(null);
          }
        }}
        onConfirm={() => {
          if (createIntent) void submitCreate(createIntent);
        }}
      />

      <ConfirmDialog
        open={mutationSnapshot !== null}
        title={mutationSnapshot?.kind === "approve" ? "Aprovar revisão exata?" : "Arquivar mandato?"}
        description={
          mutationSnapshot?.kind === "approve"
            ? `${mutationSnapshot.current.revision.specification.name} · revisão ${mutationSnapshot.current.revision.revision} · checksum ${checksumSummary(mutationSnapshot.current.revision.specification_checksum)} · ${mutationSnapshot.current.revision.specification.instruments.map(instrumentLabel).join(", ")}.`
            : mutationSnapshot
              ? `${mutationSnapshot.current.revision.specification.name} · versão de registro ${mutationSnapshot.current.mandate.record_version}.`
              : "Confirme a ação."
        }
        confirmLabel={mutationSnapshot?.kind === "approve" ? "Aprovar esta revisão" : "Arquivar mandato"}
        danger={mutationSnapshot?.kind === "archive"}
        busy={mutationBusy === "approve" || mutationBusy === "archive"}
        onCancel={() => {
          if (!busy) setMutationSnapshot(null);
        }}
        onConfirm={() => void confirmMutation()}
      />
    </div>
  );
}
