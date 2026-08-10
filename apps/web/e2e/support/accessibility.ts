import { expect, type Locator, type Page } from "@playwright/test";

export function captureRuntimeErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") {
      errors.push(`console.error: ${message.text()}`);
    }
  });
  return errors;
}

export async function expectFocusedElementInsideViewport(
  page: Page,
): Promise<void> {
  const focusBounds = await page.evaluate(() => {
    const bounds = document.activeElement?.getBoundingClientRect();
    return {
      bottom: bounds?.bottom,
      left: bounds?.left,
      right: bounds?.right,
      top: bounds?.top,
      viewportHeight: document.documentElement.clientHeight,
      viewportWidth: document.documentElement.clientWidth,
    };
  });

  expect(focusBounds.left).toBeGreaterThanOrEqual(0);
  expect(focusBounds.top).toBeGreaterThanOrEqual(0);
  expect(focusBounds.right).toBeLessThanOrEqual(focusBounds.viewportWidth);
  expect(focusBounds.bottom).toBeLessThanOrEqual(focusBounds.viewportHeight);
}

export async function expectNoPageOverflow(
  page: Page,
  routeName: string,
): Promise<void> {
  const dimensions = await page.evaluate(() => ({
    bodyClientWidth: document.body.clientWidth,
    bodyScrollWidth: document.body.scrollWidth,
    htmlClientWidth: document.documentElement.clientWidth,
    htmlScrollWidth: document.documentElement.scrollWidth,
  }));

  expect(
    dimensions.htmlScrollWidth,
    `${routeName}: documentElement excedeu o viewport`,
  ).toBeLessThanOrEqual(dimensions.htmlClientWidth);
  expect(
    dimensions.bodyScrollWidth,
    `${routeName}: body excedeu o viewport`,
  ).toBeLessThanOrEqual(dimensions.bodyClientWidth);
}

export async function expectLocalHorizontalScroll(
  container: Locator,
): Promise<void> {
  await expect(container).toBeVisible();
  const state = await container.evaluate((element) => ({
    clientWidth: element.clientWidth,
    overflowX: getComputedStyle(element).overflowX,
    scrollWidth: element.scrollWidth,
  }));
  expect(["auto", "scroll"]).toContain(state.overflowX);
  expect(state.scrollWidth).toBeGreaterThanOrEqual(state.clientWidth);
}

export async function tabTo(
  page: Page,
  target: Locator,
  maximumTabs = 40,
): Promise<void> {
  for (let index = 0; index < maximumTabs; index += 1) {
    await page.keyboard.press("Tab");
    if (
      await target.evaluate((element) => element === document.activeElement)
    ) {
      return;
    }
  }
  throw new Error(`O controle não recebeu foco após ${maximumTabs} Tabs.`);
}
