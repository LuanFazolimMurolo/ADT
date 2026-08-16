import { expect, test } from "./fixtures/test";
import { loginAsAdmin } from "./support/actions";
import {
  ADMIN_MARKET_DATASET_ID,
  ADMIN_MARKET_OPERATION_ID,
} from "./support/mock-services";

const PREVIEW_PATH = "/api/v1/admin/market-data/operations/preview/backfill";
const OPERATIONS_PATH = "/api/v1/admin/market-data/operations";

test.describe("operações administrativas de mercado", () => {
  test("faz preview, confirma, submete e solicita pausa no layout móvel", async ({
    page,
    services,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await loginAsAdmin(page, "/admin/market-operations");

    await expect(
      page.getByRole("heading", { name: "Operações de mercado" }),
    ).toBeVisible();
    await page.getByLabel("Timeframe operacional").selectOption("12h");
    await page.getByLabel("Início do intervalo (UTC)").fill("2026-08-01T00:00");
    await page
      .getByLabel("Fim do intervalo (UTC, exclusivo)")
      .fill("2026-08-03T00:00");
    await page.getByRole("button", { name: "Gerar prévia" }).click();

    await expect(
      page.getByRole("heading", {
        name: "RAW backfill · BTC/USDT · 12h",
      }),
    ).toBeVisible();
    expect(services.requestsFor("POST", PREVIEW_PATH)).toHaveLength(1);
    expect(services.requestsFor("POST", PREVIEW_PATH)[0].body).toEqual({
      dataset_id: ADMIN_MARKET_DATASET_ID,
      range_start: "2026-08-01T00:00:00Z",
      range_end: "2026-08-03T00:00:00Z",
    });

    await page.getByRole("button", { name: "Confirmar e submeter" }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Confirmar e submeter" }).click();

    await expect(
      page.getByText(`Operação ${ADMIN_MARKET_OPERATION_ID} submetida.`),
    ).toBeVisible();
    const submissions = services.requestsFor("POST", OPERATIONS_PATH);
    expect(submissions).toHaveLength(1);
    expect(submissions[0].body).toEqual({
      operation_type: "RAW_BACKFILL",
      dataset_id: ADMIN_MARKET_DATASET_ID,
      range_start: "2026-08-01T00:00:00Z",
      range_end: "2026-08-03T00:00:00Z",
      plan_checksum: "a".repeat(64),
      idempotency_key: expect.stringMatching(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
      ),
      confirmed: true,
    });

    await expect(page.getByText("Versão 1")).toBeVisible();
    await page.getByRole("button", { name: "Solicitar pausa" }).click();
    await expect(
      page.getByLabel("Detalhe da operação").getByText("Pausa solicitada"),
    ).toBeVisible();

    const pausePath = `${OPERATIONS_PATH}/${ADMIN_MARKET_OPERATION_ID}/pause`;
    const pauseRequests = services.requestsFor("POST", pausePath);
    expect(pauseRequests).toHaveLength(1);
    expect(pauseRequests[0].body).toEqual({ expected_version: 1 });

    const hasHorizontalOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    );
    expect(hasHorizontalOverflow).toBe(false);
  });

  test("trata conflito de versão sem repetir a mutação", async ({
    page,
    services,
  }) => {
    await loginAsAdmin(page, "/admin/market-operations");
    await page
      .getByRole("button", {
        name: "Inspecionar RAW backfill BTC/USDT 12h",
      })
      .click();
    await expect(page.getByText("Versão 2")).toBeVisible();

    services.conflictNextMarketOperationControl();
    await page.getByRole("button", { name: "Solicitar pausa" }).click();

    await expect(page.getByRole("alert")).toContainText(
      "A operação mudou no servidor",
    );
    await expect(page.getByText("Versão 3")).toBeVisible();
    const pausePath = `${OPERATIONS_PATH}/${ADMIN_MARKET_OPERATION_ID}/pause`;
    expect(services.requestsFor("POST", pausePath)).toHaveLength(1);
    expect(
      services.requestsFor(
        "GET",
        `${OPERATIONS_PATH}/${ADMIN_MARKET_OPERATION_ID}`,
    ).length,
    ).toBeGreaterThanOrEqual(2);
  });

  test("apresenta negação 403 sem encerrar a sessão administrativa", async ({
    page,
    services,
  }) => {
    await loginAsAdmin(page, "/admin/settings");
    services.rejectNextAdminRequestsWith403(100);
    await page.getByRole("link", { name: "Operações de mercado" }).click();

    await expect(page).toHaveURL(/\/admin\/market-operations$/);
    await expect(
      page.getByRole("heading", { name: "Operações de mercado" }),
    ).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(2);
    await expect(page.getByRole("alert").first()).toContainText(
      "Acesso administrativo negado",
    );
    await expect(page.getByRole("button", { name: "Sair" })).toBeVisible();
  });
});
