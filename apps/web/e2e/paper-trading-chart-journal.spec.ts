import { expect, test } from "./fixtures/test";
import { captureRuntimeErrors } from "./support/accessibility";
import { loginAsAdmin } from "./support/actions";
import {
  ADMIN_PAPER_SESSION_ID,
  ADMIN_PAPER_TRADE_ID,
} from "./support/mock-services";

const JOURNAL_PATH = "/api/v1/admin/paper-trading/journal";
const CANDLES_PATH = "/api/v1/admin/market-data/candles/BTC/USDT";
const ANNOTATIONS_PATH = `/api/v1/admin/paper-trading/sessions/${ADMIN_PAPER_SESSION_ID}/chart-annotations`;

function exactUrl(path: string): RegExp {
  return new RegExp(`${path.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`);
}

test("preserva identidade e contexto na jornada administrativa journal ↔ chart", async ({
  page,
  services,
}) => {
  const runtimeErrors = captureRuntimeErrors(page);
  const journalUrl = `/admin/paper-trading/journal?session_id=${ADMIN_PAPER_SESSION_ID}&trade_id=${ADMIN_PAPER_TRADE_ID}`;
  const chartUrl = `/admin/paper-trading/chart?session_id=${ADMIN_PAPER_SESSION_ID}&base=BTC&quote=USDT&timeframe=1m&trade_id=${ADMIN_PAPER_TRADE_ID}`;

  await loginAsAdmin(page, journalUrl);
  await expect(
    page.getByRole("heading", { name: "Trade journal" }),
  ).toBeVisible();

  const selectedTrade = page.locator(".journal-trade-card--selected");
  await expect(selectedTrade).toContainText(
    `Trade ${ADMIN_PAPER_TRADE_ID.slice(0, 12)}`,
  );
  await expect(selectedTrade).toContainText("Aberta");
  await expect(selectedTrade).toContainText("BTC/USDT");
  await selectedTrade.getByRole("link", { name: "Abrir no gráfico" }).click();

  await expect(page).toHaveURL(exactUrl(chartUrl));
  await expect(
    page.getByRole("heading", { name: "Gráfico de mercado" }),
  ).toBeVisible();
  await expect(page.getByLabel("Metadados do dataset")).toContainText(
    "BTC/USDT",
  );
  await expect(page.getByLabel("Metadados do dataset")).toContainText(
    "1m · UTC",
  );
  await expect(page.getByLabel("Sessão selecionada")).toContainText(
    ADMIN_PAPER_SESSION_ID,
  );
  await expect(
    page.getByLabel("Resumo textual do último candle"),
  ).toContainText("Abertura UTC");

  const selectedAnnotation = page.locator(
    ".instrument-chart-annotation--selected",
  );
  await expect(selectedAnnotation).toContainText("Entrada executada");
  await expect(selectedAnnotation).toContainText("BUY 0,5 a 112 · trade #2");
  await expect(
    page.getByRole("heading", { name: "Eventos da sessão" }),
  ).toBeVisible();

  await page.getByRole("link", { name: "Abrir journal da sessão" }).click();
  await expect(page).toHaveURL(exactUrl(journalUrl));
  await expect(
    page.getByRole("heading", { name: "Trade journal" }),
  ).toBeVisible();
  await expect(page.locator(".journal-trade-card--selected")).toContainText(
    `Trade ${ADMIN_PAPER_TRADE_ID.slice(0, 12)}`,
  );

  expect(
    services.requestsFor("GET", JOURNAL_PATH).length,
  ).toBeGreaterThanOrEqual(2);
  expect(
    services.requestsFor("GET", CANDLES_PATH).length,
  ).toBeGreaterThanOrEqual(1);
  expect(
    services.requestsFor("GET", ANNOTATIONS_PATH).length,
  ).toBeGreaterThanOrEqual(1);
  const appRequests = services.requests.filter((request) =>
    request.pathname.startsWith("/api/v1/app/"),
  );
  expect(appRequests.length).toBeGreaterThanOrEqual(1);
  expect(
    appRequests.every((request) => request.pathname === "/api/v1/app/me"),
  ).toBe(true);
  expect(
    services.requests.filter((request) =>
      request.pathname.startsWith("/api/v1/public/"),
    ),
  ).toEqual([]);
  expect(runtimeErrors).toEqual([]);
});
