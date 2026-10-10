import { test, expect } from "@playwright/test";
import { docScreenshot, expectSidecar, navigateTo, openApp, selectFixtureField } from "./helpers";

const STAGE_LABELS = [
  "Data Ingestion", "Preprocessing", "Source Detection", "Deblending", "Photometry",
  "Characterization", "Cross-Match", "Anomaly Detection", "Report",
];

test("pipeline dashboard lists the nine stages and the data already on disk", async ({ page }) => {
  await openApp(page, "/");
  await expectSidecar(page, "ready");
  await expect(page.getByRole("heading", { name: "Pipeline Dashboard" })).toBeVisible();

  // Default field has nothing downloaded.
  await expect(page.getByText("No data downloaded yet. Select data to begin.")).toBeVisible();

  await selectFixtureField(page);
  await navigateTo(page, "Pipeline");

  for (const label of STAGE_LABELS) {
    await expect(page.getByRole("heading", { name: label, exact: true })).toBeVisible();
  }
  // The fixture workspace has six mosaics on disk, so ingest is already done.
  await expect(page.getByText("6 filters ready")).toBeVisible();
  await expect(page.getByText("6 filters on disk")).toBeVisible();
  await expect(page.getByText("Pipeline: idle")).toBeVisible();
  await expect(page.getByRole("button", { name: "Run Full Pipeline" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Cancel" })).toBeDisabled();

  await docScreenshot(page, "01-dashboard");
});
