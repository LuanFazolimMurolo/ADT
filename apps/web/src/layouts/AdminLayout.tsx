import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { apiClient } from '../http/client'

const navigation = [
  { to: '/admin', label: 'Visão geral', icon: '⌁', end: true },
  { to: '/admin/simulations', label: 'Simulações', icon: '▤', end: false },
  { to: '/admin/settings', label: 'Configurações', icon: '⚙', end: false },
]

export function AdminLayout() {
  const { signOut } = useAuth()
  const [menuOpen, setMenuOpen] = useState(false)
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null)

  useEffect(() => {
    void apiClient.getHealth()
      .then(() => setBackendOnline(true))
      .catch(() => setBackendOnline(false))
  }, [])

  return (
    <div className="admin-shell">
      <aside className={menuOpen ? 'sidebar sidebar--open' : 'sidebar'}>
        <div className="sidebar__brand">
          <span className="brand-mark" aria-hidden="true">A</span>
          <div><strong>ADT</strong><small>CONTROL ROOM</small></div>
        </div>
        <nav aria-label="Navegação administrativa">
          {navigation.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={() => setMenuOpen(false)}
              className={({ isActive }) => isActive ? 'sidebar__link sidebar__link--active' : 'sidebar__link'}
            >
              <span aria-hidden="true">{item.icon}</span>{item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar__footer">
          <p><span className={backendOnline ? 'signal' : 'signal signal--offline'} />Backend {backendOnline === null ? 'verificando' : backendOnline ? 'conectado' : 'offline'}</p>
          <small>Paper trading only</small>
        </div>
      </aside>

      <div className="admin-content">
        <header className="topbar">
          <button
            className="menu-button"
            type="button"
            aria-label="Alternar navegação"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((value) => !value)}
          >
            <span /><span /><span />
          </button>
          <div className="topbar__identity">
            <span className="identity-avatar" aria-hidden="true">AD</span>
            <div><strong>Administrador ADT</strong><small>Acesso verificado pelo backend</small></div>
          </div>
          <button className="button button--ghost button--compact" type="button" onClick={() => void signOut()}>
            Sair
          </button>
        </header>
        <main className="admin-main">
          <Outlet />
        </main>
      </div>
      {menuOpen && <button className="sidebar-scrim" aria-label="Fechar navegação" onClick={() => setMenuOpen(false)} />}
    </div>
  )
}
