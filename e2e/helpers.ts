import { expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const SCREENSHOT_DIR = "docs/screenshots";

/** Read one of the JSON fixtures the Tauri mock serves (src/e2e/fixtures/). */
export function loadFixture<T>(relativePath: string): T {
  const url = new URL(`../src/e2e/fixtures/${relativePath}`, import.meta.url);
  return JSON.parse(readFileSync(fileURLToPath(url), "utf8")) as T;
}

/** The field preset the fixture workspace belongs to (Config page button). */
export const FIELD_PRESET = /SMACS J0723\.3-7327/;
export const FIELD_NAME = "SMACS-0723";

/** Console lines that mean the CSP (or something else) blocked a resource. */
export function isCspViolation(text: string): boolean {
  return /Content Security Policy|Refused to (connect|load|execute|compile|apply|create)/i.test(text);
}

/** Collect console errors/warnings for later assertions. */
export function watchConsole(page: Page): { errors: string[]; cspViolations: string[] } {
  const errors: string[] = [];
  const cspViolations: string[] = [];
  page.on("console", (msg) => {
    const text = msg.text();
    if (isCspViolation(text)) cspViolations.push(text);
    if (msg.type() === "error") errors.push(text);
  });
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  return { errors, cspViolations };
}

/**
 * Open the app at `path`. `scenario` is handed to the Tauri mock before any
 * script runs; the mock sets `__E2E_MOCK_READY__` once `mockIPC` is installed.
 */
export async function openApp(page: Page, path = "/", scenario?: E2EScenario): Promise<void> {
  if (scenario) {
    await page.addInitScript((s: E2EScenario) => {
      window.__E2E_SCENARIO__ = s;
    }, scenario);
  }
  await page.goto(path);
  await page.waitForFunction(() => window.__E2E_MOCK_READY__ === true);
  await expect(page.getByText("JWST Discovery", { exact: true })).toBeVisible();
}

/** Click a sidebar entry (client-side navigation keeps the stores alive). */
export async function navigateTo(page: Page, label: "Data" | "Pipeline" | "Viewer" | "Catalog" | "Anomalies" | "Compare" | "Config"): Promise<void> {
  await page.locator("aside").getByRole("link", { name: label, exact: true }).click();
}

/**
 * Apply the SMACS-0723 preset on the Config page, the way a user points the
 * app at a field. Afterwards the workspace resolves to the fixture directory
 * `<root>/SMACS-0723`, whose report the mock serves.
 */
export async function selectFixtureField(page: Page): Promise<void> {
  await navigateTo(page, "Config");
  await page.getByRole("button", { name: FIELD_PRESET }).click();
  await expect(page.getByLabel("Field Name")).toHaveValue(FIELD_NAME);
}

/** Wait for the sidecar status pill in the top bar to read `status`. */
export async function expectSidecar(page: Page, status: "ready" | "starting" | "unavailable"): Promise<void> {
  await expect(page.getByTestId("sidecar-status")).toHaveText(new RegExp(`Sidecar: ${status}`));
}

/** Deterministic full-page screenshot for the docs. */
export async function docScreenshot(page: Page, name: string): Promise<void> {
  await page.screenshot({ path: `${SCREENSHOT_DIR}/${name}.png`, animations: "disabled", caret: "hide" });
}
