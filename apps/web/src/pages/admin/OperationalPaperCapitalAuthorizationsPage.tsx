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
  OperationalPaperCapitalAuthorization,
  OperationalPaperCapitalAuthorizationCreateRequest,
  OperationalPaperCapitalAuthorizationList,
  OperationalPaperCapitalAuthorizationState,
  OperationalPaperSessionProfileCurrent,
  OperationalPaperSessionProfileList,
  SimulationListItem,
} from "../../types/api";
import {
  FINANCIAL_DECIMAL_MAX_EXCLUSIVE,
  validateFinancialDecimal,
} from "../../utils/decimal";
import { formatDate, getErrorMessage } from "../../utils/format";

const PAGE_SIZE = 20;
const PROFILE_PAGE_SIZE = 100;
const SIMULATION_PAGE_SIZE = 100;
const COMPACT_GHOST = "button button--ghost button--compact";

const STATE_LABELS: Record<OperationalPaperCapitalAuthorizationState, string> = {
  AUTHORIZED: "Autorizada",
  REVOKED: "Revogada",
};

interface ConfirmedCreateIntent {
  payload: OperationalPaperCapitalAuthorizationCreateRequest;
  profileLabel: string;
  simulationLabel: string;
}

interface RevokeSnapshot {
  authorizationId: string;
  expectedRecordVersion: number;
  capitalLabel: string;
}

function safeError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return "Acesso administrativo negado.";
    if (error.status === 404)
      return "A autorização selecionada não está mais disponível.";
    if (error.status === 409)
      return "A autorização mudou no servidor. Revise o estado recarregado antes de tentar novamente.";
    if (error.status === 422)
      return "O servidor rejeitou os dados informados. Revise o intento.";
  }
  return getErrorMessage(error, fallback);
}

function isSelectableApprovedProfile(
  current: OperationalPaperSessionProfileCurrent,
): boolean {
  const { profile, revision } = current;
  return (
    profile.state === "APPROVED" &&
    typeof profile.approved_revision === "number" &&
    profile.approved_revision > 0 &&
    typeof profile.approved_checksum === "string" &&
    profile.approved_checksum.length > 0 &&
    revision.revision === profile.approved_revision &&
    revision.specification_checksum === profile.approved_checksum &&
    revision.specification.name.trim().length > 0 &&
    revision.specification.selected_instrument.quote_asset.trim().length > 0
  );
}

function profileLabel(current: OperationalPaperSessionProfileCurrent): string {
  const { profile, revision } = current;
  const instrument = revision.specification.selected_instrument;
  return `${revision.specification.name} · ${instrument.base_asset}/${instrument.quote_asset} · rev. ${profile.approved_revision}`;
}

function simulationLabel(simulation: SimulationListItem): string {
  return `${simulation.name} · ${simulation.currency} · ${formatDate(simulation.started_at)}`;
}

function StateBadge({
  state,
}: {
  state: OperationalPaperCapitalAuthorizationState;
}) {
  return (
    <span
      className={`operation-state ${
        state === "AUTHORIZED" ? "operation-state--success" : ""
      }`.trim()}
    >
      {STATE_LABELS[state]} ({state})
    </span>
  );
}

function OffsetPagination({
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
  const currentPage = total === 0 ? 0 : offset / limit + 1;
  const totalPages = total === 0 ? 0 : Math.ceil(total / limit);
  return (
    <nav className="operation-pagination" aria-label={label}>
      <button
        className={COMPACT_GHOST}
        type="button"
        disabled={disabled || offset === 0}
        onClick={() => onChange(offset >= limit ? offset - limit : 0)}
      >
        Anterior
      </button>
      <span>
        Página {currentPage} de {totalPages} · {total} registro(s)
      </span>
      <button
        className={COMPACT_GHOST}
        type="button"
        disabled={disabled || offset + limit >= total}
        onClick={() => onChange(offset + limit)}
      >
        Próxima
      </button>
    </nav>
  );
}

function ExactValue({ value }: { value: string | number | null }) {
  return <code className="mandate-code">{value ?? "—"}</code>;
}

export function OperationalPaperCapitalAuthorizationsPage() {
  const mountedRef = useRef(true);
  const listSequenceRef = useRef(0);
  const detailSequenceRef = useRef(0);
  const profileSequenceRef = useRef(0);
  const simulationSequenceRef = useRef(0);

  const [stateFilter, setStateFilter] = useState<
    "" | OperationalPaperCapitalAuthorizationState
  >("");
  const [listOffset, setListOffset] = useState(0);
  const [catalog, setCatalog] =
    useState<OperationalPaperCapitalAuthorizationList | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] =
    useState<OperationalPaperCapitalAuthorization | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [profileOffset, setProfileOffset] = useState(0);
  const [profileCatalog, setProfileCatalog] =
    useState<OperationalPaperSessionProfileList | null>(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [selectedProfile, setSelectedProfile] =
    useState<OperationalPaperSessionProfileCurrent | null>(null);
  const [simulations, setSimulations] = useState<SimulationListItem[]>([]);
  const [simulationLoading, setSimulationLoading] = useState(false);
  const [simulationError, setSimulationError] = useState<string | null>(null);
  const [selectedSimulation, setSelectedSimulation] =
    useState<SimulationListItem | null>(null);
  const [authorizedCapital, setAuthorizedCapital] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [confirmedCreate, setConfirmedCreate] =
    useState<ConfirmedCreateIntent | null>(null);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);

  const [revokeSnapshot, setRevokeSnapshot] =
    useState<RevokeSnapshot | null>(null);
  const [revokeReviewRequired, setRevokeReviewRequired] = useState(false);
  const [mutationBusy, setMutationBusy] = useState<"create" | "revoke" | null>(
    null,
  );
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const busy = mutationBusy !== null;
  const selectableProfiles = (profileCatalog?.items ?? []).filter(
    isSelectableApprovedProfile,
  );

  const loadCatalog = useCallback(
    async (
      offset = listOffset,
      state: "" | OperationalPaperCapitalAuthorizationState = stateFilter,
    ) => {
      const sequence = ++listSequenceRef.current;
      setListLoading(true);
      setListError(null);
      try {
        const response =
          await apiClient.listOperationalPaperCapitalAuthorizations({
            limit: PAGE_SIZE,
            offset,
            state: state || undefined,
          });
        if (!mountedRef.current || sequence !== listSequenceRef.current) return;
        setCatalog(response);
      } catch (error) {
        if (mountedRef.current && sequence === listSequenceRef.current)
          setListError(
            safeError(error, "Não foi possível carregar as autorizações."),
          );
      } finally {
        if (mountedRef.current && sequence === listSequenceRef.current)
          setListLoading(false);
      }
    },
    [listOffset, stateFilter],
  );

  const loadDetail = useCallback(
    async (authorizationId: string) => {
      const sequence = ++detailSequenceRef.current;
      setSelectedId(authorizationId);
      setDetail(null);
      setDetailLoading(true);
      setDetailError(null);
      try {
        const response =
          await apiClient.getOperationalPaperCapitalAuthorization(
            authorizationId,
          );
        if (!mountedRef.current || sequence !== detailSequenceRef.current)
          return;
        setDetail(response);
      } catch (error) {
        if (!mountedRef.current || sequence !== detailSequenceRef.current)
          return;
        setDetailError(safeError(error, "Não foi possível carregar o detalhe."));
        if (error instanceof ApiError && error.status === 404) {
          setSelectedId(null);
          setDetail(null);
          void loadCatalog();
        }
      } finally {
        if (mountedRef.current && sequence === detailSequenceRef.current)
          setDetailLoading(false);
      }
    },
    [loadCatalog],
  );

  const loadProfiles = useCallback(async (offset = profileOffset) => {
    const sequence = ++profileSequenceRef.current;
    setProfileLoading(true);
    setProfileError(null);
    try {
      const response = await apiClient.listOperationalPaperSessionProfiles({
        limit: PROFILE_PAGE_SIZE,
        offset,
        state: "APPROVED",
      });
      if (!mountedRef.current || sequence !== profileSequenceRef.current)
        return;
      setProfileCatalog(response);
    } catch (error) {
      if (mountedRef.current && sequence === profileSequenceRef.current)
        setProfileError(
          safeError(error, "Não foi possível carregar os perfis aprovados."),
        );
    } finally {
      if (mountedRef.current && sequence === profileSequenceRef.current)
        setProfileLoading(false);
    }
  }, [profileOffset]);

  const loadSimulations = useCallback(async () => {
    const sequence = ++simulationSequenceRef.current;
    setSimulationLoading(true);
    setSimulationError(null);
    try {
      const response = await apiClient.listSimulations(1, SIMULATION_PAGE_SIZE);
      if (!mountedRef.current || sequence !== simulationSequenceRef.current)
        return;
      setSimulations(response.items.filter((item) => item.status === "ACTIVE"));
    } catch (error) {
      if (mountedRef.current && sequence === simulationSequenceRef.current)
        setSimulationError(
          safeError(error, "Não foi possível carregar a simulação ativa."),
        );
    } finally {
      if (mountedRef.current && sequence === simulationSequenceRef.current)
        setSimulationLoading(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      listSequenceRef.current += 1;
      detailSequenceRef.current += 1;
      profileSequenceRef.current += 1;
      simulationSequenceRef.current += 1;
    };
  }, []);

  useEffect(() => {
    void loadCatalog();
  }, [loadCatalog]);

  useEffect(() => {
    if (!createOpen) return;
    void loadProfiles();
  }, [createOpen, loadProfiles]);

  useEffect(() => {
    if (!createOpen) return;
    void loadSimulations();
  }, [createOpen, loadSimulations]);

  const clearConfirmedCreate = () => {
    setConfirmedCreate(null);
    setCreateDialogOpen(false);
    setFormError(null);
  };

  const resetCreate = () => {
    setSelectedProfile(null);
    setSelectedSimulation(null);
    setAuthorizedCapital("");
    setProfileOffset(0);
    setConfirmedCreate(null);
    setCreateDialogOpen(false);
    setFormError(null);
  };

  const refreshAuthoritative = useCallback(async () => {
    const requests: Promise<unknown>[] = [loadCatalog()];
    if (selectedId) requests.push(loadDetail(selectedId));
    if (createOpen) {
      requests.push(loadProfiles());
      requests.push(loadSimulations());
    }
    await Promise.all(requests);
  }, [
    createOpen,
    loadCatalog,
    loadDetail,
    loadProfiles,
    loadSimulations,
    selectedId,
  ]);

  const prepareCreate = (event: FormEvent) => {
    event.preventDefault();
    setMutationError(null);
    setSuccess(null);
    if (!selectedProfile || !isSelectableApprovedProfile(selectedProfile)) {
      setFormError("Selecione um perfil APPROVED válido.");
      return;
    }
    if (!selectedSimulation || selectedSimulation.status !== "ACTIVE") {
      setFormError("Selecione a simulação ACTIVE retornada pelo servidor.");
      return;
    }
    const capitalError = validateFinancialDecimal(authorizedCapital);
    if (capitalError === "range") {
      setFormError(
        `O capital autorizado deve ser menor que ${FINANCIAL_DECIMAL_MAX_EXCLUSIVE}.`,
      );
      return;
    }
    if (capitalError !== null) {
      setFormError(
        "Use capital positivo em decimal base 10, com ponto e até 8 casas.",
      );
      return;
    }

    const profile = selectedProfile.profile;
    const snapshot: ConfirmedCreateIntent = {
      payload: {
        intent: {
          profile_binding: {
            profile_id: profile.profile_id,
            approved_revision: profile.approved_revision as number,
            specification_checksum: profile.approved_checksum as string,
          },
          simulation_id: selectedSimulation.id,
          quote_asset: selectedSimulation.currency,
          authorized_capital: authorizedCapital,
        },
        idempotency_key: crypto.randomUUID(),
      },
      profileLabel: profileLabel(selectedProfile),
      simulationLabel: simulationLabel(selectedSimulation),
    };
    setFormError(null);
    setConfirmedCreate(snapshot);
    setCreateDialogOpen(true);
  };

  const submitCreate = async (snapshot: ConfirmedCreateIntent) => {
    setMutationBusy("create");
    setMutationError(null);
    try {
      const created =
        await apiClient.createOperationalPaperCapitalAuthorization(
          snapshot.payload,
        );
      if (!mountedRef.current) return;
      setConfirmedCreate(null);
      setCreateDialogOpen(false);
      setCreateOpen(false);
      resetCreate();
      setSuccess("Autorização criada e recarregada pela autoridade backend.");
      setStateFilter("");
      setListOffset(0);
      setDetail(created);
      setSelectedId(created.authorization_id);
      await Promise.all([
        loadCatalog(0, ""),
        loadDetail(created.authorization_id),
      ]);
    } catch (error) {
      if (!mountedRef.current) return;
      setCreateDialogOpen(false);
      if (error instanceof ApiError && error.status === 0) {
        setMutationError(
          "A resposta da criação não foi confirmada. Repita somente este mesmo envio para reutilizar a chave de idempotência.",
        );
      } else {
        setConfirmedCreate(null);
        setMutationError(
          safeError(error, "Não foi possível criar a autorização."),
        );
        if (error instanceof ApiError && error.status === 409)
          await Promise.all([
            loadCatalog(),
            loadProfiles(),
            loadSimulations(),
          ]);
      }
    } finally {
      if (mountedRef.current) setMutationBusy(null);
    }
  };

  const beginRevoke = (authorization: OperationalPaperCapitalAuthorization) => {
    if (authorization.state !== "AUTHORIZED" || revokeReviewRequired) return;
    setMutationError(null);
    setSuccess(null);
    setRevokeSnapshot({
      authorizationId: authorization.authorization_id,
      expectedRecordVersion: authorization.record_version,
      capitalLabel: `${authorization.authorized_capital} ${authorization.quote_asset}`,
    });
  };

  const submitRevoke = async () => {
    const snapshot = revokeSnapshot;
    if (!snapshot || revokeReviewRequired) return;
    setMutationBusy("revoke");
    setMutationError(null);
    try {
      await apiClient.revokeOperationalPaperCapitalAuthorization(
        snapshot.authorizationId,
        { expected_record_version: snapshot.expectedRecordVersion },
      );
      if (!mountedRef.current) return;
      setRevokeSnapshot(null);
      setRevokeReviewRequired(false);
      setSuccess("Revogação confirmada e estado autoritativo recarregado.");
      await Promise.all([
        loadCatalog(),
        selectedId === snapshot.authorizationId
          ? loadDetail(snapshot.authorizationId)
          : Promise.resolve(),
      ]);
    } catch (error) {
      if (!mountedRef.current) return;
      setRevokeSnapshot(null);
      setMutationError(
        safeError(error, "Não foi possível revogar a autorização."),
      );
      if (error instanceof ApiError && error.status === 409) {
        setRevokeReviewRequired(true);
        await Promise.all([
          loadCatalog(),
          loadDetail(snapshot.authorizationId),
        ]);
      }
    } finally {
      if (mountedRef.current) setMutationBusy(null);
    }
  };

  return (
    <div>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Reserva administrativa de capital paper</p>
          <h1>Autorizações de capital operacional</h1>
          <p>
            Vincule capital simulado a um perfil aprovado sem materializar ou
            executar uma sessão paper.
          </p>
        </div>
        <div className="form-actions">
          <button
            className="button button--ghost"
            type="button"
            disabled={listLoading || busy}
            onClick={() => void refreshAuthoritative()}
          >
            Atualizar autorizações
          </button>
          <button
            className="button"
            type="button"
            disabled={busy}
            onClick={() => {
              setCreateOpen((open) => !open);
              setMutationError(null);
              setSuccess(null);
              if (createOpen) resetCreate();
            }}
          >
            {createOpen ? "Fechar criação" : "Nova autorização"}
          </button>
        </div>
      </div>

      {success && <SuccessMessage message={success} />}
      {mutationError && <InlineError message={mutationError} />}
      {revokeReviewRequired && (
        <div
          className="operation-notice operation-notice--warning"
          role="status"
        >
          <p>
            O estado da autorização foi recarregado após conflito. Revise a
            versão e o estado antes de iniciar uma nova revogação.
          </p>
          <button
            className={COMPACT_GHOST}
            type="button"
            onClick={() => setRevokeReviewRequired(false)}
          >
            Marcar estado recarregado como revisado
          </button>
        </div>
      )}

      {createOpen && (
        <section className="panel form-panel" aria-labelledby="create-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Intento administrativo</p>
              <h2 id="create-title">Criar autorização</h2>
            </div>
            <span>Seleções autoritativas · capital decimal exato</span>
          </div>
          <form className="form-grid" onSubmit={prepareCreate}>
            <label className="form-grid__wide">
              Perfil paper APPROVED
              <select
                value={selectedProfile?.profile.profile_id ?? ""}
                required
                disabled={profileLoading || busy}
                onChange={(event) => {
                  const selected = selectableProfiles.find(
                    (item) => item.profile.profile_id === event.target.value,
                  );
                  setSelectedProfile(selected ?? null);
                  clearConfirmedCreate();
                }}
              >
                <option value="">Selecione um perfil aprovado</option>
                {selectableProfiles.map((item) => (
                  <option
                    key={item.profile.profile_id}
                    value={item.profile.profile_id}
                  >
                    {profileLabel(item)}
                  </option>
                ))}
              </select>
            </label>
            {profileLoading && !profileCatalog && (
              <div className="form-grid__wide">
                <LoadingState message="Carregando perfis aprovados…" />
              </div>
            )}
            {profileError && (
              <div className="form-grid__wide">
                <InlineError message={profileError} />
              </div>
            )}
            {profileCatalog && (
              <div className="form-grid__wide">
                <OffsetPagination
                  label="Paginação do seletor de perfis aprovados"
                  limit={profileCatalog.limit}
                  offset={profileCatalog.offset}
                  total={profileCatalog.total}
                  disabled={profileLoading || busy}
                  onChange={(offset) => {
                    setSelectedProfile(null);
                    clearConfirmedCreate();
                    setProfileOffset(offset);
                  }}
                />
              </div>
            )}

            <label className="form-grid__wide">
              Simulação ACTIVE mais recente
              <select
                value={selectedSimulation?.id ?? ""}
                required
                disabled={simulationLoading || busy}
                onChange={(event) => {
                  const selected = simulations.find(
                    (item) => item.id === event.target.value,
                  );
                  setSelectedSimulation(selected ?? null);
                  clearConfirmedCreate();
                }}
              >
                <option value="">Selecione a simulação ativa</option>
                {simulations.map((simulation) => (
                  <option key={simulation.id} value={simulation.id}>
                    {simulationLabel(simulation)}
                  </option>
                ))}
              </select>
            </label>
            {simulationLoading && simulations.length === 0 && (
              <div className="form-grid__wide">
                <LoadingState message="Carregando simulações…" />
              </div>
            )}
            {simulationError && (
              <div className="form-grid__wide">
                <InlineError message={simulationError} />
              </div>
            )}

            <label>
              Capital autorizado
              <input
                inputMode="decimal"
                value={authorizedCapital}
                required
                disabled={busy}
                placeholder="100.12345678"
                onChange={(event) => {
                  setAuthorizedCapital(event.target.value);
                  clearConfirmedCreate();
                }}
              />
            </label>
            <p className="form-hint form-grid__wide">
              Informe somente o capital. Perfil, revisão, checksum, simulação e
              moeda são derivados das seleções retornadas pelo backend.
            </p>
            {selectedProfile && selectedSimulation && (
              <div className="operation-notice form-grid__wide" role="status">
                <p>
                  Perfil: {profileLabel(selectedProfile)}. Simulação:{" "}
                  {simulationLabel(selectedSimulation)}. Moeda derivada:{" "}
                  {selectedSimulation.currency}.
                </p>
              </div>
            )}
            {formError && (
              <div className="form-grid__wide">
                <InlineError message={formError} />
              </div>
            )}
            <div className="form-actions form-grid__wide">
              <button className="button" type="submit" disabled={busy}>
                Revisar autorização
              </button>
            </div>
          </form>
          {confirmedCreate && !createDialogOpen && (
            <div
              className="operation-notice operation-notice--warning"
              role="status"
            >
              <p>Existe um envio de criação com resposta ambígua.</p>
              <button
                className={COMPACT_GHOST}
                type="button"
                disabled={busy}
                onClick={() => void submitCreate(confirmedCreate)}
              >
                Repetir o mesmo envio
              </button>
            </div>
          )}
        </section>
      )}

      <section className="panel" aria-labelledby="catalog-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Catálogo bounded</p>
            <h2 id="catalog-title">Autorizações registradas</h2>
          </div>
          <label>
            Estado da autorização
            <select
              value={stateFilter}
              disabled={listLoading || busy}
              onChange={(event) => {
                setStateFilter(
                  event.target.value as
                    | ""
                    | OperationalPaperCapitalAuthorizationState,
                );
                setListOffset(0);
              }}
            >
              <option value="">ALL</option>
              <option value="AUTHORIZED">AUTHORIZED</option>
              <option value="REVOKED">REVOKED</option>
            </select>
          </label>
        </div>
        {listLoading && !catalog ? (
          <LoadingState message="Carregando autorizações…" />
        ) : listError ? (
          <InlineError message={listError} />
        ) : catalog?.items.length === 0 ? (
          <EmptyState
            title="Nenhuma autorização encontrada"
            description="Altere o filtro ou crie uma nova autorização de capital paper."
          />
        ) : catalog ? (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Capital</th>
                    <th>Estado</th>
                    <th>Perfil</th>
                    <th>Simulação</th>
                    <th>Versão</th>
                    <th>Datas</th>
                    <th>Ações</th>
                  </tr>
                </thead>
                <tbody>
                  {catalog.items.map((authorization) => (
                    <tr key={authorization.authorization_id}>
                      <td>
                        <strong>
                          {authorization.authorized_capital}{" "}
                          {authorization.quote_asset}
                        </strong>
                      </td>
                      <td><StateBadge state={authorization.state} /></td>
                      <td>
                        <code className="mandate-code">
                          {authorization.profile_binding.profile_id}
                        </code>
                        <small>
                          rev. {authorization.profile_binding.approved_revision}
                        </small>
                      </td>
                      <td>
                        <code className="mandate-code">
                          {authorization.simulation_id}
                        </code>
                      </td>
                      <td>{authorization.record_version}</td>
                      <td>
                        <small>Criada {formatDate(authorization.created_at)}</small>
                        {authorization.revoked_at && (
                          <small>
                            Revogada {formatDate(authorization.revoked_at)}
                          </small>
                        )}
                      </td>
                      <td>
                        <div className="operation-controls">
                          <button
                            className={COMPACT_GHOST}
                            type="button"
                            aria-label={`Inspecionar autorização ${authorization.authorization_id}`}
                            disabled={busy}
                            onClick={() => void loadDetail(authorization.authorization_id)}
                          >
                            Inspecionar
                          </button>
                          {authorization.state === "AUTHORIZED" && (
                            <button
                              className="button button--danger button--compact"
                              type="button"
                              disabled={busy || revokeReviewRequired}
                              aria-label={`Revogar autorização ${authorization.authorization_id}`}
                              onClick={() => beginRevoke(authorization)}
                            >
                              Revogar
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <OffsetPagination
              label="Paginação do catálogo de autorizações"
              limit={catalog.limit}
              offset={catalog.offset}
              total={catalog.total}
              disabled={listLoading || busy}
              onChange={setListOffset}
            />
          </>
        ) : null}
      </section>

      {selectedId && (
        <section className="panel mandate-detail" aria-labelledby="detail-title">
          {detailLoading && !detail ? (
            <LoadingState message="Carregando detalhe autoritativo…" />
          ) : detailError ? (
            <InlineError message={detailError} />
          ) : detail ? (
            <>
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Auditoria pública completa</p>
                  <h2 id="detail-title">Autorização selecionada</h2>
                </div>
                <StateBadge state={detail.state} />
              </div>
              <dl className="operation-detail-grid">
                <div><dt>Authorization ID</dt><dd><ExactValue value={detail.authorization_id} /></dd></div>
                <div><dt>Schema version</dt><dd>{detail.schema_version}</dd></div>
                <div><dt>State</dt><dd>{detail.state}</dd></div>
                <div><dt>Record version</dt><dd>{detail.record_version}</dd></div>
                <div><dt>Profile ID</dt><dd><ExactValue value={detail.profile_binding.profile_id} /></dd></div>
                <div><dt>Approved revision</dt><dd>{detail.profile_binding.approved_revision}</dd></div>
                <div><dt>Specification checksum</dt><dd><ExactValue value={detail.profile_binding.specification_checksum} /></dd></div>
                <div><dt>Simulation ID</dt><dd><ExactValue value={detail.simulation_id} /></dd></div>
                <div><dt>Quote asset</dt><dd>{detail.quote_asset}</dd></div>
                <div><dt>Authorized capital</dt><dd><ExactValue value={detail.authorized_capital} /></dd></div>
                <div><dt>Authorization checksum</dt><dd><ExactValue value={detail.authorization_checksum} /></dd></div>
                <div><dt>Created by</dt><dd><ExactValue value={detail.created_by} /></dd></div>
                <div><dt>Created at</dt><dd><ExactValue value={detail.created_at} /></dd></div>
                <div><dt>Revoked by</dt><dd><ExactValue value={detail.revoked_by} /></dd></div>
                <div><dt>Revoked at</dt><dd><ExactValue value={detail.revoked_at} /></dd></div>
              </dl>
            </>
          ) : null}
        </section>
      )}

      <ConfirmDialog
        open={createDialogOpen && confirmedCreate !== null}
        title="Criar esta autorização?"
        description={
          confirmedCreate
            ? `Perfil congelado: ${confirmedCreate.profileLabel}. Simulação congelada: ${confirmedCreate.simulationLabel}. Capital: ${confirmedCreate.payload.intent.authorized_capital} ${confirmedCreate.payload.intent.quote_asset}.`
            : "Revise o intento exato."
        }
        confirmLabel="Criar autorização"
        busy={mutationBusy === "create"}
        onCancel={() => {
          if (!busy) {
            setCreateDialogOpen(false);
            setConfirmedCreate(null);
          }
        }}
        onConfirm={() => {
          if (confirmedCreate) void submitCreate(confirmedCreate);
        }}
      />

      <ConfirmDialog
        open={revokeSnapshot !== null}
        title="Revogar esta autorização?"
        description={
          revokeSnapshot
            ? `Autorização ${revokeSnapshot.authorizationId} · ${revokeSnapshot.capitalLabel} · versão esperada ${revokeSnapshot.expectedRecordVersion}.`
            : "Revise a autorização."
        }
        confirmLabel="Revogar autorização"
        danger
        busy={mutationBusy === "revoke"}
        onCancel={() => {
          if (!busy) setRevokeSnapshot(null);
        }}
        onConfirm={() => void submitRevoke()}
      />
    </div>
  );
}
