import { useEffect, useState } from 'react'
import { apiClient } from '../http/client'
import type { SystemStatus } from '../types/system'

export function PublicHome() {
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [offline, setOffline] = useState(false)

  useEffect(() => {
    void apiClient.getSystemStatus()
      .then((data) => {
        setStatus(data)
        setOffline(false)
      })
      .catch(() => setOffline(true))
  }, [])

  return (
    <div className="public-page">
      <header className="public-hero">
        <div className="brand-mark" aria-hidden="true">A</div>
        <p className="eyebrow">Automatic Dry Trade</p>
        <h1>Decisões frias.<br />Execução disciplinada.</h1>
        <p className="public-hero__copy">
          Infraestrutura de pesquisa e paper trading construída para medir estratégias
          sem emoção e sem operar capital real.
        </p>
        <div className="public-status" role="status">
          <span className={offline ? 'signal signal--offline' : 'signal'} />
          {offline ? 'API temporariamente indisponível' : `Sistema operacional · ${status?.environment ?? 'conectando'}`}
        </div>
      </header>

      <main className="public-grid">
        <article>
          <span>01</span>
          <h2>Backtesting</h2>
          <p>Validação histórica reproduzível, sem viés emocional.</p>
        </article>
        <article>
          <span>02</span>
          <h2>Paper trading</h2>
          <p>Capital estritamente simulado. Nenhum risco financeiro real.</p>
        </article>
        <article>
          <span>03</span>
          <h2>Arquitetura modular</h2>
          <p>Estratégias, mercados e execução mantidos em limites claros.</p>
        </article>
      </main>
      <footer className="public-footer">ADT · Fase 1 · Administração privada sem cadastro público</footer>
    </div>
  )
}
