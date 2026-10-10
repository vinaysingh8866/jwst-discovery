import { create } from "zustand";
import type { StageName, StageState, StageStatus, LogEntry, LogLevel } from "@/types/pipeline";
import { STAGE_NAMES } from "@/lib/constants";

export type OverallStatus = "idle" | "running" | "completed" | "failed";

/** Payload of the `pipeline:stage-update` Tauri event emitted by the sidecar manager. */
export interface StageUpdateEvent {
  /** May be `null` when the sidecar reports an error outside any stage. */
  stageName: string | null;
  status: StageStatus;
  progress: number;
  message: string;
  result?: Record<string, unknown>;
}

/** Payload of the `sidecar:health` Tauri event and of the `sidecar_status` command. */
export interface SidecarHealth {
  alive: boolean;
  ready: boolean;
  launch?: string | null;
  message?: string | null;
}

export type SidecarStatus = "starting" | "ready" | "unavailable";

/** Collapse a health payload into the three states the UI shows. */
export function sidecarStatusOf(h: SidecarHealth): SidecarStatus {
  if (h.ready) return "ready";
  if (h.alive) return "starting";
  return "unavailable";
}

/** Payload of the `pipeline:log` Tauri event. */
export interface LogEvent {
  level: string;
  message: string;
}

interface PipelineStore {
  stages: Record<StageName, StageState>;
  overallStatus: OverallStatus;
  /** Sidecar lifecycle; survives `reset()` because it is not per-run state. */
  sidecar: SidecarHealth;
  logs: LogEntry[];
  startTime: string | null;

  updateStage: (name: StageName, update: Partial<StageState>) => void;
  appendLog: (entry: LogEntry) => void;
  clearLogs: () => void;
  setOverallStatus: (status: OverallStatus) => void;
  reset: () => void;

  /** Apply a `pipeline:stage-update` event (Progress / StageComplete / Error). */
  applyStageUpdate: (event: StageUpdateEvent) => void;
  /** Apply a `pipeline:log` event. */
  applyLog: (event: LogEvent, timestamp?: string) => void;
  /** Apply a `pipeline:complete` event. */
  applyPipelineComplete: () => void;
  /** Apply a `sidecar:health` event or `sidecar_status` result. */
  applySidecarHealth: (health: SidecarHealth) => void;
}

const MAX_LOG_ENTRIES = 5000;

export function isStageName(name: unknown): name is StageName {
  return typeof name === "string" && (STAGE_NAMES as string[]).includes(name);
}

/** Map an arbitrary sidecar log level string onto the UI's `LogLevel`. */
export function normalizeLogLevel(level: string): LogLevel {
  switch (level.toLowerCase()) {
    case "debug":
      return "debug";
    case "warn":
    case "warning":
      return "warn";
    case "error":
    case "critical":
      return "error";
    default:
      return "info";
  }
}

function createInitialStages(): Record<StageName, StageState> {
  const stages = {} as Record<StageName, StageState>;
  for (const name of STAGE_NAMES) {
    stages[name] = {
      name,
      status: "idle",
      progress: 0,
      message: "",
    };
  }
  return stages;
}

export const usePipelineStore = create<PipelineStore>((set) => ({
  stages: createInitialStages(),
  overallStatus: "idle",
  sidecar: { alive: false, ready: false },
  logs: [],
  startTime: null,

  updateStage: (name, update) =>
    set((state) => ({
      stages: {
        ...state.stages,
        [name]: { ...state.stages[name], ...update },
      },
    })),

  appendLog: (entry) =>
    set((state) => ({
      logs: [...state.logs.slice(-(MAX_LOG_ENTRIES - 1)), entry],
    })),

  clearLogs: () => set({ logs: [] }),

  setOverallStatus: (status) => set({ overallStatus: status }),

  reset: () =>
    set({
      stages: createInitialStages(),
      overallStatus: "idle",
      logs: [],
      startTime: null,
    }),

  applyStageUpdate: (event) =>
    set((state) => {
      const { stageName, status, progress, message, result } = event;

      let stages = state.stages;
      if (isStageName(stageName)) {
        stages = {
          ...state.stages,
          [stageName]: {
            ...state.stages[stageName],
            status,
            progress,
            message,
            ...(result !== undefined ? { result } : {}),
          },
        };
      }

      let overallStatus = state.overallStatus;
      if (status === "failed") {
        overallStatus = "failed";
      } else if (status === "completed") {
        // When a stage completes and nothing else is still running, drop back
        // to idle so the user can trigger the next stage.
        const anyRunning = Object.values(stages).some(
          (s) => s.name !== stageName && s.status === "running",
        );
        if (!anyRunning) overallStatus = "idle";
      }

      return { stages, overallStatus };
    }),

  applyLog: (event, timestamp = new Date().toISOString()) =>
    set((state) => ({
      logs: [
        ...state.logs.slice(-(MAX_LOG_ENTRIES - 1)),
        { timestamp, level: normalizeLogLevel(event.level), message: event.message },
      ],
    })),

  applyPipelineComplete: () => set({ overallStatus: "completed" }),

  applySidecarHealth: (health) => set({ sidecar: { ...health } }),
}));
