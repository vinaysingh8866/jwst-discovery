import { test, expect } from "@playwright/test";
import { docScreenshot, navigateTo, openApp, selectFixtureField } from "./helpers";

test("running the full pipeline streams stage progress into the cards", async ({ page }) => {
  await openApp(page, "/");
  await selectFixtureField(page);
  await navigateTo(page, "Pipeline");
  await expect(page.getByText("6 filters ready")).toBeVisible();

  await page.getByRole("button", { name: "Run Full Pipeline" }).click();
  await expect(page.getByText("Pipeline: running")).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel" })).toBeEnabled();

  // The mock holds `deblend` at 45 % for a while: earlier stages are done, it is running.
  const deblend = page.locator("div.rounded-lg", { has: page.getByRole("heading", { name: "Deblending" }) });
  await expect(deblend).toContainText("running", { timeout: 15_000 });
  await expect(deblend).toContainText("Deblending 1,204 blended groups");
  await expect(deblend.locator("div.bg-blue-500.rounded-full")).toHaveAttribute("style", /width: 45%/);
  for (const label of ["Data Ingestion", "Preprocessing", "Source Detection"]) {
    const card = page.locator("div.rounded-lg", { has: page.getByRole("heading", { name: label, exact: true }) });
    await expect(card).toContainText("completed");
  }
  await expect(page.getByText(/jwst_pipeline: detect completed/)).toBeVisible();

  await docScreenshot(page, "04-pipeline");

  await expect(page.getByText("Pipeline: completed")).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("span.rounded-full", { hasText: /^completed$/ })).toHaveCount(9);
});

test("a single stage can be run on its own", async ({ page }) => {
  await openApp(page, "/");
  await selectFixtureField(page);
  await navigateTo(page, "Pipeline");
  await expect(page.getByText("6 filters ready")).toBeVisible();

  const preprocess = page.locator("div.rounded-lg", { has: page.getByRole("heading", { name: "Preprocessing" }) });
  await expect(preprocess).toContainText("Ready to run");
  await preprocess.getByRole("button", { name: "Run Next" }).click();
  await expect(preprocess).toContainText("running");
  await expect(preprocess).toContainText("completed", { timeout: 10_000 });
  await expect(preprocess).toContainText(/Completed in/);
});
