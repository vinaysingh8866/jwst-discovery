import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/globals.css";

async function bootstrap() {
  // Playwright runs the UI in plain Chromium: install the Tauri IPC mock first
  // so the very first invoke()/listen() calls already have a host to talk to.
  // `VITE_E2E` is only set by `vite --mode e2e` (.env.e2e); in every other
  // build the condition is a constant and the import is dropped.
  if (import.meta.env.VITE_E2E === "1") {
    await import("./e2e/mockTauri");
  }

  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}

void bootstrap();
