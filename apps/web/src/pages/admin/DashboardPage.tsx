import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiClient } from '../../http/client'
import type { SimulationDetail } from '../../types/api'
import { EmptyState, InlineError, LoadingState } from '../../components/States'
import { StatusBadge } from '../../components/StatusBadge'
import { formatDate, formatMoney, getErrorMessage } from '../../utils/format'

interface ServiceState {
  backend: 'checking' | 'online' | 'offline'
  database: 'checking' | 'online' | 'offline'
}

export function DashboardPage() {
  const [services, setServices] = useState<ServiceState>({ backend: 'checking', database: 'checking' })
  const [simulation, setSimulation] = useState<SimulationDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    const load = async () => {
      const [health, database] = await Promise.allSettled([
        apiClient.getHealth(),
        apiClient.getDatabaseHealth(),
      ])
      if (!active) return
      setServices({
        backend: health.status === 'fulfilled' ? 'online' : 'offline',
        database: database.status === 'fulfilled' ? 'online' : 'offline',
      })
      try {
        const list = await apiClient.listSimulations(1, 100)
        const activeSimulation = list.items.find((item) => item.status === 'ACTIVE')
        setSimulation(activeSimulation ? await apiClient.getSimulation(activeSimulation.id) : null)
      } catch (nextError) {
        setError(getErrorMessage(nextError, 'Não foi possível carregar o dashboard.'))
      } finally {
        setLoading(false)
      }
    }
    void load()
    return () => { active = false }
  }, [])

  return (
    <div>
      <div className="page-heading">
        <div><p className="eyebrow">Centro operacional</p><h1>Visão geral</h1><p>Estado atual da infraestrutura e do capital simulado.</p></div>
        <Link className="button button--ghost" to="/admin/simulations">Ver histórico</Link>
      </div>

      <section className="health-strip" aria-label="Estado dos serviços">
        <div><span className={`signal ${services.backend === 'offline' ? 'signal--offline' : services.backend === 'checking' ? 'signal--checking' : ''}`} /><div><small>BACKEND</small><strong>{services.backend === 'online' ? 'Operacional' : services.backend === 'offline' ? 'Indisponível' : 'Verificando'}</strong></div></div>
        <div><span className={`signal ${services.database === 'offline' ? 'signal--offline' : services.database === 'checking' ? 'signal--checking' : ''}`} /><div><small>BANCO DE DADOS</small><strong>{services.database === 'online' ? 'Conectado' : services.database === 'offline' ? 'Indisponível' : 'Verificando'}</strong></div></div>
        <div><span className="signal signal--paper" /><div><small>MODO</small><strong>Paper trading</strong></div></div>
      </section>

      {loading ? <LoadingState message="Carregando simulação ativa…" /> : error ? <InlineError message={error} /> : !simulation ? (
        <EmptyState
          title="Nenhuma simulação ativa"
          description="Crie a primeira simulação para começar a acompanhar o capital fictício."
          action={<Link className="button" to="/admin/simulations?create=true">Criar primeira simulação</Link>}
        />
      ) : (
        <>
          <section className="section-heading">
            <div><p className="eyebrow">Simulação ativa</p><h2>{simulation.name}</h2></div>
            <StatusBadge status={simulation.status} />
          </section>
          <section className="metrics-grid">
            <article className="metric-card">
              <span className="metric-label">CAPITAL INICIAL</span>
              <strong>{formatMoney(simulation.initial_capital, simulation.currency)}</strong>
              <small>Base imutável</small>
            </article>
            <article className="metric-card metric-card--primary">
              <span className="metric-label">SALDO ATUAL</span>
              <strong>{formatMoney(simulation.current_balance, simulation.currency)}</strong>
              <small>Calculado pelo backend</small>
            </article>
            <article className={`metric-card ${Number(simulation.total_profit_loss) < 0 ? 'metric-card--negative' : ''}`}>
              <span className="metric-label">LUCRO / PREJUÍZO</span>
              <strong>{formatMoney(simulation.total_profit_loss, simulation.currency)}</strong>
              <small>Movimentos acumulados</small>
            </article>
            <article className="metric-card">
              <span className="metric-label">INÍCIO</span>
              <strong className="metric-card__date">{formatDate(simulation.started_at)}</strong>
              <small>ID {simulation.id.slice(0, 8)}</small>
            </article>
          </section>
          <section className="quick-actions">
            <div><h2>Atalhos</h2><p>Acesse as operações administrativas permitidas.</p></div>
            <Link className="quick-action" to={`/admin/simulations/${simulation.id}`}><span>▤</span><div><strong>Gerenciar simulação</strong><small>Movimentos e encerramento</small></div><b>→</b></Link>
            <Link className="quick-action" to="/admin/settings"><span>⚙</span><div><strong>Configurações</strong><small>Parâmetros não secretos</small></div><b>→</b></Link>
          </section>
        </>
      )}
    </div>
  )
}
