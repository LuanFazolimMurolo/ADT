import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const authState = vi.hoisted(() => ({
  session: null as object | null,
  identity: null as object | null,
  isAdmin: false,
  loading: false,
  signIn: vi.fn(),
  signOut: vi.fn(),
}));

vi.mock("./auth/AuthContext", () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => children,
  useAuth: () => authState,
}));

vi.mock("./lib/supabase", () => ({
  getSupabaseClient: () => ({
    auth: {
      getSession: vi
        .fn()
        .mockResolvedValue({ data: { session: null }, error: null }),
      onAuthStateChange: vi.fn(() => ({
        data: { subscription: { unsubscribe: vi.fn() } },
      })),
      signOut: vi.fn().mockResolvedValue({ error: null }),
      refreshSession: vi.fn(),
    },
  }),
}));

vi.mock("./http/client", async () => {
  class ApiError extends Error {
    constructor(
      public status: number,
      public code: string,
      message: string,
    ) {
      super(message);
    }
  }
  return {
    ApiError,
    apiClient: {
      getSystemStatus: vi.fn().mockResolvedValue({
        status: "operational",
        version: "0.1.0",
        environment: "development",
        timestamp: new Date().toISOString(),
      }),
      getPublicSimulation: vi.fn().mockResolvedValue(null),
      getHealth: vi.fn().mockResolvedValue({ status: "healthy" }),
      listOperationalPaperSessionProfiles: vi.fn().mockResolvedValue({
        items: [],
        limit: 20,
        offset: 0,
        total: 0,
      }),
      listOperationalPaperCapitalAuthorizations: vi.fn().mockResolvedValue({
        items: [],
        limit: 20,
        offset: 0,
        total: 0,
      }),
      listOperationalMandates: vi.fn().mockResolvedValue({
        items: [],
        limit: 100,
        offset: 0,
        total: 0,
      }),
      listStrategyDefinitions: vi.fn().mockResolvedValue({
        items: [],
        pagination: { page: 1, page_size: 100, total: 0, total_pages: 0 },
      }),
    },
  };
});

beforeEach(() => {
  authState.session = null;
  authState.identity = null;
  authState.isAdmin = false;
  authState.loading = false;
  window.history.pushState({}, "", "/");
});

describe("site público", () => {
  it("mantém a landing pública e oferece somente o login geral", async () => {
    render(<App />);
    await screen.findByText("API operacional");
    await screen.findByText("Nenhuma simulação pública ativa no momento.");

    expect(screen.getByText(/Pesquisa disciplinada/)).toBeDefined();
    expect(
      screen.getByRole("heading", { name: "Paper trading público" }),
    ).toBeDefined();
    expect(
      screen.getByRole("link", { name: "Entrar" }).getAttribute("href"),
    ).toBe("/login");
    expect(
      screen.queryByRole("link", { name: /criar conta|registrar|sign up/i }),
    ).toBeNull();
    expect(
      screen.queryByText(/cadastro/i)?.textContent?.toLowerCase(),
    ).toContain("sem cadastro público");
  });

  it("mantém o perfil paper somente na rota administrativa protegida e na navegação admin", async () => {
    authState.session = { user: { id: "admin-id" } };
    authState.identity = { user_id: "admin-id", is_admin: true };
    authState.isAdmin = true;
    window.history.pushState(
      {},
      "",
      "/admin/operational-paper-session-profiles",
    );

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Perfis de sessão paper" }),
    ).toBeDefined();
    expect(
      screen
        .getByRole("link", { name: /Perfis de sessão paper/ })
        .getAttribute("href"),
    ).toBe("/admin/operational-paper-session-profiles");
  });

  it("não expõe equivalente em /app e preserva o redirecionamento sem autenticação", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/operational-paper-session-profiles",
    );
    render(<App />);
    await waitFor(() => expect(window.location.pathname).toBe("/admin/login"));
    expect(
      screen.queryByRole("heading", { name: "Perfis de sessão paper" }),
    ).toBeNull();
  });

  it("não registra rota equivalente no espaço /app", async () => {
    authState.session = { user: { id: "admin-id" } };
    authState.identity = { user_id: "admin-id", is_admin: true };
    authState.isAdmin = true;
    window.history.pushState({}, "", "/app/operational-paper-session-profiles");

    render(<App />);
    await waitFor(() => expect(window.location.pathname).toBe("/"));
    expect(
      screen.queryByRole("heading", { name: "Perfis de sessão paper" }),
    ).toBeNull();
  });

  it("resolve a autorização de capital paper somente na área administrativa", async () => {
    authState.session = { user: { id: "admin-id" } };
    authState.identity = { user_id: "admin-id", is_admin: true };
    authState.isAdmin = true;
    window.history.pushState(
      {},
      "",
      "/admin/operational-paper-capital-authorizations",
    );

    render(<App />);

    expect(
      await screen.findByRole("heading", {
        name: "Autorizações de capital operacional",
      }),
    ).toBeDefined();
    const navigationLinks = screen.getAllByRole("link", {
      name: "Autorizações de capital paper",
    });
    expect(navigationLinks).toHaveLength(1);
    expect(navigationLinks[0].getAttribute("href")).toBe(
      "/admin/operational-paper-capital-authorizations",
    );
  });

  it("protege a rota de autorização de capital paper sem autenticação", async () => {
    window.history.pushState(
      {},
      "",
      "/admin/operational-paper-capital-authorizations",
    );

    render(<App />);

    await waitFor(() => expect(window.location.pathname).toBe("/admin/login"));
    expect(
      screen.queryByRole("heading", {
        name: "Autorizações de capital operacional",
      }),
    ).toBeNull();
  });

  it("não cria URL alternativa para autorização de capital paper em /app", async () => {
    authState.session = { user: { id: "admin-id" } };
    authState.identity = { user_id: "admin-id", is_admin: true };
    authState.isAdmin = true;
    window.history.pushState(
      {},
      "",
      "/app/operational-paper-capital-authorizations",
    );

    render(<App />);

    await waitFor(() => expect(window.location.pathname).toBe("/"));
    expect(
      screen.queryByRole("heading", {
        name: "Autorizações de capital operacional",
      }),
    ).toBeNull();
  });
});
