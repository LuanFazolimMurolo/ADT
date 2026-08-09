import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AppPaperSessionCatalogResponse } from "../../types/api";
import { AppPaperSessionsPage } from "./AppPaperSessionsPage";

const mocks = vi.hoisted(() => ({
  getAppPaperSessions: vi.fn(),
}));

vi.mock("../../http/client", () => ({
  apiClient: mocks,
}));

const SESSION_ID = "a".repeat(64);

function catalog(
  overrides: Partial<AppPaperSessionCatalogResponse> = {},
): AppPaperSessionCatalogResponse {
  return {
    items: [
      {
        session_id: SESSION_ID,
        base_asset: "BTC",
        quote_asset: "USDT",
        timeframe: "15m",
        strategy_name: "paper-buy-test",
        strategy_version: "1",
      },
    ],
    page: 1,
    page_size: 20,
    total: 1,
    total_pages: 1,
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <AppPaperSessionsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mocks.getAppPaperSessions.mockReset();
  mocks.getAppPaperSessions.mockResolvedValue(catalog());
});

describe("AppPaperSessionsPage", () => {
  it("carrega somente o catálogo /app com paginação bounded", async () => {
    renderPage();

    expect(await screen.findByText("Sessões autorizadas")).toBeDefined();
    expect(mocks.getAppPaperSessions).toHaveBeenCalledWith(1, 20);
    expect(mocks.getAppPaperSessions).toHaveBeenCalledTimes(1);
  });

  it("mostra loading enquanto o backend decide o catálogo autorizado", async () => {
    let resolveRequest!: (value: AppPaperSessionCatalogResponse) => void;
    mocks.getAppPaperSessions.mockReturnValue(
      new Promise((resolve) => {
        resolveRequest = resolve;
      }),
    );
    renderPage();

    expect(screen.getByText("Carregando sessões autorizadas…")).toBeDefined();
    resolveRequest(catalog());
    expect(await screen.findByText("Sessões autorizadas")).toBeDefined();
  });

  it("mostra empty state neutro para catálogo não autorizado", async () => {
    mocks.getAppPaperSessions.mockResolvedValue(
      catalog({ items: [], total: 0, total_pages: 0 }),
    );
    renderPage();

    expect(
      await screen.findByText(
        "Nenhuma sessão de paper trading autorizada para esta conta.",
      ),
    ).toBeDefined();
    expect(document.body.textContent).not.toContain(SESSION_ID);
  });

  it("renderiza somente a projeção mínima e resume visualmente o session id", async () => {
    renderPage();

    expect((await screen.findAllByText("BTC/USDT")).length).toBe(2);
    expect(screen.getAllByText("15m").length).toBeGreaterThan(0);
    expect(screen.getByText("paper-buy-test")).toBeDefined();
    expect(screen.getAllByText("1").length).toBeGreaterThan(0);
    expect(screen.getByText("ID aaaaaaaaaaaa…")).toBeDefined();
    expect(document.body.textContent).not.toContain(SESSION_ID);
    expect(document.body.textContent).not.toMatch(
      /strategy_parameters|quantity|pnl|equity|runner|orders|fills/i,
    );
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(
      screen.getByRole("link", { name: "Abrir sessão" }).getAttribute("href"),
    ).toBe(`/app/sessions/${SESSION_ID}`);
  });

  it("pagina no backend sem ordenar ou carregar tudo no browser", async () => {
    const user = userEvent.setup();
    mocks.getAppPaperSessions
      .mockResolvedValueOnce(catalog({ total: 21, total_pages: 2 }))
      .mockResolvedValueOnce(
        catalog({ items: [], page: 2, total: 21, total_pages: 2 }),
      );
    renderPage();
    await screen.findByText("Sessões autorizadas");

    await user.click(screen.getByRole("button", { name: "Próxima" }));

    await waitFor(() =>
      expect(mocks.getAppPaperSessions).toHaveBeenLastCalledWith(2, 20),
    );
    expect(
      await screen.findByText(
        "Nenhuma sessão de paper trading autorizada para esta conta.",
      ),
    ).toBeDefined();
  });

  it("mostra erro seguro sem vazar detalhes internos", async () => {
    mocks.getAppPaperSessions.mockRejectedValue(
      new Error("/home/private/paper token-ultrassecreto"),
    );
    renderPage();

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Não foi possível carregar as sessões autorizadas",
    );
    expect(document.body.textContent).not.toContain("/home/private");
    expect(document.body.textContent).not.toContain("token-ultrassecreto");
  });

  it("refresh continua read-only e repete apenas a consulta GET do cliente", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Sessões autorizadas");

    await user.click(
      screen.getByRole("button", { name: "Atualizar catálogo de sessões" }),
    );

    await waitFor(() =>
      expect(mocks.getAppPaperSessions).toHaveBeenCalledTimes(2),
    );
    expect(mocks.getAppPaperSessions).toHaveBeenLastCalledWith(1, 20);
  });
});
