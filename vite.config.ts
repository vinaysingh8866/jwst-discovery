import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
import fs from "fs";

const host = process.env.TAURI_DEV_HOST;

/**
 * Playwright mode (`vite --mode e2e`): serve the page under the exact
 * Content-Security-Policy the packaged app enforces (`app.security.csp` in
 * src-tauri/tauri.conf.json), so anything the webview would block is also
 * blocked, and visible as a console violation, in headless Chromium.
 *
 * The only difference from the packaged CSP is a nonce on `script-src`: Vite's
 * dev tooling injects inline scripts (the react-refresh preamble) that the
 * production bundle does not have. `html.cspNonce` makes Vite stamp that nonce
 * on the tags it injects.
 */
const E2E_NONCE = "jwst-e2e-nonce";

function e2eCspPlugin(): Plugin {
  const confPath = path.resolve(__dirname, "src-tauri/tauri.conf.json");
  const conf = JSON.parse(fs.readFileSync(confPath, "utf8")) as { app: { security: { csp: string } } };
  const csp = conf.app.security.csp.replace(
    /script-src ([^;]*)/,
    (_m, value: string) => `script-src ${value} 'nonce-${E2E_NONCE}'`,
  );
  return {
    name: "jwst-e2e-csp",
    transformIndexHtml() {
      return [
        {
          tag: "meta",
          attrs: { "http-equiv": "Content-Security-Policy", content: csp },
          injectTo: "head-prepend",
        },
      ];
    },
  };
}

export default defineConfig(async ({ mode }) => {
  const e2e = mode === "e2e";
  return {
    plugins: [react(), tailwindcss(), ...(e2e ? [e2eCspPlugin()] : [])],
    html: e2e ? { cspNonce: E2E_NONCE } : undefined,
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    clearScreen: false,
    server: {
      port: 5179,
      strictPort: true,
      host: host || false,
      hmr: host
        ? {
            protocol: "ws",
            host,
            port: 5180,
          }
        : undefined,
      watch: {
        ignored: ["**/src-tauri/**"],
      },
    },
  };
});
