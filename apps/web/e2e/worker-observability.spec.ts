import { expect, test } from "./fixtures/test";
import {
  captureRuntimeErrors,
  expectNoPageOverflow,
} from "./support/accessibility";
import { loginAsAdmin } from "./support/actions";

const PAGE_PATH = "/admin/worker-observability";
const RUNTIMES_PATH = "/api/v1/admin/market-data/worker-observability/runtimes";
const EVENTS_PATH = "/api/v1/admin/market-data/worker-observability/events";

test.describe("observabilidade administrativa do worker", () => {
  test("protege a rota e apresenta observabilidade read-only no layout móvel", async ({
    page,
    services,
  }) => {
    await page.setViewportSize({
      width: 390,
      height: 844,
    });

    const runtimeErrors = captureRuntimeErrors(page);

    await loginAsAdmin(page, PAGE_PATH);

    await expect(
      page.getByRole("heading", {
        name: "Worker de market data",
      }),
    ).toBeVisible();

    await expect(
      page.getByText(/heartbeat atrasado não confirma processo morto/i),
    ).toBeVisible();

    await expect(page.getByText("Heartbeat recente").first()).toBeVisible();

    await expect(page.getByText("Falha confirmada")).toBeVisible();

    await expect(page.getByText("Operação liquidada")).toBeVisible();

    await expect(page.getByText("Runtime iniciado")).toBeVisible();

    await expect(
      page.getByRole("button", {
        name: /iniciar|parar|reiniciar|pausar|retomar/i,
      }),
    ).toHaveCount(0);

    const runtimeRequests = services.requestsFor("GET", RUNTIMES_PATH);
    const eventRequests = services.requestsFor("GET", EVENTS_PATH);

    /*
     * O Playwright executa o Vite em development e a aplicação usa
     * React.StrictMode. Portanto, effects de montagem podem ser
     * executados novamente. O contrato relevante é:
     *
     * - pelo menos uma consulta;
     * - runtimes/events consultados em pares;
     * - somente GET;
     * - limites bounded sempre preservados.
     */
    expect(runtimeRequests.length).toBeGreaterThan(0);
    expect(eventRequests.length).toBe(runtimeRequests.length);

    for (const request of runtimeRequests) {
      expect(request.search).toBe("?limit=20");
    }

    for (const request of eventRequests) {
      expect(request.search).toBe("?limit=50");
    }

    expect(services.requestsFor("POST", RUNTIMES_PATH)).toHaveLength(0);

    expect(services.requestsFor("POST", EVENTS_PATH)).toHaveLength(0);

    await expectNoPageOverflow(page, "worker observability mobile");

    expect(runtimeErrors).toEqual([]);
  });

  test("refresh manual repete somente os GETs bounded", async ({
    page,
    services,
  }) => {
    await loginAsAdmin(page, PAGE_PATH);

    await expect(
      page.getByRole("heading", {
        name: "Worker de market data",
      }),
    ).toBeVisible();

    await expect(page.getByText("Heartbeat recente").first()).toBeVisible();

    const runtimeRequestsBeforeRefresh = services.requestsFor(
      "GET",
      RUNTIMES_PATH,
    ).length;

    const eventRequestsBeforeRefresh = services.requestsFor(
      "GET",
      EVENTS_PATH,
    ).length;

    expect(runtimeRequestsBeforeRefresh).toBeGreaterThan(0);
    expect(eventRequestsBeforeRefresh).toBe(runtimeRequestsBeforeRefresh);

    await page.getByRole("button", { name: "Atualizar" }).click();

    await expect
      .poll(() => services.requestsFor("GET", RUNTIMES_PATH).length)
      .toBe(runtimeRequestsBeforeRefresh + 1);

    await expect
      .poll(() => services.requestsFor("GET", EVENTS_PATH).length)
      .toBe(eventRequestsBeforeRefresh + 1);

    const runtimeRequestsAfterRefresh = services.requestsFor(
      "GET",
      RUNTIMES_PATH,
    );

    const eventRequestsAfterRefresh = services.requestsFor("GET", EVENTS_PATH);

    expect(runtimeRequestsAfterRefresh.at(-1)?.search).toBe("?limit=20");

    expect(eventRequestsAfterRefresh.at(-1)?.search).toBe("?limit=50");

    expect(services.requestsFor("POST", RUNTIMES_PATH)).toHaveLength(0);

    expect(services.requestsFor("POST", EVENTS_PATH)).toHaveLength(0);
  });

  test("403 administrativo não encerra a sessão", async ({
    page,
    services,
  }) => {
    await loginAsAdmin(page, "/admin/settings");

    services.rejectNextAdminRequestsWith403(100);

    await page.getByRole("link", { name: "Worker runtime" }).click();

    await expect(page).toHaveURL(/\/admin\/worker-observability$/);

    await expect(
      page.getByRole("heading", {
        name: "Worker de market data",
      }),
    ).toBeVisible();

    await expect(page.getByRole("alert")).toContainText(
      "Acesso administrativo negado",
    );

    await expect(page.getByRole("button", { name: "Sair" })).toBeVisible();

    await expect(page).not.toHaveURL(/\/admin\/login$/);
  });
});
