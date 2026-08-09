import { useAuth } from '../../auth/AuthContext'

export function AppHomePage() {
  const { identity } = useAuth()

  return (
    <section className="app-home">
      <p className="eyebrow">Boundary autenticado</p>
      <h1>Paper trading, com acesso verificado.</h1>
      <p className="app-home__lead">
        Esta área inicial é somente leitura. Gráficos, sinais e performance
        autorizada serão adicionados em entregas futuras, sem expor controles
        administrativos.
      </p>
      <div className="app-home__identity">
        <span className="metric-label">IDENTIDADE VALIDADA PELO BACKEND</span>
        <code>{identity?.user_id}</code>
      </div>
      <div className="app-home__principles" aria-label="Limites desta área">
        <article>
          <span aria-hidden="true">01</span>
          <h2>Read-only</h2>
          <p>Nenhuma configuração ou sessão pode ser alterada aqui.</p>
        </article>
        <article>
          <span aria-hidden="true">02</span>
          <h2>Capital fictício</h2>
          <p>O ADT continua operando exclusivamente com paper trading.</p>
        </article>
        <article>
          <span aria-hidden="true">03</span>
          <h2>Backend-authorized</h2>
          <p>A identidade e o privilégio administrativo vêm somente da API.</p>
        </article>
      </div>
    </section>
  )
}
