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
  await expect(page.locator(".card")).toHaveCount(2);
  await expect(page.locator(".card-bewerben")).toHaveCount(1);
  await expect(page.locator(".card .chip-position")).toContainText("PhD");
  await expect(page.locator('#map[data-status="ready"]')).toHaveAttribute("data-location-count", "2");
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
  await expect(page.locator("#drawer-content .req-matrix tr")).toHaveCount(5);
  await expect(page.locator("#drawer-content .panel")).toContainText("Empfehlung: Bewerben");
  expect(errors).toEqual([]);
});

test("detail drawer leaves the list usable and supports arrow-key paging", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "drawer is a full-screen overlay on mobile");
  await page.goto("/?demo=1");
  await expect(page.locator('#map[data-status="ready"]')).toBeVisible();
  await page.locator('[data-segment="alle"]').click();
  await expect(page.locator(".card")).toHaveCount(4);

  await page.locator('[data-job-id="1"]').click();
  await expect(page.locator("#drawer.open")).toBeVisible();
  await expect(page.locator('[data-job-id="1"]')).toHaveClass(/selected/);

  // The list stays visible and clickable while the drawer is open.
  await expect(page.locator("#list")).toBeVisible();
  // Default-Sortierung: Empfehlung vor Score → 1 (bewerben), 3 (stretch), 2, 4.
  await page.locator('[data-job-id="3"]').click();
  await expect(page.locator("#drawer.open")).toBeVisible();
  await expect(page.locator('[data-job-id="3"]')).toHaveClass(/selected/);
  await expect(page.locator('[data-job-id="1"]')).not.toHaveClass(/selected/);

  // Arrow keys page through the list in place.
  await page.keyboard.press("ArrowUp");
  await expect(page.locator('[data-job-id="1"]')).toHaveClass(/selected/);

  await page.locator("#drawer-close").click();
  await expect(page.locator("#drawer.open")).toHaveCount(0);
  await expect(page.locator(".card.selected")).toHaveCount(0);
});

test("PhD filter narrows to doctoral positions", async ({ page }) => {
  await page.goto("/?demo=1");
  await page.locator('[data-position="phd"]').click();
  await expect(page).toHaveURL(/pos=phd/);
  await expect(page.locator(".card")).toHaveCount(1);
  await expect(page.locator(".card")).toContainText("PhD Position");
  await expect(page.locator(".card .chip-stub")).toBeVisible();
});

test("groups stacked jobs and keeps the responsive layout usable", async ({ page }) => {
  await page.goto("/?demo=1");
  await expect(page.locator('#map[data-status="ready"]')).toBeVisible();
  await page.locator('[data-segment="alle"]').click();
  await expect(page.locator(".card")).toHaveCount(4);
  await expect(page.locator("#map")).toHaveAttribute("data-location-count", "3");
  await expect(page.locator("#list")).toBeVisible();
});
