import { expect, test } from "./fixtures/test";
import { E2E_PASSWORD, USER_EMAIL } from "./support/constants";

test("usuário non-admin consulta o gráfico autenticado sem API administrativa e faz logout", async ({
  page,
  services,
}) => {
  await page.goto("/login");
  await page.getByLabel("E-mail").fill(USER_EMAIL);
  await page.getByLabel("Senha").fill(E2E_PASSWORD);
  await page.getByRole("button", { name: "Entrar" }).click();

  await expect(page).toHaveURL(/\/app$/);
  await page.getByRole("link", { name: "Mercado" }).click();
  await expect(page).toHaveURL(/\/app\/market$/);
  await expect(
    page.getByRole("heading", { name: "Gráfico de mercado" }),
  ).toBeVisible();
  await expect(page.getByText("BTC/USDT")).toBeVisible();
  await expect(page.getByLabel("Timeframe")).toHaveValue("15m");
  await expect(
    page.getByLabel("Gráfico financeiro interativo"),
  ).toBeVisible();
  await expect(page.getByText("2.500000000000000000")).toBeVisible();
  await expect(page.getByLabel(/sessão/i)).toHaveCount(0);

  const candlePath = "/api/v1/app/market-data/candles/BTC/USDT";
  const candleRequests = services.requestsFor("GET", candlePath);
  expect(candleRequests.length).toBeGreaterThanOrEqual(1);
  expect(candleRequests.at(-1)?.search).toContain("timeframe=15m");
  expect(candleRequests.at(-1)?.authorization).toBe(
    `Bearer ${services.lastIssuedAccessToken}`,
  );
  expect(
    services.requests.filter((request) =>
      request.pathname.startsWith("/api/v1/admin/"),
    ),
  ).toEqual([]);

  await page.getByRole("button", { name: "Sair" }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect(
    await page.evaluate(() =>
      Object.keys(localStorage).filter(
        (key) => key.startsWith("sb-") && key.endsWith("-auth-token"),
      ),
    ),
  ).toEqual([]);
});
