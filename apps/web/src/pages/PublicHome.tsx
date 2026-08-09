import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiClient } from '../http/client'
import type { PublicSimulationSummary } from '../types/api'
import type { SystemStatus } from '../types/system'
import { formatDate, formatMoney } from '../utils/format'

type SystemState =
  | { state: 'loading' }
  | { state: 'operational'; status: SystemStatus }
  | { state: 'offline' }

type PublicSimulationState =
  | { state: 'loading' }
  | { state: 'available'; simulation: PublicSimulationSummary }
  | { state: 'empty' }
  | { state: 'unavailable' }

const principles = [
  {
    title: 'Dados verificáveis',
    description:
      'Candles persistidos, fechados e validados antes de atravessarem os limites de pesquisa.',
  },
  {
    title: 'Backtesting reproduzível',
    description:
      'Entradas imutáveis e contratos determinísticos permitem repetir e auditar resultados.',
  },
  {
    title: 'Execução simulada',
    description:
      'Regras mecânicas substituem impulso e emoção em um ambiente de capital fictício.',
  },
  {
    title: 'Risco determinístico',
    description:
      'Dimensionamento, limites e stops seguem políticas explícitas no motor de simulação.',
  },
  {
    title: 'Histórico auditável',
    description:
      'Identidades de conteúdo e checksums verificam artefatos sem alterar evidências anteriores.',
  },
  {
    title: 'Nenhum capital real',
    description:
      'Nesta fase, o ADT não acessa contas de exchange nem executa ordens com dinheiro real.',
  },
] as const

export function PublicHome() {
  const [system, setSystem] = useState<SystemState>({ state: 'loading' })
  const [publicSimulation, setPublicSimulation] =
    useState<PublicSimulationState>({ state: 'loading' })

  useEffect(() => {
    let active = true

    void apiClient
      .getSystemStatus()
      .then((status) => {
        if (active) setSystem({ state: 'operational', status })
      })
      .catch(() => {
        if (active) setSystem({ state: 'offline' })
      })

    void apiClient
      .getPublicSimulation()
      .then((simulation) => {
        if (!active) return
        setPublicSimulation(
          simulation ? { state: 'available', simulation } : { state: 'empty' },
        )
      })
      .catch(() => {
        if (active) setPublicSimulation({ state: 'unavailable' })
      })

    return () => {
      active = false
    }
  }, [])

  return (
    <div className="public-page">
      <header className="public-header">
        <a className="public-brand" href="#inicio" aria-label="ADT — início">
          <span className="brand-mark" aria-hidden="true">
            A
          </span>
          <span>
            <strong>ADT</strong>
            <small>AUTOMATIC DRY TRADE</small>
          </span>
        </a>
        <nav aria-label="Navegação pública">
          <a href="#principios">Princípios</a>
          <a href="#paper-trading">Paper trading</a>
          <Link className="button button--compact" to="/login">
            Entrar
          </Link>
        </nav>
      </header>

      <main>
        <section
          className="public-hero"
          id="inicio"
          aria-labelledby="public-title"
        >
          <div>
            <p className="eyebrow">Automatic Dry Trade</p>
            <h1 id="public-title">
              Pesquisa disciplinada.
              <br />
              Evidência verificável.
            </h1>
            <p className="public-hero__copy">
              Infraestrutura determinística para pesquisa, backtesting e paper
              trading — sem execução automática de capital real nesta fase.
            </p>
            <div className="public-hero__actions">
              <a className="button" href="#paper-trading">
                Ver simulação pública
              </a>
              <span>
                Resultados passados não representam promessa de retorno.
              </span>
            </div>
          </div>
          <aside
            className="public-manifesto"
            aria-label="Princípio operacional"
          >
            <span className="metric-label">PRINCÍPIO OPERACIONAL</span>
            <p>Sem impulso. Sem pânico. Apenas regras verificáveis.</p>
            <dl>
              <div>
                <dt>Modo atual</dt>
                <dd>Paper trading</dd>
              </div>
              <div>
                <dt>Capital real</dt>
                <dd>Não operado</dd>
              </div>
            </dl>
          </aside>
        </section>

        <section
          className="public-section"
          id="principios"
          aria-labelledby="principles-title"
        >
          <div className="public-section__heading">
            <div>
              <p className="eyebrow">Como o ADT trabalha</p>
              <h2 id="principles-title">Determinismo antes de narrativa.</h2>
            </div>
            <p>
              O projeto separa dados observados, cálculos reproduzíveis e
              apresentação. Nenhuma interface substitui a autoridade do backend.
            </p>
          </div>
          <div className="public-principles">
            {principles.map((principle, index) => (
              <article key={principle.title}>
                <span aria-hidden="true">
                  {String(index + 1).padStart(2, '0')}
                </span>
                <h3>{principle.title}</h3>
                <p>{principle.description}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="public-section" aria-labelledby="system-title">
          <div className="public-section__heading public-section__heading--compact">
            <div>
              <p className="eyebrow">Disponibilidade</p>
              <h2 id="system-title">Estado do sistema</h2>
            </div>
          </div>
          <div className="public-system-card" role="status" aria-live="polite">
            {system.state === 'loading' && (
              <>
                <span className="signal signal--checking" aria-hidden="true" />
                <div>
                  <strong>Verificando disponibilidade</strong>
                  <span>Consultando o estado público da API…</span>
                </div>
              </>
            )}
            {system.state === 'operational' && (
              <>
                <span className="signal" aria-hidden="true" />
                <div>
                  <strong>API operacional</strong>
                  <span>
                    Ambiente {system.status.environment} · versão{' '}
                    {system.status.version}
                  </span>
                </div>
              </>
            )}
            {system.state === 'offline' && (
              <>
                <span className="signal signal--offline" aria-hidden="true" />
                <div>
                  <strong>API temporariamente indisponível</strong>
                  <span>A página institucional permanece acessível.</span>
                </div>
              </>
            )}
          </div>
        </section>

        <section
          className="public-section"
          id="paper-trading"
          aria-labelledby="simulation-title"
        >
          <div className="public-section__heading">
            <div>
              <p className="eyebrow">Projeção pública intencional</p>
              <h2 id="simulation-title">Paper trading público</h2>
            </div>
            <p>
              Esta seção usa somente a projeção pública restrita do backend. Não
              expõe identificadores, movimentos ou configuração administrativa.
            </p>
          </div>

          {publicSimulation.state === 'loading' && (
            <div
              className="public-simulation-state"
              role="status"
              aria-live="polite"
            >
              <span className="spinner" aria-hidden="true" />
              <p>Carregando simulação pública…</p>
            </div>
          )}

          {publicSimulation.state === 'empty' && (
            <div className="public-simulation-state">
              <span aria-hidden="true">◇</span>
              <h3>Nenhuma simulação pública ativa no momento.</h3>
              <p>O restante da landing continua disponível normalmente.</p>
            </div>
          )}

          {publicSimulation.state === 'unavailable' && (
            <div className="public-simulation-state" role="status">
              <span aria-hidden="true">◇</span>
              <h3>Simulação pública temporariamente indisponível.</h3>
              <p>
                Tente novamente mais tarde. Nenhum detalhe interno foi exibido.
              </p>
            </div>
          )}

          {publicSimulation.state === 'available' && (
            <article className="public-simulation-card">
              <header>
                <div>
                  <span className="badge badge--active">Paper trading</span>
                  <h3>{publicSimulation.simulation.name}</h3>
                </div>
                <div className="public-simulation-card__notice">
                  <strong>Capital simulado</strong>
                  <span>Não representa capital real</span>
                </div>
              </header>
              <dl className="public-simulation-metrics">
                <div>
                  <dt>Moeda</dt>
                  <dd>{publicSimulation.simulation.currency}</dd>
                </div>
                <div>
                  <dt>Capital inicial</dt>
                  <dd>
                    {formatMoney(
                      publicSimulation.simulation.initial_capital,
                      publicSimulation.simulation.currency,
                    )}
                  </dd>
                </div>
                <div>
                  <dt>Saldo atual</dt>
                  <dd>
                    {formatMoney(
                      publicSimulation.simulation.current_balance,
                      publicSimulation.simulation.currency,
                    )}
                  </dd>
                </div>
                <div>
                  <dt>PnL total informado pelo backend</dt>
                  <dd>
                    {formatMoney(
                      publicSimulation.simulation.total_profit_loss,
                      publicSimulation.simulation.currency,
                    )}
                  </dd>
                </div>
                <div>
                  <dt>Início</dt>
                  <dd>{formatDate(publicSimulation.simulation.started_at)}</dd>
                </div>
                <div>
                  <dt>Estado</dt>
                  <dd>{publicSimulation.simulation.status}</dd>
                </div>
              </dl>
            </article>
          )}
        </section>

        <section className="public-boundary" aria-labelledby="boundary-title">
          <div>
            <p className="eyebrow">Limites de acesso</p>
            <h2 id="boundary-title">Cada superfície tem um propósito.</h2>
          </div>
          <div className="public-boundary__grid">
            <article>
              <span>PÚBLICO</span>
              <h3>Visão institucional</h3>
              <p>Status e simulação pública intencionalmente restrita.</p>
            </article>
            <article>
              <span>/APP</span>
              <h3>Área autenticada</h3>
              <p>Boundary read-only para usuários com identidade validada.</p>
            </article>
            <article>
              <span>/ADMIN</span>
              <h3>Administração restrita</h3>
              <p>Controles protegidos por autorização calculada no backend.</p>
            </article>
          </div>
        </section>
      </main>

      <footer className="public-footer">
        <span>ADT · Automatic Dry Trade</span>
        <span>Paper trading somente · Sem cadastro público</span>
      </footer>
    </div>
  )
}
