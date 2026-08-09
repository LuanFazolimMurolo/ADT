import { Link, Outlet } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

export function AppLayout() {
  const { identity, signOut } = useAuth()

  return (
    <div className="app-shell">
      <header className="app-header">
        <Link
          className="app-brand"
          to="/app"
          aria-label="Início da área autenticada"
        >
          <span className="brand-mark" aria-hidden="true">
            A
          </span>
          <span>
            <strong>ADT</strong>
            <small>ÁREA AUTENTICADA</small>
          </span>
        </Link>
        <nav aria-label="Ações da conta">
          <Link
            className="button button--ghost button--compact"
            to="/app/market"
          >
            Mercado
          </Link>
          <Link
            className="button button--ghost button--compact"
            to="/app/sessions"
          >
            Sessões
          </Link>
          {identity?.is_admin && (
            <Link className="button button--ghost button--compact" to="/admin">
              Administração
            </Link>
          )}
          <button
            className="button button--ghost button--compact"
            type="button"
            onClick={() => void signOut()}
          >
            Sair
          </button>
        </nav>
      </header>
      <div className="app-read-only-notice" role="note">
        <span className="signal signal--paper" aria-hidden="true" />
        Paper trading somente · Área read-only · Nenhuma ordem real
      </div>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  )
}
