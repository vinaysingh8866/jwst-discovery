import { test, expect } from "@playwright/test";
import { docScreenshot, loadFixture, navigateTo, openApp, selectFixtureField } from "./helpers";

interface Summary { n_sources: number; filters: string[]; sources: { novel?: boolean | null }[] }
const summary = loadFixture<Summary>("SMACS-0723/report/sources_summary.json");

test("catalog page tabulates every source of the workspace", async ({ page }) => {
  await openApp(page, "/");
  await selectFixtureField(page);
  await navigateTo(page, "Catalog");

  await expect(page.getByRole("heading", { name: "Source Catalog" })).toBeVisible();
  await expect(page.getByText(`${summary.n_sources} of ${summary.n_sources} sources across 6 filters`)).toBeVisible();

  const rows = page.getByTestId("catalog-row");
  await expect(rows).toHaveCount(summary.n_sources);
  // Default sort is by anomaly rank: the first row is rank 1 / id 4821.
  await expect(rows.first()).toContainText("4821");
  // Per-filter magnitude columns exist.
  for (const filt of summary.filters) {
    await expect(page.getByText(filt, { exact: true }).first()).toBeVisible();
  }

  const nNovel = summary.sources.filter((s) => s.novel === true).length;
  await page.getByLabel("Novel only").check();
  await expect(rows).toHaveCount(nNovel);
  await expect(page.getByText(`${nNovel} of ${summary.n_sources} sources`)).toBeVisible();
  await page.getByLabel("Novel only").uncheck();

  await page.getByPlaceholder("Search by ID...").fill("4821");
  await expect(rows).toHaveCount(1);
  await page.getByPlaceholder("Search by ID...").fill("");
  await expect(rows).toHaveCount(summary.n_sources);

  await docScreenshot(page, "05-catalog");
});
