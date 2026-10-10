/** Globals the in-page Tauri mock (src/e2e/mockTauri.ts) exposes to the tests. */
interface E2EScenario {
  sidecar?: "ready" | "starting" | "unavailable";
  mastFails?: boolean;
  pipelineHoldMs?: number;
}

interface Window {
  __E2E_SCENARIO__?: E2EScenario;
  __E2E_MOCK_READY__?: boolean;
}
