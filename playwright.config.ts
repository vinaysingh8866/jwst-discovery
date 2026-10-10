import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests: the real React front end in headless Chromium with the
 * Tauri host replaced by `src/e2e/mockTauri.ts` (enabled by `--mode e2e`).
 * Each spec also writes the screenshot embedded in docs/USAGE.md.
 */
export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  workers: process.env.CI ? 2 : undefined,
  retries: 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  outputDir: "test-results",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: "http://localhost:5180",
    viewport: { width: 1400, height: 900 },
    deviceScaleFactor: 1,
    colorScheme: "dark",
    trace: "retain-on-failure",
    launchOptions: {
      // Aladin Lite needs WebGL2; headless Chromium provides it through SwiftShader.
      args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
    },
  },
  projects: [{ name: "chromium" }],
  webServer: {
    command: "npx vite --mode e2e --port 5180",
    url: "http://localhost:5180",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
