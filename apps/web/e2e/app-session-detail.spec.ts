import { expect, test } from "./fixtures/test";
import { ADMIN_EMAIL, E2E_PASSWORD, USER_EMAIL } from "./support/constants";
import { PAPER_SESSION_ID } from "./support/mock-services";

const DETAIL_PATH = `/api/v1/app/paper-trading/sessions/${PAPER_SESSION_ID}`;
const ANNOTATIONS_PATH = `${DETAIL_PATH}/chart-annotations`;
const TRADES_PATH = `${DETAIL_PATH}/trades`;
const CANDLES_PATH = "/api/v1/app/market-data/candles/BTC/USDT";

async function signIn(page: import("@playwright/test").Page, email: string) {
  await page.getByLabel("E-mail").fill(email);
  await page.getByLabel("Senha").fill(E2E_PASSWORD);
  await page.getByRole("button", { name: "Entrar" }).click();
}

test("project owner abre chart e trades autorizados sem endpoint administrativo", async ({
  page,
  services,
}) => {
  await page.goto("/app/sessions");
  await signIn(page, ADMIN_EMAIL);
  await expect(page).toHaveURL(/\/app\/sessions$/);

  await page.getByRole("link", { name: "Abrir sessão" }).click();
  await expect(page).toHaveURL(
    new RegExp(`/app/sessions/${PAPER_SESSION_ID}$`),
  );
  await expect(
    page.getByRole("heading", { name: "Chart e trades da sessão" }),
  ).toBeVisible();
  await expect(page.getByLabel("Gráfico financeiro interativo")).toBeVisible();
  await expect(page.getByText("Entrada executada")).toBeVisible();
  await expect(page.getByText("Stop protetivo")).toBeVisible();
  await expect(page.getByRole("cell", { name: "OPEN" })).toBeVisible();
  await expect(page.getByText("13.000000000000000000")).toBeVisible();

  for (const path of [
    DETAIL_PATH,
    CANDLES_PATH,
    ANNOTATIONS_PATH,
    TRADES_PATH,
  ]) {
    expect(services.requestsFor("GET", path).length).toBeGreaterThanOrEqual(1);
  }
  expect(
    services.requests.filter((request) =>
      request.pathname.startsWith("/api/v1/admin/"),
    ),
  ).toEqual([]);
});

test("non-admin em deep-link recebe 403 seguro e permanece autenticado", async ({
  page,
  services,
}) => {
  await page.goto(`/app/sessions/${PAPER_SESSION_ID}`);
  await signIn(page, USER_EMAIL);

  await expect(page).toHaveURL(
    new RegExp(`/app/sessions/${PAPER_SESSION_ID}$`),
  );
  await expect(
    page.getByText("Esta conta não possui acesso a esta sessão."),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Sair" })).toBeVisible();
  expect(
    services.requestsFor("GET", DETAIL_PATH).length,
  ).toBeGreaterThanOrEqual(1);
  expect(services.requestsFor("GET", CANDLES_PATH)).toEqual([]);
  expect(services.requestsFor("GET", ANNOTATIONS_PATH)).toEqual([]);
  expect(services.requestsFor("GET", TRADES_PATH)).toEqual([]);
  expect(
    services.requests.filter((request) =>
      request.pathname.startsWith("/api/v1/admin/"),
    ),
  ).toEqual([]);
});
