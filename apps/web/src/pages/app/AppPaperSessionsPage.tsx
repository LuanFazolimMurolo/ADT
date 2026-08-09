import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { EmptyState, InlineError, LoadingState } from "../../components/States";
import { Pagination } from "../../components/Pagination";
import { apiClient } from "../../http/client";
import type { AppPaperSessionCatalogResponse, PageMeta } from "../../types/api";

const PAGE_SIZE = 20;
const SAFE_LOAD_ERROR =
  "Não foi possível carregar as sessões autorizadas. Tente novamente.";

function abbreviatedSessionId(value: string): string {
  return `${value.slice(0, 12)}…`;
}

export function AppPaperSessionsPage() {
  const [page, setPage] = useState(1);
  const [catalog, setCatalog] = useState<AppPaperSessionCatalogResponse | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestSequence = useRef(0);

  const load = useCallback(
    async (initial: boolean) => {
      const sequence = ++requestSequence.current;
      if (initial) {
        setLoading(true);
        setCatalog(null);
      } else {
        setRefreshing(true);
      }
      try {
        const result = await apiClient.getAppPaperSessions(page, PAGE_SIZE);
        if (sequence !== requestSequence.current) return;
        setCatalog(result);
        setError(null);
      } catch {
        if (sequence !== requestSequence.current) return;
        setError(SAFE_LOAD_ERROR);
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
    return () => {
      requestSequence.current += 1;
    };
  }, [load]);

  const pagination: PageMeta | null = catalog
    ? {
        page: catalog.page,
        page_size: catalog.page_size,
        total: catalog.total,
        total_pages: catalog.total_pages,
      }
    : null;

  return (
    <section className="page-stack" aria-labelledby="app-sessions-title">
      <header className="page-heading paper-dashboard-heading">
        <div>
          <p className="eyebrow">Catálogo autorizado</p>
          <h1 id="app-sessions-title">Sessões de paper trading</h1>
          <p>
            Seleção read-only definida exclusivamente pela autorização do
            backend.
          </p>
        </div>
        <div className="paper-dashboard-actions">
          <button
            className="button button--ghost"
            type="button"
            disabled={loading || refreshing}
            onClick={() => void load(false)}
            aria-label="Atualizar catálogo de sessões"
          >
            {refreshing ? "Atualizando…" : "Atualizar"}
          </button>
        </div>
      </header>

      {loading ? (
        <LoadingState message="Carregando sessões autorizadas…" />
      ) : error && catalog === null ? (
        <InlineError message={error} />
      ) : catalog ? (
        <>
          {error && <InlineError message={error} />}
          {catalog.items.length === 0 ? (
            <EmptyState
              title="Nenhuma sessão disponível"
              description="Nenhuma sessão de paper trading autorizada para esta conta."
            />
          ) : (
            <section aria-labelledby="authorized-sessions-title">
              <div className="section-heading paper-sessions-heading">
                <div>
                  <p className="eyebrow">Página {catalog.page}</p>
                  <h2 id="authorized-sessions-title">Sessões autorizadas</h2>
                </div>
                <span>{catalog.total} no catálogo autorizado</span>
              </div>
              <div className="paper-session-grid">
                {catalog.items.map((session) => (
                  <article className="paper-session" key={session.session_id}>
                    <header className="paper-session__header">
                      <div>
                        <p className="eyebrow">{session.timeframe}</p>
                        <h3>
                          {session.base_asset}/{session.quote_asset}
                        </h3>
                        <small>
                          ID {abbreviatedSessionId(session.session_id)}
                        </small>
                      </div>
                    </header>
                    <dl className="paper-session__details">
                      <div>
                        <dt>Estratégia</dt>
                        <dd>{session.strategy_name}</dd>
                      </div>
                      <div>
                        <dt>Versão</dt>
                        <dd>{session.strategy_version}</dd>
                      </div>
                      <div>
                        <dt>Timeframe</dt>
                        <dd>{session.timeframe}</dd>
                      </div>
                      <div>
                        <dt>Instrumento</dt>
                        <dd>
                          {session.base_asset}/{session.quote_asset}
                        </dd>
                      </div>
                    </dl>
                    <Link
                      className="button button--ghost button--compact"
                      to={`/app/sessions/${session.session_id}`}
                    >
                      Abrir sessão
                    </Link>
                  </article>
                ))}
              </div>
            </section>
          )}
          {pagination && (
            <Pagination pagination={pagination} onChange={setPage} />
          )}
        </>
      ) : null}
    </section>
  );
}
