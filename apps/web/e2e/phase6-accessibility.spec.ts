import { expect, test } from "./fixtures/test";
import {
  captureRuntimeErrors,
  expectFocusedElementInsideViewport,
  expectLocalHorizontalScroll,
  expectNoPageOverflow,
  tabTo,
} from "./support/accessibility";
import { loginAsAdmin, loginToApp } from "./support/actions";
import {
  ADMIN_PAPER_SESSION_ID,
  PAPER_SESSION_ID,
} from "./support/mock-services";

test.describe("fechamento de acessibilidade da Phase 6", () => {
  test("mantém a navegação admin fechada fora da ordem de tabulação no mobile", async ({
    page,
  }) => {
    const runtimeErrors = captureRuntimeErrors(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await loginAsAdmin(page);
    await expect(
      page.getByRole("heading", { name: "Visão geral" }),
    ).toBeVisible();

    await page.reload();
    await expect(
      page.getByRole("heading", { name: "Visão geral" }),
    ).toBeVisible();

    const menu = page.getByRole("button", { name: "Alternar navegação" });
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await expect(
      page.getByRole("navigation", { name: "Navegação administrativa" }),
    ).toHaveCount(0);
    await expect(page.locator(".sidebar")).toHaveCSS("visibility", "hidden");
    await expect(page.locator(".sidebar")).toHaveCSS("pointer-events", "none");

    const closedNavigationLink = page.locator(".sidebar__link").first();
    const closedLinkBounds = await closedNavigationLink.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      return { left: bounds.left, right: bounds.right };
    });
    expect(closedLinkBounds.right).toBeLessThanOrEqual(0);

    await page.keyboard.press("Tab");

    await expect(menu).toBeFocused();
    await expectFocusedElementInsideViewport(page);
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "Sair" })).toBeFocused();
    await expectNoPageOverflow(page, "/admin");
    expect(runtimeErrors).toEqual([]);
  });

  test("abre pelo teclado, expõe links e fecha ao navegar sem perder o foco", async ({
    page,
  }) => {
    const runtimeErrors = captureRuntimeErrors(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await loginAsAdmin(page);
    await page.reload();
    await expect(
      page.getByRole("heading", { name: "Visão geral" }),
    ).toBeVisible();

    const menu = page.getByRole("button", { name: "Alternar navegação" });
    await page.keyboard.press("Tab");
    await expect(menu).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(menu).toHaveAttribute("aria-expanded", "true");

    const navigation = page.getByRole("navigation", {
      name: "Navegação administrativa",
    });
    await expect(navigation).toBeVisible();

    const overviewLink = navigation.getByRole("link", { name: "Visão geral" });

    await expect
      .poll(async () => {
        const box = await overviewLink.boundingBox();
        return box?.x ?? -1;
      })
      .toBeGreaterThanOrEqual(0);

    await page.keyboard.press("Tab");
    await expect(overviewLink).toBeFocused();
    await expectFocusedElementInsideViewport(page);

    const navigationLinks = navigation.getByRole("link");
    const navigationLinkCount = await navigationLinks.count();

    for (let index = 1; index < navigationLinkCount; index += 1) {
      await page.keyboard.press("Tab");
      await expectFocusedElementInsideViewport(page);
    }

    await expect(
      navigation.getByRole("link", { name: "Configurações", exact: true }),
    ).toBeFocused();
    await page.keyboard.press("Enter");

    await expect(page).toHaveURL(/\/admin\/settings$/);
    await expect(
      page.getByRole("heading", { name: "Configurações" }),
    ).toBeVisible();
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await expect(menu).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "Sair" })).toBeFocused();
    expect(runtimeErrors).toEqual([]);
  });

  test("restaura foco ao fechar pelo toggle e pelo scrim", async ({ page }) => {
    const runtimeErrors = captureRuntimeErrors(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await loginAsAdmin(page);
    await page.reload();
    await expect(
      page.getByRole("heading", { name: "Visão geral" }),
    ).toBeVisible();

    const menu = page.getByRole("button", { name: "Alternar navegação" });
    await page.keyboard.press("Tab");
    await page.keyboard.press("Enter");
    await expect(menu).toHaveAttribute("aria-expanded", "true");
    await page.keyboard.press("Space");
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await expect(menu).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "Sair" })).toBeFocused();

    await page.keyboard.press("Shift+Tab");
    await page.keyboard.press("Enter");
    await expect(menu).toHaveAttribute("aria-expanded", "true");
    await page
      .getByRole("button", { name: "Fechar navegação" })
      .click({ position: { x: 350, y: 400 } });
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await expect(menu).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "Sair" })).toBeFocused();
    expect(runtimeErrors).toEqual([]);
  });

  test("mantém o sidebar desktop visível e navegável pelo teclado", async ({
    page,
  }) => {
    const runtimeErrors = captureRuntimeErrors(page);
    await page.setViewportSize({ width: 1280, height: 800 });
    await loginAsAdmin(page);
    await page.reload();
    await expect(
      page.getByRole("heading", { name: "Visão geral" }),
    ).toBeVisible();

    await expect(
      page.getByRole("button", { name: "Alternar navegação" }),
    ).toBeHidden();
    const navigation = page.getByRole("navigation", {
      name: "Navegação administrativa",
    });
    await expect(navigation).toBeVisible();
    await expect(
      navigation.getByRole("link", { name: "Paper trading", exact: true }),
    ).toBeVisible();

    await page.keyboard.press("Tab");
    await expect(
      navigation.getByRole("link", { name: "Visão geral" }),
    ).toBeFocused();
    await expectFocusedElementInsideViewport(page);
    expect(runtimeErrors).toEqual([]);
  });
});

test.describe("closure browser e acessibilidade das surfaces Phase 6", () => {
  test("mantém home e login públicos acessíveis no mobile com reduced motion", async ({
    page,
  }) => {
    const runtimeErrors = captureRuntimeErrors(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");

    expect(
      await page.evaluate(
        () => matchMedia("(prefers-reduced-motion: reduce)").matches,
      ),
    ).toBe(true);
    await expect(
      page.getByRole("heading", { name: /Pesquisa disciplinada/ }),
    ).toBeVisible();
    await expect(page.getByRole("main")).toBeVisible();
    const loginCta = page.getByRole("link", { name: "Entrar" });
    await expect(loginCta).toBeVisible();
    await expect(loginCta).toHaveAttribute("href", "/login");
    await expect(
      page.getByRole("link", { name: /criar conta|registrar|sign up/i }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("link", { name: /administração|painel admin/i }),
    ).toHaveCount(0);
    await expectNoPageOverflow(page, "/");
    expect(
      await loginCta.evaluate(
        (element) => getComputedStyle(element).transitionDuration,
      ),
    ).toBe("0s");

    await loginCta.click();
    await expect(page).toHaveURL(/\/login$/);
    await expect(
      page.getByRole("heading", { name: "Entrar no ADT" }),
    ).toBeVisible();
    await expect(
      page.getByText("O ADT não oferece cadastro público."),
    ).toBeVisible();
    await expectNoPageOverflow(page, "/login");
    expect(runtimeErrors).toEqual([]);
  });

  test("mantém o app shell mobile acessível e sem API admin", async ({
    page,
    services,
  }) => {
    const runtimeErrors = captureRuntimeErrors(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await loginToApp(page);

    await expect(
      page.getByRole("heading", {
        name: /Paper trading, com acesso verificado/i,
      }),
    ).toBeVisible();
    await expectNoPageOverflow(page, "/app");
    await page.reload();
    await expect(
      page.getByRole("heading", {
        name: /Paper trading, com acesso verificado/i,
      }),
    ).toBeVisible();
    await page.keyboard.press("Tab");
    await expect(
      page.getByRole("link", { name: "Início da área autenticada" }),
    ).toBeFocused();
    await expectFocusedElementInsideViewport(page);

    expect(
      services.requests.filter((request) =>
        request.pathname.startsWith("/api/v1/admin/"),
      ),
    ).toEqual([]);
    expect(runtimeErrors).toEqual([]);
  });

  test("valida market mobile, teclado, pattern e resize sem API admin", async ({
    page,
    services,
  }) => {
    const runtimeErrors = captureRuntimeErrors(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await loginToApp(page);

    await page.goto("/app/market");
    await expect(
      page.getByRole("heading", { name: "Gráfico de mercado" }),
    ).toBeVisible();
    await expect(
      page.getByLabel("Gráfico financeiro interativo"),
    ).toBeVisible();
    await expect(page.getByLabel("Proveniência do dataset")).toContainText(
      "BTC/USDT",
    );
    await expect(page.getByLabel("Proveniência do dataset")).toContainText(
      "Intervalo carregado (UTC)",
    );
    await expect(page.getByLabel("Último candle carregado")).toContainText(
      "Horário (UTC)",
    );
    await expectNoPageOverflow(page, "/app/market");

    const baseAsset = page.getByLabel("Ativo base");
    const quoteAsset = page.getByLabel("Ativo de cotação");
    const assetPattern = String.raw`\s*[A-Za-z0-9][A-Za-z0-9._\-]{0,31}\s*`;
    await expect(baseAsset).toHaveAttribute("pattern", assetPattern);
    await expect(quoteAsset).toHaveAttribute("pattern", assetPattern);

    for (const value of [
      "BTC",
      "BTC1",
      "BTC_TEST",
      "BTC.TEST",
      "BTC-TEST",
      " eth ",
      "A".repeat(32),
    ]) {
      await baseAsset.fill(value);
      expect(
        await baseAsset.evaluate((element: HTMLInputElement) =>
          element.checkValidity(),
        ),
        `${value} deveria passar na validação nativa`,
      ).toBe(true);
    }

    for (const value of [
      "-BTC",
      "_BTC",
      ".BTC",
      " -BTC ",
      "BTC/USDT",
      "BTC USDT",
    ]) {
      await baseAsset.fill(value);
      expect(
        await baseAsset.evaluate((element: HTMLInputElement) => ({
          patternMismatch: element.validity.patternMismatch,
          valid: element.checkValidity(),
        })),
        `${value} deveria falhar por patternMismatch`,
      ).toEqual({ patternMismatch: true, valid: false });
    }

    const overLengthValidity = await baseAsset.evaluate(
      (element: HTMLInputElement) => {
        element.value = "A".repeat(33);
        return {
          patternMismatch: element.validity.patternMismatch,
          tooLong: element.validity.tooLong,
          valid: element.checkValidity(),
        };
      },
    );
    expect(overLengthValidity.valid).toBe(false);
    expect(
      overLengthValidity.patternMismatch || overLengthValidity.tooLong,
    ).toBe(true);

    await baseAsset.fill(" eth ");
    await quoteAsset.fill(" usdt ");
    expect(
      await baseAsset.evaluate((element: HTMLInputElement) =>
        element.checkValidity(),
      ),
    ).toBe(true);
    expect(
      await quoteAsset.evaluate((element: HTMLInputElement) =>
        element.checkValidity(),
      ),
    ).toBe(true);
    await page.getByRole("button", { name: "Aplicar seleção" }).click();
    await expect(baseAsset).toHaveValue("ETH");
    await expect(quoteAsset).toHaveValue("USDT");
    await expect(page.getByLabel("Proveniência do dataset")).toContainText(
      "ETH/USDT",
    );
    await expect
      .poll(
        () =>
          services.requestsFor(
            "GET",
            "/api/v1/app/market-data/candles/ETH/USDT",
          ).length,
      )
      .toBeGreaterThanOrEqual(1);

    await baseAsset.fill("BTC USDT");
    const candleRequestsBeforeInvalidSubmit = services.requests.filter(
      (request) =>
        request.pathname.startsWith("/api/v1/app/market-data/candles/"),
    ).length;
    await page.getByRole("button", { name: "Aplicar seleção" }).click();
    await expect
      .poll(
        () =>
          services.requests.filter((request) =>
            request.pathname.startsWith("/api/v1/app/market-data/candles/"),
          ).length,
      )
      .toBe(candleRequestsBeforeInvalidSubmit);
    expect(
      await baseAsset.evaluate(
        (element: HTMLInputElement) => element.validity.patternMismatch,
      ),
    ).toBe(true);
    await baseAsset.fill("BTC");
    await quoteAsset.fill("USDT");

    const timeframe = page.getByLabel("Timeframe");
    await tabTo(page, timeframe);
    await page.keyboard.press("ArrowDown");
    await expect(timeframe).toHaveValue("30m");
    await page.keyboard.press("Tab");
    const applyMarket = page.getByRole("button", { name: "Aplicar seleção" });
    await expect(applyMarket).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByLabel("Proveniência do dataset")).toContainText(
      "30m",
    );
    await expect
      .poll(
        () =>
          services
            .requestsFor("GET", "/api/v1/app/market-data/candles/BTC/USDT")
            .at(-1)?.search,
      )
      .toContain("timeframe=30m");

    await page.setViewportSize({ width: 1280, height: 800 });
    await expect(
      page.getByLabel("Gráfico financeiro interativo"),
    ).toBeVisible();
    await expect(page.getByLabel("Último candle carregado")).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByLabel("Último candle carregado")).toBeVisible();
    await expectNoPageOverflow(page, "/app/market após resize");

    expect(
      services.requests.filter((request) =>
        request.pathname.startsWith("/api/v1/admin/"),
      ),
    ).toEqual([]);
    expect(runtimeErrors).toEqual([]);
  });

  test("cobre sessions mobile, loading, detail e scroll local sem API admin", async ({
    page,
    services,
  }) => {
    const runtimeErrors = captureRuntimeErrors(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await loginToApp(page);

    const releaseCatalog = services.holdNextApiRequest(
      "GET",
      "/api/v1/app/paper-trading/sessions",
    );
    await page.goto("/app/sessions");
    await expect(
      page.getByText("Carregando sessões autorizadas…"),
    ).toBeVisible();
    releaseCatalog();
    await expect(
      page.getByRole("heading", { name: "Sessões autorizadas" }),
    ).toBeVisible();
    await expectNoPageOverflow(page, "/app/sessions");

    await page.goto(`/app/sessions/${PAPER_SESSION_ID}`);
    await expect(
      page.getByRole("heading", { name: "Chart e trades da sessão" }),
    ).toBeVisible();
    await expect(
      page.getByLabel("Gráfico financeiro interativo"),
    ).toBeVisible();
    await expect(page.getByLabel("Identificação da sessão")).toContainText(
      "15m · UTC",
    );
    await expect(page.getByLabel("Range UTC do gráfico")).toBeVisible();
    await expect(page.getByText("Entrada executada")).toBeVisible();
    await expect(page.getByText("BUY · 1.000000000000000000")).toBeVisible();
    await expect(page.getByRole("cell", { name: "OPEN" })).toBeVisible();
    const detailTableContainer = page.locator(".table-wrap").last();
    await expectLocalHorizontalScroll(detailTableContainer);
    await expectNoPageOverflow(page, `/app/sessions/${PAPER_SESSION_ID}`);

    expect(
      services.requests.filter((request) =>
        request.pathname.startsWith("/api/v1/admin/"),
      ),
    ).toEqual([]);
    expect(runtimeErrors).toEqual([]);
  });

  test("cobre performance app mobile, teclado e alternativas sem API admin", async ({
    page,
    services,
  }) => {
    const runtimeErrors = captureRuntimeErrors(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await loginToApp(page);

    await page.goto(`/app/sessions/${PAPER_SESSION_ID}/performance`);
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
      page.getByRole("columnheader", { name: "Equity" }),
    ).toBeVisible();
    await expect(
      page.getByRole("columnheader", { name: "PnL não realizado" }),
    ).toBeVisible();
    await expect(
      page.getByRole("columnheader", { name: "Fees", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("columnheader", { name: "Slippage", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("columnheader", { name: "Drawdown", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("cell", { name: "Inativo" }).first(),
    ).toBeVisible();
    await expect(
      page.getByText(/Realized-only · calendário UTC/),
    ).toBeVisible();
    await expectLocalHorizontalScroll(page.locator(".table-wrap").first());
    await expectNoPageOverflow(
      page,
      `/app/sessions/${PAPER_SESSION_ID}/performance`,
    );

    const granularity = page.getByLabel("Granularidade");
    await tabTo(page, granularity);
    await page.keyboard.press("ArrowDown");
    await expect(granularity).toHaveValue("WEEKLY");
    const applyPeriod = page.getByRole("button", { name: "Aplicar período" });
    await tabTo(page, applyPeriod);
    await expectFocusedElementInsideViewport(page);
    await page.keyboard.press("Enter");
    await expect
      .poll(
        () =>
          services
            .requestsFor(
              "GET",
              `/api/v1/app/paper-trading/sessions/${PAPER_SESSION_ID}/period-metrics`,
            )
            .at(-1)?.search,
      )
      .toContain("granularity=WEEKLY");
    const focusAfterUpdate = await page.evaluate(() => {
      const active = document.activeElement as HTMLElement | null;
      return {
        display: active ? getComputedStyle(active).display : null,
        hiddenAncestor: Boolean(
          active?.closest('[hidden], [aria-hidden="true"], .sidebar:not(.sidebar--open)'),
        ),
        visibility: active ? getComputedStyle(active).visibility : null,
      };
    });
    expect(focusAfterUpdate.hiddenAncestor).toBe(false);
    expect(focusAfterUpdate.display).not.toBe("none");
    expect(focusAfterUpdate.visibility).not.toBe("hidden");
    await page.keyboard.press("Tab");
    await expectFocusedElementInsideViewport(page);
    await expect(granularity.locator("option:checked")).toHaveText("Semanal");
    await expect(
      page.getByRole("columnheader", { name: "Período UTC" }),
    ).toBeVisible();

    expect(
      services.requests.filter((request) =>
        request.pathname.startsWith("/api/v1/admin/"),
      ),
    ).toEqual([]);
    expect(runtimeErrors).toEqual([]);
  });

  test("cobre surfaces admin Phase 6 mobile e alternativas acessíveis", async ({
    page,
    services,
  }) => {
    const runtimeErrors = captureRuntimeErrors(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await loginAsAdmin(page, "/admin/paper-trading");

    await expect(
      page.getByRole("heading", { name: "Paper trading" }),
    ).toBeVisible();
    await expectNoPageOverflow(page, "/admin/paper-trading");

    const chartUrl = `/admin/paper-trading/chart?session_id=${ADMIN_PAPER_SESSION_ID}&base=BTC&quote=USDT&timeframe=1m`;
    await page.goto(chartUrl);
    await expect(
      page.getByRole("heading", { name: "Gráfico de mercado" }),
    ).toBeVisible();
    await expect(
      page.getByLabel("Gráfico financeiro interativo"),
    ).toBeVisible();
    await expect(page.getByLabel("Metadados do dataset")).toContainText(
      "1m · UTC",
    );
    await expect(
      page.getByLabel("Resumo textual do último candle"),
    ).toContainText("Abertura UTC");
    await expect(
      page.getByRole("heading", { name: "Eventos da sessão" }),
    ).toBeVisible();
    await expect(page.getByText("Entrada executada")).toBeVisible();
    await expect(page.getByText("BUY 0,5 a 112 · trade #2")).toBeVisible();
    await expect(page.getByText("Stop protetivo").first()).toBeVisible();
    expect(
      await page
        .getByRole("button", { name: "Atualizar" })
        .evaluate((element) => getComputedStyle(element).transitionDuration),
    ).toBe("0s");
    expect(
      await page.evaluate(
        () => matchMedia("(prefers-reduced-motion: reduce)").matches,
      ),
    ).toBe(true);
    await expectNoPageOverflow(page, chartUrl);

    await page.setViewportSize({ width: 1280, height: 800 });
    await expect(
      page.getByLabel("Gráfico financeiro interativo"),
    ).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(
      page.getByLabel("Resumo textual do último candle"),
    ).toBeVisible();
    await expectNoPageOverflow(page, `${chartUrl} após resize`);

    const journalUrl = `/admin/paper-trading/journal?session_id=${ADMIN_PAPER_SESSION_ID}`;
    await page.goto(journalUrl);
    await expect(
      page.getByRole("heading", { name: "Trade journal" }),
    ).toBeVisible();
    await expect(page.getByText(`Trade ${"e".repeat(12)}`)).toBeVisible();
    await expect(
      page.getByLabel("Operações").getByText("Aberta", { exact: true }),
    ).toBeVisible();
    await expectNoPageOverflow(page, journalUrl);

    const performanceUrl = `/admin/paper-trading/performance?session_id=${ADMIN_PAPER_SESSION_ID}`;
    await page.goto(performanceUrl);
    await expect(
      page.getByRole("heading", { name: "Performance histórica" }),
    ).toBeVisible();
    await expect(page.getByLabel("Resumo da timeline carregada")).toContainText(
      "Equity",
    );
    await expect(page.getByLabel("Resumo da timeline carregada")).toContainText(
      "PnL realizado",
    );
    await expect(page.getByLabel("Resumo da timeline carregada")).toContainText(
      "PnL não realizado",
    );
    await expect(page.getByLabel("Resumo da timeline carregada")).toContainText(
      "Drawdown atual",
    );
    await expect(page.getByLabel("Resumo da timeline carregada")).toContainText(
      "Taxas acumuladas",
    );
    await expect(page.getByLabel("Resumo da timeline carregada")).toContainText(
      "Slippage acumulado",
    );
    await expect(
      page.getByRole("columnheader", { name: "Candle" }),
    ).toBeVisible();
    await expectLocalHorizontalScroll(page.locator(".table-wrap").last());
    await expectNoPageOverflow(page, performanceUrl);

    const periodUrl = "/admin/paper-trading/period-metrics";
    await page.goto(periodUrl);
    await expect(
      page.getByRole("heading", { name: "Performance por período" }),
    ).toBeVisible();
    await expect(page.getByText("Escopo contábil realized-only")).toBeVisible();
    await expect(page.getByLabel("Totais do período")).toContainText(
      "1 positivas · 1 negativas",
    );
    await expect(
      page.getByRole("heading", { name: "Série contínua" }),
    ).toBeVisible();
    await expect(
      page.getByText("1 períodos com realizações · USDT"),
    ).toBeVisible();
    await expect(
      page.getByRole("columnheader", { name: "Período UTC" }),
    ).toBeVisible();
    await expectLocalHorizontalScroll(page.locator(".table-wrap").last());
    await expectNoPageOverflow(page, periodUrl);

    expect(
      services.requests.filter((request) =>
        request.pathname.startsWith("/api/v1/public/"),
      ),
    ).toEqual([]);
    const appRequests = services.requests.filter((request) =>
      request.pathname.startsWith("/api/v1/app/"),
    );
    expect(appRequests.length).toBeGreaterThanOrEqual(1);
    expect(
      appRequests.every((request) => request.pathname === "/api/v1/app/me"),
    ).toBe(true);
    expect(runtimeErrors).toEqual([]);
  });
});
