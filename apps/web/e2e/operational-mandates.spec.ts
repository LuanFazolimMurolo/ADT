import { expect, test } from "./fixtures/test";
import {
  captureRuntimeErrors,
  expectNoPageOverflow,
} from "./support/accessibility";
import { loginAsAdmin } from "./support/actions";
import { ADMIN_OPERATIONAL_MANDATE_ID } from "./support/mock-services";

const PAGE_PATH = "/admin/operational-mandates";
const MANDATES_PATH = "/api/v1/admin/operational-mandates";

test.describe("mandatos operacionais administrativos", () => {
  test("protege a rota e permite revisão corrente/histórica no layout móvel", async ({
    page,
    services,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const runtimeErrors = captureRuntimeErrors(page);

    await loginAsAdmin(page, PAGE_PATH);
    await expect(
      page.getByRole("heading", { name: "Mandatos operacionais" }),
    ).toBeVisible();
    await expect(page.getByLabel("Revisão atual exata")).toContainText(
      "BTC/USDT",
    );

    await page
      .getByRole("button", { name: "Inspecionar revisão histórica 1" })
      .click();
    await expect(page.getByLabel("Revisão histórica exata")).toContainText(
      "Mandato histórico",
    );
    await expect(page.getByLabel("Revisão histórica exata")).toContainText(
      "somente a especificação histórica",
    );

    const catalogRequests = services.requestsFor("GET", MANDATES_PATH);
    expect(catalogRequests.length).toBeGreaterThan(0);
    for (const request of catalogRequests) {
      expect(request.search).toBe("?limit=20&offset=0");
    }
    const historyPath = `${MANDATES_PATH}/${ADMIN_OPERATIONAL_MANDATE_ID}/revisions`;
    expect(services.requestsFor("GET", historyPath).at(-1)?.search).toBe(
      "?limit=20&offset=0",
    );
    await expectNoPageOverflow(page, "operational mandates mobile");
    expect(runtimeErrors).toEqual([]);
  });

  test("cria, substitui, aprova e arquiva com os tokens exatos", async ({
    page,
    services,
  }) => {
    await loginAsAdmin(page, PAGE_PATH);
    await expect(page.getByText("Mandato BTC principal").first()).toBeVisible();

    await page.getByRole("button", { name: "Novo mandato" }).click();
    await page.getByLabel("Nome").fill("Mandato criado no navegador");
    await page.getByLabel("Descrição").fill("Escopo deliberado");
    await page.getByLabel("Ativo base").fill("ETH");
    await page.getByRole("button", { name: "Revisar criação" }).click();
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "Criar mandato DRAFT" })
      .click();
    await expect(page.getByText(/criado como DRAFT/)).toBeVisible();

    const createBody = services.requestsFor("POST", MANDATES_PATH)[0]?.body;
    expect(createBody).toEqual({
      specification: {
        schema_version: 1,
        name: "Mandato criado no navegador",
        description: "Escopo deliberado",
        instruments: [
          {
            exchange: "binance",
            market_type: "spot",
            base_asset: "ETH",
            quote_asset: "USDT",
          },
        ],
      },
      idempotency_key: expect.stringMatching(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
      ),
    });
    expect(JSON.stringify(createBody)).not.toContain("actor");

    await page.getByRole("button", { name: "Substituir rascunho" }).click();
    await page.getByLabel("Nome").fill("Mandato revisado no navegador");
    await page
      .getByRole("button", { name: "Substituir com tokens exibidos" })
      .click();
    await expect(page.getByText(/Rascunho substituído pela revisão 2/)).toBeVisible();

    const mandatePath = `${MANDATES_PATH}/${ADMIN_OPERATIONAL_MANDATE_ID}`;
    expect(services.requestsFor("PATCH", mandatePath)[0]?.body).toMatchObject({
      expected_revision: 1,
      expected_record_version: 1,
      specification: { name: "Mandato revisado no navegador" },
    });

    await page.getByRole("button", { name: "Aprovar revisão atual" }).click();
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "Aprovar esta revisão" })
      .click();
    await expect(page.getByText(/Aprovado \(APPROVED\)/).last()).toBeVisible();
    expect(
      services.requestsFor("POST", `${mandatePath}/approve`)[0]?.body,
    ).toEqual({
      expected_revision: 2,
      expected_checksum: "d".repeat(64),
      expected_record_version: 2,
    });

    await page.getByRole("button", { name: "Arquivar mandato" }).click();
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "Arquivar mandato" })
      .click();
    await expect(page.getByText(/Arquivado \(ARCHIVED\)/).last()).toBeVisible();
    expect(
      services.requestsFor("POST", `${mandatePath}/archive`)[0]?.body,
    ).toEqual({ expected_record_version: 3 });
  });

  test("recarrega conflito sem repetir a aprovação e exige nova revisão", async ({
    page,
    services,
  }) => {
    await loginAsAdmin(page, PAGE_PATH);
    await expect(page.getByText("Versão do registro")).toBeVisible();

    services.conflictNextOperationalMandateMutation();
    await page.getByRole("button", { name: "Aprovar revisão atual" }).click();
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "Aprovar esta revisão" })
      .click();

    await expect(page.getByText(/mudou no servidor/)).toBeVisible();
    await expect(
      page.getByRole("button", {
        name: "Marcar estado recarregado como revisado",
      }),
    ).toBeVisible();
    const approvePath = `${MANDATES_PATH}/${ADMIN_OPERATIONAL_MANDATE_ID}/approve`;
    expect(services.requestsFor("POST", approvePath)).toHaveLength(1);
    expect(
      services.requestsFor(
        "GET",
        `${MANDATES_PATH}/${ADMIN_OPERATIONAL_MANDATE_ID}`,
      ).length,
    ).toBeGreaterThanOrEqual(2);
  });
});
