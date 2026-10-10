import { test, expect } from "@playwright/test";
import { docScreenshot, loadFixture, openApp, FIELD_PRESET } from "./helpers";

const knownFields = loadFixture<{ field: string }[]>("known_fields.json");

test("config page offers the known-field presets and applies one", async ({ page }) => {
  await openApp(page, "/config");
  await expect(page.getByRole("heading", { name: "Pipeline Configuration" })).toBeVisible();

  // One preset button per field the sidecar registry knows about.
  const presets = page.locator("section", { hasText: "Known Field Presets" }).getByRole("button");
  await expect(presets).toHaveCount(knownFields.length);

  await expect(page.getByLabel("Field Name")).toHaveValue("JADES-GOODS-S");
  await page.getByRole("button", { name: FIELD_PRESET }).click();
  await expect(page.getByLabel("Field Name")).toHaveValue("SMACS-0723");
  await expect(page.getByLabel("Program ID")).toHaveValue("2736");
  await expect(page.getByLabel("Center RA (deg)")).toHaveValue("110.838");
  await expect(page.getByLabel("Center Dec (deg)")).toHaveValue("-73.453");
  await expect(page.getByText("Unsaved changes")).toBeVisible();

  await docScreenshot(page, "02-config");
});
