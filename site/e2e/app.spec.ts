import { expect, test } from "@playwright/test";

const EMPTY_STYLE = {
  version: 8,
  sources: {},
  layers: [{ id: "background", type: "background", paint: { "background-color": "#e5e7eb" } }],
};

test.beforeEach(async ({ page }) => {
  await page.route("https://tiles.openfreemap.org/styles/liberty", (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify(EMPTY_STYLE) }));
});

test("loads the map without errors and avoids sort-only reclustering", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  page.on("pageerror", (error) => errors.push(error.message));

  await page.goto("/?demo=1");
  await expect(page.locator("#demo-badge")).toBeVisible();
  await expect(page.locator(".card")).toHaveCount(3);
  await expect(page.locator('#map[data-status="ready"]')).toHaveAttribute("data-location-count", "3");
  await expect.poll(() => page.evaluate(() => {
    const map = (window as unknown as { __heimspielMap: { queryRenderedFeatures: (options: object) => unknown[] } }).__heimspielMap;
    return map.queryRenderedFeatures({ layers: ["job-points"] }).length;
  })).toBeGreaterThan(0);

  const initialUpdates = Number(await page.locator("#map").getAttribute("data-source-updates"));
  await page.locator("#f-sort").selectOption("new");
  await expect(page).toHaveURL(/sort=new/);
  await page.waitForTimeout(100);
  expect(Number(await page.locator("#map").getAttribute("data-source-updates"))).toBe(initialUpdates);

  await page.locator("#f-color").selectOption("travel");
  await expect(page).toHaveURL(/color=travel/);
  await expect.poll(async () => Number(await page.locator("#map").getAttribute("data-source-updates"))).toBe(initialUpdates + 1);
  await page.locator('[data-job-id="1"]').click();
  await expect(page.locator("#drawer")).toBeVisible();
  await expect(page.locator("#drawer-content")).toContainText("NGS-Auswertungspipelines");
  expect(errors).toEqual([]);
});

test("groups stacked jobs and keeps the responsive layout usable", async ({ page }) => {
  await page.goto("/?demo=1");
  await expect(page.locator('#map[data-status="ready"]')).toBeVisible();
  await page.locator("#f-segment").selectOption("alle");
  await expect(page.locator(".card")).toHaveCount(4);
  await expect(page.locator("#map")).toHaveAttribute("data-location-count", "3");
  await expect(page.locator("#list")).toBeVisible();
});
