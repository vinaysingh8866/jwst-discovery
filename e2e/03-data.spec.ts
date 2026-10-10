import { test, expect } from "@playwright/test";
import { docScreenshot, expectSidecar, navigateTo, openApp, selectFixtureField } from "./helpers";

test("data page queries MAST once the sidecar is ready and lists the programs", async ({ page }) => {
  await openApp(page, "/");
  await expectSidecar(page, "ready");
  await selectFixtureField(page);
  await navigateTo(page, "Data");

  await expect(page.getByRole("heading", { name: "JWST Data Browser" })).toBeVisible();
  await expect(page.getByTestId("sidecar-notice")).toHaveCount(0);

  // Auto-query result from the mock (300 ms later).
  await expect(page.getByText("41 observations across 3 programs")).toBeVisible();
  for (const pid of ["2736", "4446", "1176"]) {
    await expect(page.getByText(`PID ${pid}`, { exact: true })).toBeVisible();
  }
  // First program is expanded: its six filters are selectable.
  const first = page.locator("div.rounded-lg", { hasText: "PID 2736" }).first();
  await expect(first.getByRole("checkbox")).toHaveCount(6);
  await expect(page.getByText("6 filters already downloaded")).toBeVisible();

  // Field setup panel recognised the preset and lists catalog status.
  const setup = page.locator("div.rounded-lg", { hasText: "Field Setup" }).first();
  await expect(setup).toContainText("SMACS-0723");
  await expect(setup).toContainText(/\d+\/\d+ catalogs/);

  // Select two filters and check the action bar counts them.
  await first.getByRole("checkbox").nth(0).check();
  await first.getByRole("checkbox").nth(1).check();
  await expect(page.getByText("2 filters selected from 1 program")).toBeVisible();

  await docScreenshot(page, "03-data");
});

test("MastMissions panel discovers programs on demand", async ({ page }) => {
  await openApp(page, "/data");
  await page.getByRole("button", { name: /JWST Observations \(MastMissions\)/ }).click();
  await page.getByRole("button", { name: "Discover Observations" }).click();
  await expect(page.getByText("41 obs, 3 programs")).toBeVisible();
  await expect(page.locator("table tbody tr")).toHaveCount(3);
  await expect(page.getByText("Pontoppidan, Klaus")).toBeVisible();
});
