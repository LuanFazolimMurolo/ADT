import { useCallback, useEffect, useState } from "react";
import { apiClient } from "../../http/client";
import type {
  MLRecommendation,
  MLRecommendationEvidenceKind,
  MLStrategyParameterValue,
} from "../../types/mlRecommendations";

const PAGE_LIMIT = 50;

const evidenceLabels: Record<MLRecommendationEvidenceKind, string> = {
  PERFORMANCE_PREDICTION: "Previsão de performance",
  DEGRADATION: "Degradação",
  MARKET_REGIME: "Regime de mercado",
  ANOMALY: "Anomalia",
};

function formatUtc(value: string): string {
  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("pt-BR", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(date);
}

function formatParameterValue(value: MLStrategyParameterValue): string {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function rationaleLabel(code: string): string {
  return code.split("_").join(" ");
}

function RecommendationCard({
  recommendation,
}: {
  recommendation: MLRecommendation;
}) {
  const candidate = recommendation.candidate;

  return (
    <article
      className="panel ml-recommendation"
      data-testid={`recommendation-${recommendation.identity}`}
    >
      <header className="ml-recommendation__header">
        <div>
          <p className="eyebrow">Candidato</p>
          <h2>
            {candidate.plugin_name} · {candidate.plugin_version}
          </h2>
          <p>
            Recomendação emitida em{" "}
            <strong>{formatUtc(recommendation.recommendation_time)}</strong>
          </p>
        </div>

        <span className="paper-status">Somente leitura</span>
      </header>

      <section aria-labelledby={`rationale-${recommendation.identity}`}>
        <h3 id={`rationale-${recommendation.identity}`}>Rationale</h3>

        <ul>
          {recommendation.rationale_codes.map((code) => (
            <li key={code}>
              <code>{code}</code>
              <span> — {rationaleLabel(code)}</span>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby={`evidence-${recommendation.identity}`}>
        <h3 id={`evidence-${recommendation.identity}`}>
          Evidências ({recommendation.evidence.length})
        </h3>

        <div className="ml-recommendation__evidence">
          {recommendation.evidence.map((evidence) => (
            <article
              key={`${evidence.kind}:${evidence.evidence_identity}`}
              className="panel panel--nested"
            >
              <strong>{evidenceLabels[evidence.kind]}</strong>

              <dl>
                <div>
                  <dt>Tipo</dt>
                  <dd>{evidence.kind}</dd>
                </div>

                <div>
                  <dt>Information time</dt>
                  <dd>{formatUtc(evidence.information_time)}</dd>
                </div>

                <div>
                  <dt>Evidence identity</dt>
                  <dd>
                    <code>{evidence.evidence_identity}</code>
                  </dd>
                </div>

                <div>
                  <dt>Evidence checksum</dt>
                  <dd>
                    <code>{evidence.evidence_checksum}</code>
                  </dd>
                </div>

                <div>
                  <dt>Strategy subject</dt>
                  <dd>
                    {evidence.strategy_subject_identity ? (
                      <code>{evidence.strategy_subject_identity}</code>
                    ) : (
                      "Contextual"
                    )}
                  </dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      </section>

      <section aria-labelledby={`parameters-${recommendation.identity}`}>
        <h3 id={`parameters-${recommendation.identity}`}>
          Parâmetros do candidato
        </h3>

        {candidate.parameters.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>Parâmetro</th>
                <th>Tipo</th>
                <th>Valor</th>
              </tr>
            </thead>
            <tbody>
              {candidate.parameters.map((parameter) => (
                <tr key={parameter.name}>
                  <td>
                    <code>{parameter.name}</code>
                  </td>
                  <td>{parameter.kind}</td>
                  <td>
                    <code>{formatParameterValue(parameter.value)}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p>Nenhum parâmetro explícito.</p>
        )}
      </section>

      <section aria-labelledby={`lineage-${recommendation.identity}`}>
        <h3 id={`lineage-${recommendation.identity}`}>Identidade e lineage</h3>

        <dl>
          <div>
            <dt>Recommendation identity</dt>
            <dd>
              <code>{recommendation.identity}</code>
            </dd>
          </div>

          <div>
            <dt>Recommendation checksum</dt>
            <dd>
              <code>{recommendation.checksum}</code>
            </dd>
          </div>

          <div>
            <dt>Candidate identity</dt>
            <dd>
              <code>{candidate.identity}</code>
            </dd>
          </div>

          <div>
            <dt>Candidate checksum</dt>
            <dd>
              <code>{candidate.checksum}</code>
            </dd>
          </div>

          <div>
            <dt>Parameters checksum</dt>
            <dd>
              <code>{candidate.parameters_checksum}</code>
            </dd>
          </div>

          <div>
            <dt>Configuration checksum</dt>
            <dd>
              <code>{candidate.configuration_checksum}</code>
            </dd>
          </div>

          <div>
            <dt>Plugin schema</dt>
            <dd>{candidate.plugin_schema_version}</dd>
          </div>

          <div>
            <dt>Lifecycle</dt>
            <dd>{candidate.lifecycle_version}</dd>
          </div>
        </dl>
      </section>

      <section aria-labelledby={`implementation-${recommendation.identity}`}>
        <h3 id={`implementation-${recommendation.identity}`}>
          Implementação da recomendação
        </h3>

        <dl>
          <div>
            <dt>Nome</dt>
            <dd>{recommendation.implementation.name}</dd>
          </div>

          <div>
            <dt>Versão</dt>
            <dd>{recommendation.implementation.version}</dd>
          </div>

          <div>
            <dt>Content checksum</dt>
            <dd>
              <code>{recommendation.implementation.content_checksum}</code>
            </dd>
          </div>
        </dl>

        <h4>Implementação do candidato</h4>

        <dl>
          <div>
            <dt>Nome</dt>
            <dd>{candidate.implementation.name}</dd>
          </div>

          <div>
            <dt>Versão</dt>
            <dd>{candidate.implementation.version}</dd>
          </div>

          <div>
            <dt>Content checksum</dt>
            <dd>
              <code>{candidate.implementation.content_checksum}</code>
            </dd>
          </div>
        </dl>
      </section>
    </article>
  );
}

export function MLRecommendationsPage() {
  const [items, setItems] = useState<MLRecommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (refresh: boolean) => {
    if (refresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }

    try {
      const result = await apiClient.listMLRecommendations(PAGE_LIMIT);
      setItems(result.items);
      setError(null);
    } catch {
      setError("Não foi possível carregar as recomendações ML.");
    } finally {
      if (refresh) {
        setRefreshing(false);
      } else {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  return (
    <div className="admin-page ml-recommendations-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">ML · EXPLAINABILITY</p>
          <h1>Recomendações ML</h1>
          <p>
            Evidências explicáveis e imutáveis para análise administrativa. Esta
            superfície não aprova, promove ou executa estratégias.
          </p>
        </div>

        <button
          className="button button--ghost"
          type="button"
          disabled={loading || refreshing}
          onClick={() => void load(true)}
        >
          {refreshing ? "Atualizando..." : "Atualizar"}
        </button>
      </header>

      {loading ? (
        <section className="state" aria-live="polite">
          <strong>Carregando recomendações ML...</strong>
        </section>
      ) : (
        <>
          {error && (
            <div className="state state--error" role="alert">
              {error}
            </div>
          )}

          {!error && items.length === 0 && (
            <section className="state">
              <strong>Nenhuma recomendação ML disponível</strong>
              <p>
                O backend não possui recommendation evidence disponível para
                leitura neste momento.
              </p>
            </section>
          )}

          {!error && items.length > 0 && (
            <>
              <section className="panel" aria-label="Resumo das recomendações">
                <span className="metric-label">RECOMENDAÇÕES DISPONÍVEIS</span>
                <strong>{items.length}</strong>
                <small>Fonte read-only · limite {PAGE_LIMIT}</small>
              </section>

              <section
                className="ml-recommendations-list"
                aria-label="Recomendações ML"
              >
                {items.map((recommendation) => (
                  <RecommendationCard
                    key={recommendation.identity}
                    recommendation={recommendation}
                  />
                ))}
              </section>
            </>
          )}
        </>
      )}
    </div>
  );
}
