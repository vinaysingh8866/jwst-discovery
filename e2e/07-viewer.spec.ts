import { test, expect, type Page } from "@playwright/test";
import { decodePng, pixelStats } from "./png";
import { docScreenshot, navigateTo, openApp, selectFixtureField, watchConsole } from "./helpers";

// Survey tiles come from CDS over the network; allow two retries for flakiness.
test.describe.configure({ retries: 2 });

const TILE_WAIT_MS = 45_000;

/** Screenshot the Aladin container and summarise its pixels. */
async function containerStats(page: Page) {
  const container = page.getByTestId("aladin-container");
  const buf = await container.screenshot({ type: "png", animations: "disabled" });
  return pixelStats(decodePng(buf));
}

test("sky viewer renders survey tiles for the configured field", async ({ page }) => {
  const console_ = watchConsole(page);
  await openApp(page, "/");
  await selectFixtureField(page);
  await navigateTo(page, "Viewer");

  await expect(page.getByRole("heading", { name: "Sky Viewer" })).toBeVisible();
  await expect(page.getByText(/SMACS-0723 .* RA 110\.838, Dec -73\.453/)).toBeVisible();

  // Aladin Lite must come up (WASM + WebGL2) and must not fall back to the error panel.
  await expect(page.getByTestId("viewer-error")).toHaveCount(0);
  const container = page.getByTestId("aladin-container");
  await expect(container).toBeVisible();
  const box = await container.boundingBox();
  expect(box, "viewer container has a box").not.toBeNull();
  expect(box!.width).toBeGreaterThan(400);
  expect(box!.height).toBeGreaterThan(300);

  const canvas = container.locator("canvas").first();
  await expect(canvas).toBeVisible({ timeout: 30_000 });
  const canvasSize = await canvas.evaluate((el) => ({ w: (el as HTMLCanvasElement).width, h: (el as HTMLCanvasElement).height }));
  expect(canvasSize.w).toBeGreaterThan(0);
  expect(canvasSize.h).toBeGreaterThan(0);

  // The overlay select lists the fixture's 12 candidates.
  await expect(page.getByTestId("overlay-select")).toContainText("Anomalies (12)");

  // Wait for the HiPS tiles: the canvas stops being a flat colour.
  const deadline = Date.now() + TILE_WAIT_MS;
  let stats = await containerStats(page);
  while (Date.now() < deadline && !(stats.distinctColors > 400 && stats.luminanceStd > 6)) {
    await page.waitForTimeout(2_000);
    stats = await containerStats(page);
  }
  expect(stats.distinctColors, `distinct colours in the sky canvas (std ${stats.luminanceStd.toFixed(1)})`).toBeGreaterThan(400);
  expect(stats.luminanceStd).toBeGreaterThan(6);
  // Give the remaining tiles of the field a moment to arrive before the docs screenshot.
  await page.waitForTimeout(4_000);

  // Nothing Aladin needs may be blocked by the packaged app's CSP. Aladin also
  // probes third-party HiPS mirrors (IRSA, ESAC, CASDA, ...) with a GET of
  // `/properties`; those are deliberately not in the CSP and failing them just
  // makes it pick the CDS mirror, so only non-probe violations count.
  const blocking = console_.cspViolations.filter((line) => !/\/properties['"]?[.,]?\s|\/properties$/.test(line));
  expect(blocking, blocking.join("\n")).toEqual([]);

  await docScreenshot(page, "07-viewer");
});

test("sky viewer explains itself instead of showing a blank panel when Aladin cannot start", async ({ page }) => {
  // Deny WebGL2 before the app loads: Aladin's init rejects and the page must say so.
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, ...rest: unknown[]) {
      if (type === "webgl2") return null;
      return (original as (...a: unknown[]) => unknown).call(this, type, ...rest) as never;
    } as typeof HTMLCanvasElement.prototype.getContext;
  });
  await openApp(page, "/");
  await selectFixtureField(page);
  await navigateTo(page, "Viewer");

  const error = page.getByTestId("viewer-error");
  await expect(error).toBeVisible({ timeout: 30_000 });
  await expect(error).toContainText(/Sky viewer could not start/);
  await expect(error).toContainText(/WebGL2/i);
  // The candidate overlay still has somewhere to go: the scatter fallback.
  await expect(page.getByText(/Scatter plot fallback/)).toBeVisible();
});
