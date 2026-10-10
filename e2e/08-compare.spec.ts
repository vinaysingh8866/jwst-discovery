import { test, expect } from "@playwright/test";
import { docScreenshot, navigateTo, openApp } from "./helpers";

test("compare page summarises every workspace that has a report", async ({ page }) => {
  await openApp(page, "/");
  await navigateTo(page, "Compare");

  await expect(page.getByRole("heading", { level: 2, name: "Cross-field Comparison" })).toBeVisible();
  await expect(page.getByText("1 fields, 18,437 total sources")).toBeVisible();

  const overview = page.locator("section", { hasText: "Field Overview" });
  const row = overview.locator("tbody tr", { hasText: "SMACS-0723" });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("18,437");
  await expect(row).toContainText("12"); // anomalies
  // Gold / silver / bronze tiers from confidence_tier.
  await expect(row.locator("td").nth(5)).toHaveText("3");
  await expect(row.locator("td").nth(6)).toHaveText("5");
  await expect(row.locator("td").nth(7)).toHaveText("4");

  // Plotly charts rendered.
  await expect(page.locator(".js-plotly-plot").first()).toBeVisible();
  await expect(page.getByText("X-ray AGN").first()).toBeVisible();

  await docScreenshot(page, "08-compare");
});
