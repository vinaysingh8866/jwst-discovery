/**
 * In-browser stand-in for the Tauri host, used by the Playwright suite.
 *
 * Loaded by `src/main.tsx` before the first render when `VITE_E2E=1`
 * (`vite --mode e2e`, see `.env.e2e`). It installs `mockIPC` from
 * `@tauri-apps/api/mocks`, so every `invoke()` the UI makes, including the
 * ones `@tauri-apps/api` itself issues (`plugin:event|listen`,
 * `plugin:path|resolve_directory`, ...), lands in `handleCommand` below.
 *
 * Events: `listen()` sends `plugin:event|listen { event, target, handler }`
 * where `handler` is the id `transformCallback` registered. We keep those ids
 * per event name and deliver payloads later through
 * `window.__TAURI_INTERNALS__.runCallback(id, { event, id, payload })`,
 * exactly the shape the real host uses. Long-running commands (MAST query,
 * pipeline) return their request id immediately and emit their events on a
 * timer, like the Rust sidecar manager does.
 *
 * A test can tune the scenario before navigation with
 * `page.addInitScript(() => { window.__E2E_SCENARIO__ = { sidecar: "unavailable" } })`.
 */
import { mockIPC, mockWindows } from "@tauri-apps/api/mocks";
import candidatesReport from "./fixtures/SMACS-0723/report/candidates.json";
import sourcesSummary from "./fixtures/SMACS-0723/report/sources_summary.json";
import mastQuery from "./fixtures/mast_query.json";
import knownFields from "./fixtures/known_fields.json";
import cutoutPngDataUrl from "./fixtures/cutout.png?inline";

export interface E2EScenario {
  /** Health reported by `sidecar_status`. Default "ready". */
  sidecar?: "ready" | "starting" | "unavailable";
  /** Make `query_mast` fail with a `sidecar:error` instead of a result. */
  mastFails?: boolean;
  /** Milliseconds the pipeline mock holds the `deblend` stage at 45 %. */
  pipelineHoldMs?: number;
}

declare global {
  interface Window {
    __E2E_SCENARIO__?: E2EScenario;
    /** Set once the mock is installed; tests wait for it. */
    __E2E_MOCK_READY__?: boolean;
  }
}

interface TauriInternals {
  runCallback: (id: number, data: unknown) => void;
}

const HOME = "/e2e/home";
export const WORKSPACE_ROOT = `${HOME}/.jwst-discovery/workspaces`;
export const WORKSPACE_NAME = "SMACS-0723";
const WORKSPACE = `${WORKSPACE_ROOT}/${WORKSPACE_NAME}`;

const scenario: E2EScenario = { sidecar: "ready", mastFails: false, pipelineHoldMs: 2500, ...window.__E2E_SCENARIO__ };

const cutoutBase64 = cutoutPngDataUrl.replace(/^data:image\/png;base64,/, "");

/** Files the host would read from disk, keyed by absolute path. */
const virtualFiles: Record<string, string> = {
  [`${WORKSPACE}/report/candidates.json`]: JSON.stringify(candidatesReport),
  [`${WORKSPACE}/report/sources_summary.json`]: JSON.stringify(sourcesSummary),
};

function ioError(path: string) {
  // Same externally tagged shape the Rust `AppError::Io` serialises to.
  return { Io: `${path}: No such file or directory (os error 2)` };
}

function sidecarError(message: string) {
  return { Sidecar: message };
}

// ---------------------------------------------------------------------------
// Event plumbing
// ---------------------------------------------------------------------------
const listeners = new Map<string, Set<number>>();
let eventCounter = 0;

function internals(): TauriInternals {
  return (window as unknown as { __TAURI_INTERNALS__: TauriInternals }).__TAURI_INTERNALS__;
}

export function emit(event: string, payload: unknown): void {
  const ids = listeners.get(event);
  if (!ids || ids.size === 0) return;
  const id = ++eventCounter;
  for (const handlerId of Array.from(ids)) {
    internals().runCallback(handlerId, { event, id, payload });
  }
}

const timers = new Set<ReturnType<typeof setTimeout>>();
function later(ms: number, fn: () => void): void {
  const t = setTimeout(() => {
    timers.delete(t);
    fn();
  }, ms);
  timers.add(t);
}
function cancelTimers(): void {
  for (const t of timers) clearTimeout(t);
  timers.clear();
}

let requestCounter = 0;
function newRequestId(): string {
  requestCounter += 1;
  return `e2e-req-${requestCounter.toString().padStart(4, "0")}`;
}

// ---------------------------------------------------------------------------
// Sidecar behaviour
// ---------------------------------------------------------------------------
function sidecarHealth() {
  switch (scenario.sidecar) {
    case "unavailable":
      return {
        alive: false,
        ready: false,
        launch: null,
        message: "Bundled pipeline sidecar not found at /Applications/JWST Discovery.app/Contents/Resources/sidecar/jwst-pipeline/jwst-pipeline (e2e scenario)",
      };
    case "starting":
      return { alive: true, ready: false, launch: "e2e mock", message: null };
    default:
      return { alive: true, ready: true, launch: "e2e mock", message: null };
  }
}

const STAGES = ["ingest", "preprocess", "detect", "deblend", "measure", "characterize", "crossmatch", "anomaly", "report"];
const STAGE_MESSAGES: Record<string, string> = {
  ingest: "6 filters already on disk",
  preprocess: "Reprojecting F444W to the detection grid",
  detect: "SEP extraction: 18,437 sources",
  deblend: "Deblending 1,204 blended groups",
  measure: "Aperture photometry in 6 bands",
  characterize: "EAZY photo-z for 18,437 sources",
  crossmatch: "Querying SIMBAD (cone 4.2')",
  anomaly: "Isolation Forest, 22 features",
  report: "Writing cutouts and SEDs for 12 candidates",
};

function stageUpdate(stage: string, status: "running" | "completed" | "failed", progress: number, message: string, result?: unknown) {
  emit("pipeline:stage-update", { stageName: stage, status, progress, message, ...(result !== undefined ? { result } : {}) });
}

function streamStage(stage: string, startMs: number, durationMs: number): void {
  const holdHere = stage === "deblend";
  later(startMs, () => {
    emit("pipeline:log", { level: "info", message: `jwst_pipeline: ${stage} started` });
    stageUpdate(stage, "running", 0.1, STAGE_MESSAGES[stage]);
  });
  later(startMs + durationMs * 0.5, () => stageUpdate(stage, "running", 0.45, STAGE_MESSAGES[stage]));
  later(startMs + durationMs + (holdHere ? scenario.pipelineHoldMs ?? 0 : 0), () => {
    stageUpdate(stage, "completed", 1, `Completed in ${(durationMs / 100).toFixed(1)}s`, { stage });
    emit("pipeline:log", { level: "info", message: `jwst_pipeline: ${stage} completed` });
  });
}

function runFullPipeline(): void {
  cancelTimers();
  const perStage = 220;
  let t = 50;
  for (const stage of STAGES) {
    streamStage(stage, t, perStage);
    t += perStage + (stage === "deblend" ? scenario.pipelineHoldMs ?? 0 : 0) + 20;
  }
  later(t + 50, () => emit("pipeline:complete", { stages_completed: STAGES.length }));
}

function runSingleStage(stage: string): void {
  streamStage(stage, 50, 400);
}

function emitMastResult(targetField: string): void {
  if (scenario.mastFails) {
    later(300, () => {
      emit("sidecar:error", { requestId: lastMastRequestId, message: "MAST query failed: HTTPSConnectionPool(host='mast.stsci.edu', port=443): Read timed out (e2e scenario)" });
    });
    return;
  }
  later(300, () => emit("mast:query-result", { ...mastQuery, target: targetField }));
}
let lastMastRequestId = "";

const DOWNLOADED_KEYS = ["2736:F090W", "2736:F150W", "2736:F200W", "2736:F277W", "2736:F356W", "2736:F444W"];
const DOWNLOADED_SIZES = [1128.1, 1128.1, 1128.1, 282.5, 282.5, 282.5];

function emitDownloadManifest(workspace: string): void {
  const downloads: Record<string, { size_mb: number; downloaded_at: string | null; exists: boolean }> = {};
  const isFixtureWorkspace = workspace === WORKSPACE;
  DOWNLOADED_KEYS.forEach((key, i) => {
    downloads[key] = { size_mb: DOWNLOADED_SIZES[i], downloaded_at: "2026-10-01T09:12:00Z", exists: isFixtureWorkspace };
  });
  later(60, () => emit("download:manifest", { downloads, workspace }));
}

function emitRefCatalogStatus(field: string): void {
  const known = (knownFields as Array<{ field: string; catalogs: { name: string; label: string }[]; mosaic_filters: string[] }>).find((f) => f.field === field);
  const catalogs = (known?.catalogs ?? []).map((c, i) => ({
    name: c.name,
    label: c.label,
    status: i % 3 === 2 ? "missing" : "downloaded",
    rows: i % 3 === 2 ? 0 : 4000 + i * 1217,
    size_kb: i % 3 === 2 ? 0 : 900 + i * 310,
  }));
  const mosaics = (known?.mosaic_filters ?? []).map((filter, i) => ({
    filter,
    status: i < 2 ? "downloaded" : "missing",
    size_mb: i < 2 ? 1128.1 : 0,
  }));
  later(80, () => emit("catalog:ref-status", { field, catalogs, mosaics }));
}

// ---------------------------------------------------------------------------
// Command table
// ---------------------------------------------------------------------------
type Args = Record<string, unknown> | undefined;

function handleCommand(cmd: string, args: Args): unknown {
  const a = args ?? {};
  switch (cmd) {
    // --- host plumbing ---------------------------------------------------
    case "plugin:event|listen": {
      const event = String(a.event);
      const handler = Number(a.handler);
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event)!.add(handler);
      return handler; // eventId
    }
    case "plugin:event|unlisten": {
      listeners.get(String(a.event))?.delete(Number(a.eventId));
      return null;
    }
    case "plugin:event|emit":
      emit(String(a.event), a.payload);
      return null;
    case "plugin:path|resolve_directory":
      return HOME;
    case "plugin:shell|open":
      return null;

    // --- filesystem --------------------------------------------------------
    case "get_workspace_root":
      return WORKSPACE_ROOT;
    case "list_workspaces":
      return [{ name: WORKSPACE_NAME, path: WORKSPACE, has_report: true, has_raw: true, n_fits: 6 }];
    case "list_directory": {
      const path = String(a.path).replace(/\/+$/, "");
      if (path === WORKSPACE_ROOT) return [WORKSPACE_NAME];
      if (path === WORKSPACE) return ["raw", "catalogs", "report"];
      throw ioError(path);
    }
    case "read_text_file": {
      const path = String(a.path);
      if (path in virtualFiles) return virtualFiles[path];
      throw ioError(path);
    }
    case "read_binary_file_base64": {
      const path = String(a.path);
      if (path.startsWith(`${WORKSPACE}/report/`) && path.endsWith(".png")) return cutoutBase64;
      throw ioError(path);
    }

    // --- sidecar -----------------------------------------------------------
    case "sidecar_status":
      return sidecarHealth();
    case "get_pipeline_status":
      return { running: false, current_stage: null, progress: 0, workspace: null };
    case "list_known_fields": {
      if (scenario.sidecar !== "ready") throw sidecarError("Sidecar not running");
      later(50, () => emit("field:known-fields", knownFields));
      return newRequestId();
    }
    case "query_mast": {
      if (scenario.sidecar !== "ready") throw sidecarError("Sidecar not running");
      const config = a.config as { target_field?: string } | undefined;
      lastMastRequestId = newRequestId();
      emitMastResult(config?.target_field ?? "unknown");
      return lastMastRequestId;
    }
    case "query_mast_missions": {
      if (scenario.sidecar !== "ready") throw sidecarError("Sidecar not running");
      later(300, () =>
        emit("mast:missions-result", {
          totalObservations: 41,
          programs: [
            { program_id: "2736", pi_name: "Pontoppidan, Klaus", observations: 18, instruments: ["NIRCAM", "MIRI", "NIRSPEC", "NIRISS"], filters: ["F090W", "F150W", "F200W", "F277W", "F356W", "F444W"], total_exposure_hrs: 12.4 },
            { program_id: "4446", pi_name: "E2E Fixture", observations: 15, instruments: ["NIRCAM"], filters: ["F115W", "F200W", "F356W", "F444W"], total_exposure_hrs: 6.1 },
            { program_id: "1176", pi_name: "E2E Fixture", observations: 8, instruments: ["NIRISS", "NIRCAM"], filters: ["F115W", "F150W", "F200W"], total_exposure_hrs: 3.2 },
          ],
        }),
      );
      return newRequestId();
    }
    case "check_downloads":
      emitDownloadManifest(String(a.workspace));
      return newRequestId();
    case "check_reference_catalogs":
      emitRefCatalogStatus(String(a.field));
      return newRequestId();
    case "download_reference_catalogs": {
      const field = String(a.field);
      later(150, () => stageUpdate("ref_catalogs", "running", 0.5, "Downloading reference catalogs"));
      later(600, () => stageUpdate("ref_catalogs", "completed", 1, "Completed in 0.5s"));
      later(650, () => emitRefCatalogStatus(field));
      return newRequestId();
    }
    case "download_hlsp_mosaics": {
      const field = String(a.field);
      later(400, () => stageUpdate("hlsp_mosaics", "completed", 1, "Completed in 0.4s"));
      later(450, () => emitRefCatalogStatus(field));
      return newRequestId();
    }
    case "start_pipeline":
      if (scenario.sidecar !== "ready") throw sidecarError("Sidecar not running");
      runFullPipeline();
      return newRequestId();
    case "run_stage":
      if (scenario.sidecar !== "ready") throw sidecarError("Sidecar not running");
      runSingleStage(String(a.stage));
      return newRequestId();
    case "cancel_pipeline":
      cancelTimers();
      return null;

    default:
      console.warn(`[e2e mock] unmocked Tauri command: ${cmd}`, args);
      throw sidecarError(`e2e mock: unmocked command ${cmd}`);
  }
}

mockWindows("main");
mockIPC((cmd, args) => handleCommand(cmd, args as Args));

// `sidecar:health` is normally pushed by the host once the process answers.
later(200, () => emit("sidecar:health", sidecarHealth()));

window.__E2E_MOCK_READY__ = true;
