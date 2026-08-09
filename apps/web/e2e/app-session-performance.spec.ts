import { expect, test } from "./fixtures/test";
import { ADMIN_EMAIL, E2E_PASSWORD, USER_EMAIL } from "./support/constants";
import { PAPER_SESSION_ID } from "./support/mock-services";

const DETAIL_PATH = `/api/v1/app/paper-trading/sessions/${PAPER_SESSION_ID}`;
const TIMELINE_PATH = `${DETAIL_PATH}/portfolio-timeline`;
const PERIOD_PATH = `${DETAIL_PATH}/period-metrics`;

async function signIn(page: import("@playwright/test").Page, email: string) {
  await page.getByLabel("E-mail").fill(email);
  await page.getByLabel("Senha").fill(E2E_PASSWORD);
  await page.getByRole("button", { name: "Entrar" }).click();
}

test("project owner navega até a performance autorizada sem endpoint administrativo", async ({
  page,
  services,
}) => {
  await page.goto("/app/sessions");
  await signIn(page, ADMIN_EMAIL);
  await page.getByRole("link", { name: "Abrir sessão" }).click();
  await page.getByRole("link", { name: "Performance" }).click();

  await expect(page).toHaveURL(
    new RegExp(`/app/sessions/${PAPER_SESSION_ID}/performance$`),
  );
  await expect(
    page.getByRole("heading", { name: "Performance da sessão" }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Equity histórica", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Drawdown histórico", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("PnL realizado por período", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(/Esta visão inclui apenas resultados realizados/),
  ).toBeVisible();
  await expect(page.getByText("1009.800000000000000000")).toBeVisible();

  expect(
    services.requestsFor("GET", DETAIL_PATH).length,
  ).toBeGreaterThanOrEqual(2);
  expect(
    services.requestsFor("GET", TIMELINE_PATH).length,
  ).toBeGreaterThanOrEqual(1);
  expect(
    services.requestsFor("GET", PERIOD_PATH).length,
  ).toBeGreaterThanOrEqual(1);
  expect(
    services.requests.filter((request) =>
      request.pathname.startsWith("/api/v1/admin/"),
    ),
  ).toEqual([]);
});

test("non-admin em performance direta recebe 403 e permanece autenticado", async ({
  page,
  services,
}) => {
  await page.goto(`/app/sessions/${PAPER_SESSION_ID}/performance`);
  await signIn(page, USER_EMAIL);

  await expect(
    page.getByText("Esta conta não possui acesso a esta sessão."),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Sair" })).toBeVisible();
  expect(
    services.requestsFor("GET", DETAIL_PATH).length,
  ).toBeGreaterThanOrEqual(1);
  expect(services.requestsFor("GET", TIMELINE_PATH)).toEqual([]);
  expect(services.requestsFor("GET", PERIOD_PATH)).toEqual([]);
  expect(
    services.requests.filter((request) =>
      request.pathname.startsWith("/api/v1/admin/"),
    ),
  ).toEqual([]);
});
