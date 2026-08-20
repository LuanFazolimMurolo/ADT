import { useCallback, useEffect, useRef, useState } from "react";
import { EmptyState, InlineError, LoadingState } from "../../components/States";
import { apiClient } from "../../http/client";
import type {
  WorkerRuntime,
  WorkerRuntimeEvent,
  WorkerRuntimeEventList,
  WorkerRuntimeHealthState,
  WorkerRuntimeList,
} from "../../types/api";
import { getErrorMessage } from "../../utils/format";

const POLL_INTERVAL_MS = 30_000;
const RUNTIME_LIMIT = 20;
const EVENT_LIMIT = 50;

const healthLabels: Record<WorkerRuntimeHealthState, string> = {
  HEALTHY: "Heartbeat recente",
  STALE: "Heartbeat atrasado",
  STOPPED: "Parado confirmado",
  FAILED: "Falha confirmada",
};

const eventLabels: Record<WorkerRuntimeEvent["event_type"], string> = {
  RUNTIME_STARTED: "Runtime iniciado",
  RUNTIME_STOPPED: "Runtime encerrado",
  RUNTIME_FAILED: "Falha de runtime",
  OPERATION_SETTLED: "Operação liquidada",
};

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

function healthClass(health: WorkerRuntimeHealthState): string {
  if (health === "HEALTHY") {
    return "paper-status paper-status--success";
  }

  if (health === "STALE") {
    return "paper-status paper-status--warning";
  }

  if (health === "FAILED") {
    return "paper-status paper-status--danger";
  }

  return "paper-status";
}

function activityLabel(runtime: WorkerRuntime): string {
  return runtime.activity_state === "ACTIVE" ? "Ativo" : "Ocioso";
}

function failureLabel(runtime: WorkerRuntime): string {
  if (!runtime.failure_code) return "—";

  return runtime.failure_code.replace(/_/g, " ");
}

function operationStateLabel(event: WorkerRuntimeEvent): string {
  if (!event.operation_state) return "—";

  return event.operation_state.replace(/_/g, " ");
}

export function WorkerObservabilityPage() {
  const [runtimes, setRuntimes] = useState<WorkerRuntimeList | null>(null);
  const [events, setEvents] = useState<WorkerRuntimeEventList | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mountedRef = useRef(true);
  const sequenceRef = useRef(0);
  const inFlightRef = useRef(false);
  const hasLoadedRef = useRef(false);

  const loadObservability = useCallback(async () => {
    if (inFlightRef.current) return;

    inFlightRef.current = true;
    setRefreshing(true);

    if (!hasLoadedRef.current) {
      setLoading(true);
    }

    const sequence = ++sequenceRef.current;

    try {
      const [runtimeResponse, eventResponse] = await Promise.all([
        apiClient.listWorkerRuntimes(RUNTIME_LIMIT),
        apiClient.listWorkerRuntimeEvents(EVENT_LIMIT),
      ]);

      if (!mountedRef.current || sequence !== sequenceRef.current) {
        return;
      }

      setRuntimes(runtimeResponse);
      setEvents(eventResponse);
      setError(null);
      hasLoadedRef.current = true;
    } catch (caught) {
      if (!mountedRef.current || sequence !== sequenceRef.current) {
        return;
      }

      setError(
        getErrorMessage(
          caught,
          "Não foi possível carregar a observabilidade do worker.",
        ),
      );
    } finally {
      if (sequence === sequenceRef.current) {
        inFlightRef.current = false;

        if (mountedRef.current) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;

    void loadObservability();

    const timer = window.setInterval(
      () => void loadObservability(),
      POLL_INTERVAL_MS,
    );

    return () => {
      mountedRef.current = false;
      sequenceRef.current += 1;
      inFlightRef.current = false;
      window.clearInterval(timer);
    };
  }, [loadObservability]);

  const latestRuntime = runtimes?.items[0] ?? null;

  return (
    <div className="worker-observability-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Phase 7 · Runtime observability</p>
          <h1>Worker de market data</h1>
          <p>
            Presença, heartbeat e eventos persistidos do worker. Esta superfície
            é somente leitura e não controla o processo.
          </p>
        </div>

        <button
          className="button button--ghost"
          type="button"
          onClick={() => void loadObservability()}
          disabled={refreshing}
        >
          {refreshing ? "Atualizando…" : "Atualizar"}
        </button>
      </div>

      <div className="security-note worker-observability-note">
        <span aria-hidden="true">◇</span>
        <p>
          <strong>Heartbeat atrasado não confirma processo morto.</strong>O
          estado STALE informa apenas que o heartbeat persistido excedeu o
          limite observado. STOPPED e FAILED representam terminações
          confirmadas.
        </p>
      </div>

      {loading && !runtimes && !events ? (
        <LoadingState message="Carregando observabilidade do worker…" />
      ) : (
        <>
          {error && <InlineError message={error} />}

          <div className="metrics-grid worker-observability-metrics">
            <article className="metric-card">
              <span className="metric-label">Estado observado</span>
              <strong>
                {latestRuntime
                  ? healthLabels[latestRuntime.health_state]
                  : "Sem runtime"}
              </strong>
              <small>
                {latestRuntime
                  ? formatUtc(latestRuntime.heartbeat_at)
                  : "Nenhum registro retornado"}
              </small>
            </article>

            <article className="metric-card">
              <span className="metric-label">Atividade</span>
              <strong>
                {latestRuntime ? activityLabel(latestRuntime) : "—"}
              </strong>
              <small>
                {latestRuntime?.lifecycle_state ?? "Sem lifecycle observado"}
              </small>
            </article>

            <article className="metric-card">
              <span className="metric-label">Runtimes recentes</span>
              <strong>{runtimes?.count ?? 0}</strong>
              <small>
                Limite de stale:{" "}
                {runtimes ? `${runtimes.stale_after_seconds}s` : "indisponível"}
              </small>
            </article>

            <article className="metric-card">
              <span className="metric-label">Eventos recentes</span>
              <strong>{events?.count ?? 0}</strong>
              <small>
                Observado em {events ? formatUtc(events.observed_at) : "—"}
              </small>
            </article>
          </div>

          <section className="panel" aria-labelledby="worker-runtimes-title">
            <div className="section-heading">
              <div>
                <h2 id="worker-runtimes-title">Runtimes persistidos</h2>
                <p>
                  Estados sanitizados, sem UUID interno, hostname, PID ou
                  caminhos locais.
                </p>
              </div>
              <span>Máximo {RUNTIME_LIMIT}</span>
            </div>

            {!runtimes || runtimes.items.length === 0 ? (
              <EmptyState
                title="Nenhum runtime persistido"
                description="Ainda não há presença de worker disponível para observação."
              />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Saúde</th>
                      <th>Lifecycle</th>
                      <th>Atividade</th>
                      <th>Iniciado</th>
                      <th>Heartbeat</th>
                      <th>Encerrado</th>
                      <th>Falha</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runtimes.items.map((runtime, index) => (
                      <tr
                        key={`${runtime.started_at}-${runtime.heartbeat_at}-${index}`}
                      >
                        <td>
                          <span className={healthClass(runtime.health_state)}>
                            {healthLabels[runtime.health_state]}
                          </span>
                        </td>
                        <td>{runtime.lifecycle_state}</td>
                        <td>{activityLabel(runtime)}</td>
                        <td>{formatUtc(runtime.started_at)}</td>
                        <td>{formatUtc(runtime.heartbeat_at)}</td>
                        <td>{formatUtc(runtime.stopped_at)}</td>
                        <td>{failureLabel(runtime)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="panel" aria-labelledby="worker-events-title">
            <div className="section-heading">
              <div>
                <h2 id="worker-events-title">Eventos operacionais</h2>
                <p>
                  Histórico bounded e sanitizado das transições persistidas.
                </p>
              </div>
              <span>Máximo {EVENT_LIMIT}</span>
            </div>

            {!events || events.items.length === 0 ? (
              <EmptyState
                title="Nenhum evento persistido"
                description="Ainda não há eventos operacionais disponíveis."
              />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Evento</th>
                      <th>Ocorrido em</th>
                      <th>Operação</th>
                      <th>Estado da operação</th>
                    </tr>
                  </thead>
                  <tbody>
                    {events.items.map((event) => (
                      <tr key={event.event_id}>
                        <td>
                          <strong>{eventLabels[event.event_type]}</strong>
                          <small>#{event.event_id}</small>
                        </td>
                        <td>{formatUtc(event.occurred_at)}</td>
                        <td>
                          {event.operation_id ? (
                            <code>{event.operation_id}</code>
                          ) : (
                            "—"
                          )}
                        </td>
                        <td>{operationStateLabel(event)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
