import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { apiClient } from "../http/client";

const navigation = [
  { to: "/admin", label: "Visão geral", icon: "⌁", end: true },
  { to: "/admin/paper-trading", label: "Paper trading", icon: "◫", end: true },
  {
    to: "/admin/paper-trading/chart",
    label: "Gráfico de mercado",
    icon: "⌁",
    end: false,
  },
  {
    to: "/admin/paper-trading/performance",
    label: "Performance histórica",
    icon: "⌁",
    end: false,
  },
  {
    to: "/admin/paper-trading/journal",
    label: "Trade journal",
    icon: "≋",
    end: false,
  },
  {
    to: "/admin/paper-trading/period-metrics",
    label: "Performance por período",
    icon: "▦",
    end: false,
  },
  { to: "/admin/simulations", label: "Simulações", icon: "▤", end: false },
  {
    to: "/admin/market-operations",
    label: "Operações de mercado",
    icon: "⇄",
    end: false,
  },
  { to: "/admin/settings", label: "Configurações", icon: "⚙", end: false },
];

export function AdminLayout() {
  const { signOut } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);

  const closeMenu = () => {
    setMenuOpen(false);
    menuButtonRef.current?.focus();
  };

  useEffect(() => {
    void apiClient
      .getHealth()
      .then(() => setBackendOnline(true))
      .catch(() => setBackendOnline(false));
  }, []);

  return (
    <div className="admin-shell">
      <button
        ref={menuButtonRef}
        className="menu-button"
        type="button"
        aria-label="Alternar navegação"
        aria-controls="admin-navigation"
        aria-expanded={menuOpen}
        onClick={() => (menuOpen ? closeMenu() : setMenuOpen(true))}
      >
        <span />
        <span />
        <span />
      </button>

      <aside
        id="admin-navigation"
        className={menuOpen ? "sidebar sidebar--open" : "sidebar"}
      >
        <div className="sidebar__brand">
          <span className="brand-mark" aria-hidden="true">
            A
          </span>
          <div>
            <strong>ADT</strong>
            <small>CONTROL ROOM</small>
          </div>
        </div>
        <nav aria-label="Navegação administrativa">
          {navigation.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={closeMenu}
              className={({ isActive }) =>
                isActive
                  ? "sidebar__link sidebar__link--active"
                  : "sidebar__link"
              }
            >
              <span aria-hidden="true">{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar__footer">
          <p>
            <span
              className={backendOnline ? "signal" : "signal signal--offline"}
            />
            Backend{" "}
            {backendOnline === null
              ? "verificando"
              : backendOnline
                ? "conectado"
                : "offline"}
          </p>
          <small>Paper trading only</small>
        </div>
      </aside>

      <div className="admin-content">
        <header className="topbar">
          <div className="topbar__identity">
            <span className="identity-avatar" aria-hidden="true">
              AD
            </span>
            <div>
              <strong>Administrador ADT</strong>
              <small>Acesso verificado pelo backend</small>
            </div>
          </div>
          <button
            className="button button--ghost button--compact"
            type="button"
            onClick={() => void signOut()}
          >
            Sair
          </button>
        </header>
        <main className="admin-main">
          <Outlet />
        </main>
      </div>
      {menuOpen && (
        <button
          className="sidebar-scrim"
          aria-label="Fechar navegação"
          onClick={closeMenu}
        />
      )}
    </div>
  );
}
