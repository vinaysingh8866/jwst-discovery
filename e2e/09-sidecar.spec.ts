import { test, expect } from "@playwright/test";
import { docScreenshot, expectSidecar, openApp } from "./helpers";

test("top bar shows the sidecar as ready once the host answers", async ({ page }) => {
  await openApp(page, "/");
  await expectSidecar(page, "ready");
  await expect(page.getByTestId("sidecar-status")).toHaveAttribute("title", "e2e mock");
});

test("data page shows the error banner when the sidecar is unavailable", async ({ page }) => {
  await openApp(page, "/data", { sidecar: "unavailable" });
  await expectSidecar(page, "unavailable");

  const notice = page.getByTestId("sidecar-notice");
  await expect(notice).toHaveAttribute("role", "alert");
  await expect(notice).toContainText("Pipeline process is not running");
  await expect(notice).toContainText("Bundled pipeline sidecar not found");

  const search = page.getByRole("button", { name: /Waiting for pipeline process/ });
  await expect(search).toBeDisabled();

  await docScreenshot(page, "09-sidecar-unavailable");
});

test("a failed MAST query is reported under the archive banner", async ({ page }) => {
  await openApp(page, "/data", { mastFails: true });
  const banner = page.getByRole("alert").filter({ hasText: "Archive query failed" });
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("Read timed out");
  await expect(page.getByRole("button", { name: "Search MAST Archive" })).toBeEnabled();
});
