import { expect, test } from "./fixtures/test";
import { loginAsAdmin } from "./support/actions";

test.describe("dashboard de paper trading", () => {
  test("carrega somente por GET autenticado e compara duas sessões", async ({
    page,
    services,
  }) => {
    await loginAsAdmin(page, "/admin/paper-trading");

    await expect(
      page.getByRole("heading", { name: "Paper trading" }),
    ).toBeVisible();
    await expect(page.getByText("Ciclo 9")).toBeVisible();
    await expect(page.getByText("BTC/USDT")).toBeVisible();
    await expect(page.getByText("ETH/USDT")).toBeVisible();
    await expect(page.getByText("12,55%").first()).toBeVisible();
    await expect(page.getByText("Tendência · Alta")).toBeVisible();

    const compare = page.getByRole("checkbox", { name: "Comparar" });
    await compare.nth(0).check();
    await compare.nth(1).check();
    await expect(
      page.getByRole("heading", { name: "Sessões selecionadas" }),
    ).toBeVisible();

    const requests = services.requestsFor(
      "GET",
      "/api/v1/admin/paper-trading/dashboard",
    );
    expect(requests.length).toBeGreaterThanOrEqual(1);

    for (const request of requests) {
      expect(request.search).toBe("?page=1&page_size=20");
      expect(request.authorization).toBe(
        `Bearer ${services.lastIssuedAccessToken}`,
      );
    }
    expect(
      services.requests.filter(
        (request) =>
          request.pathname === "/api/v1/admin/paper-trading/dashboard" &&
          request.method !== "GET",
      ),
    ).toEqual([]);
  });
});
