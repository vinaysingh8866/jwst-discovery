import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { usePipelineStore } from "@/stores/pipelineStore";
import { useConfigStore } from "@/stores/configStore";
import { useDataStore } from "@/stores/dataStore";
import { STAGE_NAMES, STAGE_LABELS, STAGE_COLORS } from "@/lib/constants";
import { formatSize } from "@/lib/formatters";
import { useWorkspace } from "@/hooks/useWorkspace";
import type { StageName } from "@/types/pipeline";
import { errorMessage } from "@/lib/errors";

interface FilterDownloadState {
  status: "pending" | "downloading" | "completed" | "failed";
  bytesDone: number;
  bytesTotal: number;
}

function DownloadProgress() {
  const [downloadState, setDownloadState] = useState<Record<string, FilterDownloadState>>({});

  useEffect(() => {
    const unlisten = listen<{ filter: string; status: string; bytesDone: number; bytesTotal: number }>(
      "download:filter-progress",
      (event) => {
        const { filter, status, bytesDone, bytesTotal } = event.payload;
        setDownloadState((prev) => ({
          ...prev,
          [filter]: { status: status as FilterDownloadState["status"], bytesDone, bytesTotal },
        }));
      }
    );
    return () => { unlisten.then((fn) => fn()); };
  }, []);

  const entries = Object.entries(downloadState);
  if (entries.length === 0) return null;

  const completed = entries.filter(([, s]) => s.status === "completed").length;
  const hasActive = entries.some(([, s]) => s.status === "downloading" || s.status === "pending");

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-4 mb-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium">Data Ingestion</h3>
        <span className="text-xs text-gray-500">
          {completed}/{entries.length} filters
          {!hasActive && completed === entries.length && " — all done"}
        </span>
      </div>
      <div className="space-y-1.5">
        {entries.map(([filter, state]) => {
          const frac = state.bytesTotal > 0 ? state.bytesDone / state.bytesTotal : 0;
          return (
            <div key={filter} className="flex items-center gap-3">
              <div className="w-16 text-xs font-mono text-gray-400">{filter}</div>
              <div className="flex-1 h-1.5 bg-gray-800 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-300 ${
                    state.status === "completed" ? "bg-green-500"
                    : state.status === "failed" ? "bg-red-500"
                    : "bg-blue-500"
                  }`}
                  style={{ width: `${state.status === "completed" ? 100 : frac * 100}%` }}
                />
              </div>
              <div className="w-20 text-right">
                {state.status === "downloading" ? (
                  <span className="text-xs text-blue-400">{(state.bytesDone / 1e6).toFixed(0)} MB</span>
                ) : state.status === "completed" ? (
                  <span className="text-xs text-green-400">done</span>
                ) : state.status === "failed" ? (
                  <span className="text-xs text-red-400">failed</span>
                ) : (
                  <span className="text-xs text-gray-600">pending</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StageCard({ name, onRunStage, pipelineRunning }: { name: StageName; onRunStage: (name: StageName) => void; pipelineRunning: boolean }) {
  const stage = usePipelineStore((s) => s.stages[name]);
  const stages = usePipelineStore((s) => s.stages);
  const color = STAGE_COLORS[name];

  const statusBadge: Record<string, string> = {
    idle: "bg-gray-700 text-gray-400",
    running: "bg-blue-500/20 text-blue-400",
    completed: "bg-green-500/20 text-green-400",
    failed: "bg-red-500/20 text-red-400",
  };

  const canRun = stage.status !== "running" && !pipelineRunning;

  // Check if this stage is the next one to run (previous stage completed, this one idle)
  const stageIndex = STAGE_NAMES.indexOf(name);
  const prevCompleted = stageIndex === 0 || stages[STAGE_NAMES[stageIndex - 1]].status === "completed";
  const isNextUp = canRun && stage.status === "idle" && prevCompleted && stageIndex > 0;

  return (
    <div className={`bg-gray-900 rounded-lg border p-4 ${
      isNextUp ? "border-blue-500/50 ring-1 ring-blue-500/20" : "border-gray-800"
    }`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-full" style={{ backgroundColor: color }} />
          <h3 className="text-sm font-medium">{STAGE_LABELS[name]}</h3>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs px-2 py-0.5 rounded-full ${statusBadge[stage.status]}`}>
            {stage.status}
          </span>
          {canRun && (
            <button
              onClick={() => onRunStage(name)}
              className={`text-xs px-2.5 py-1 rounded font-medium transition-colors ${
                isNextUp
                  ? "bg-blue-600 hover:bg-blue-700 text-white"
                  : "bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-white"
              }`}
              title={`Run ${STAGE_LABELS[name]}`}
            >
              {isNextUp ? "Run Next" : "Run"}
            </button>
          )}
        </div>
      </div>
      {stage.status === "running" && (
        <div className="mt-2">
          <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-500 rounded-full transition-all duration-300"
              style={{ width: `${stage.progress * 100}%` }}
            />
          </div>
          <p className="text-xs text-gray-500 mt-1">{stage.message}</p>
        </div>
      )}
      {stage.status === "completed" && (
        <p className="text-xs text-green-400/70 mt-1">{stage.message}</p>
      )}
      {stage.status === "failed" && (
        <p className="text-xs text-red-400 mt-1">{stage.message}</p>
      )}
      {isNextUp && (
        <p className="text-xs text-blue-400/60 mt-1">Ready to run</p>
      )}
    </div>
  );
}

function LogViewer() {
  const logs = usePipelineStore((s) => s.logs);
  const lastLogs = logs.slice(-50);

  const levelColor: Record<string, string> = {
    debug: "text-gray-500",
    info: "text-gray-300",
    warn: "text-yellow-400",
    error: "text-red-400",
  };

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-4 mt-4">
      <h3 className="text-sm font-medium mb-2">Logs</h3>
      <div className="h-48 overflow-y-auto font-mono text-xs space-y-0.5">
        {lastLogs.length === 0 && (
          <p className="text-gray-600">No logs yet. Start the pipeline to see output.</p>
        )}
        {lastLogs.map((log, i) => (
          <div key={i} className={levelColor[log.level] || "text-gray-300"}>
            <span className="text-gray-600">
              {new Date(log.timestamp).toLocaleTimeString()}
            </span>{" "}
            [{log.level.toUpperCase()}] {log.message}
          </div>
        ))}
      </div>
    </div>
  );
}

export function PipelineDashboard() {
  const navigate = useNavigate();
  const overallStatus = usePipelineStore((s) => s.overallStatus);
  const stages = usePipelineStore((s) => s.stages);
  const config = useConfigStore((s) => s.config);
  const consumePendingDownload = useDataStore((s) => s.consumePendingDownload);
  const downloadedFiles = useDataStore((s) => s.downloadedFiles);
  const hasConsumed = useRef(false);

  const { workspace, error: workspaceError } = useWorkspace();

  const setDownloadedFiles = useDataStore((s) => s.setDownloadedFiles);

  // Listen for download manifest responses
  useEffect(() => {
    const unlisten = listen<{ downloads: Record<string, { size_mb: number; downloaded_at: string | null; exists: boolean }> }>(
      "download:manifest",
      (event) => {
        const existing: Record<string, { size_mb: number; downloaded_at: string | null }> = {};
        for (const [key, entry] of Object.entries(event.payload.downloads)) {
          if (entry.exists) {
            existing[key] = { size_mb: entry.size_mb, downloaded_at: entry.downloaded_at };
          }
        }
        setDownloadedFiles(existing);
      }
    );
    return () => { unlisten.then((fn) => fn()); };
  }, [setDownloadedFiles]);

  // Check for existing downloads on mount
  useEffect(() => {
    if (!workspace) return;
    invoke("check_downloads", { workspace }).catch(() => {});
  }, [workspace]);

  // Auto-start ingestion if there's a pending download from the Data page
  useEffect(() => {
    if (hasConsumed.current || !workspace) return;
    const pending = consumePendingDownload();
    if (!pending || pending.length === 0) return;
    hasConsumed.current = true;

    usePipelineStore.getState().reset();
    usePipelineStore.getState().setOverallStatus("running");
    usePipelineStore.getState().updateStage("ingest", {
      status: "running",
      progress: 0,
      message: "Starting download...",
    });

    // Start ingestion for each selected program
    for (const sel of pending) {
      const downloadConfig = {
        ...JSON.parse(JSON.stringify(config)),
        filters: sel.filters,
        program_id: sel.programId,
        force_redownload: sel.forceRedownload,
      };
      invoke("run_stage", {
        stage: "ingest",
        workspace,
        config: downloadConfig,
      }).catch((e) => {
        console.error("Failed to start download:", e);
        usePipelineStore.getState().updateStage("ingest", {
          status: "failed",
          message: errorMessage(e),
        });
        usePipelineStore.getState().setOverallStatus("failed");
      });
    }
  }, [consumePendingDownload, config, workspace]);

  const handleStartPipeline = async () => {
    if (!workspace) return;
    try {
      usePipelineStore.getState().reset();
      usePipelineStore.getState().setOverallStatus("running");
      await invoke("start_pipeline", {
        workspace,
        config: JSON.parse(JSON.stringify(config)),
      });
    } catch (e) {
      console.error("Failed to start pipeline:", e);
      usePipelineStore.getState().setOverallStatus("failed");
    }
  };

  const handleRunStage = async (stageName: StageName) => {
    if (!workspace) return;
    try {
      usePipelineStore.getState().updateStage(stageName, {
        status: "running",
        progress: 0,
        message: "Starting...",
      });
      usePipelineStore.getState().setOverallStatus("running");
      await invoke("run_stage", {
        stage: stageName,
        workspace,
        config: JSON.parse(JSON.stringify(config)),
      });
    } catch (e) {
      console.error(`Failed to run stage ${stageName}:`, e);
      usePipelineStore.getState().updateStage(stageName, {
        status: "failed",
        message: errorMessage(e),
      });
      usePipelineStore.getState().setOverallStatus("failed");
    }
  };

  const handleCancel = async () => {
    try {
      await invoke("cancel_pipeline");
      usePipelineStore.getState().setOverallStatus("idle");
    } catch (e) {
      console.error("Failed to cancel:", e);
    }
  };

  const onDiskCount = Object.keys(downloadedFiles).length;

  // When data is already on disk and ingest hasn't run, mark ingest as completed
  useEffect(() => {
    const ingestStatus = usePipelineStore.getState().stages.ingest.status;
    if (onDiskCount > 0 && ingestStatus === "idle") {
      usePipelineStore.getState().updateStage("ingest", {
        status: "completed",
        progress: 1,
        message: `${onDiskCount} filter${onDiskCount !== 1 ? "s" : ""} on disk`,
      });
    }
  }, [onDiskCount]);

  return (
    <div>
      {workspaceError && (
        <div className="bg-red-500/5 border border-red-500/20 rounded-lg p-3 mb-4 text-xs text-red-400 font-mono break-all">
          Cannot locate the workspace directory: {workspaceError}
        </div>
      )}
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold">Pipeline Dashboard</h2>
        <div className="flex gap-2">
          <button
            onClick={() => navigate("/data")}
            className="px-4 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4" />
            </svg>
            Select Data
          </button>
          <button
            onClick={handleStartPipeline}
            disabled={overallStatus === "running"}
            className="px-4 py-1.5 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg text-sm font-medium transition-colors"
          >
            Run Full Pipeline
          </button>
          <button
            onClick={handleCancel}
            disabled={overallStatus !== "running"}
            className="px-4 py-1.5 bg-red-600/20 hover:bg-red-600/30 disabled:bg-gray-800 disabled:text-gray-600 text-red-400 rounded-lg text-sm font-medium transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>

      {/* Data summary */}
      {onDiskCount > 0 && overallStatus === "idle" && (
        <div className="bg-gray-900 rounded-lg border border-gray-800 p-3 mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm text-gray-400">
            <svg className="w-4 h-4 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
            {onDiskCount} filter{onDiskCount !== 1 ? "s" : ""} ready
            <span className="text-xs text-gray-600">
              ({formatSize(Object.values(downloadedFiles).reduce((s, f) => s + f.size_mb, 0))})
            </span>
          </div>
          <button
            onClick={() => navigate("/data")}
            className="text-xs text-blue-400 hover:text-blue-300"
          >
            Change selection
          </button>
        </div>
      )}

      {/* No data prompt */}
      {onDiskCount === 0 && overallStatus === "idle" && stages.ingest.status === "idle" && (
        <div className="bg-gray-900 rounded-lg border border-gray-800 p-6 mb-4 text-center">
          <p className="text-sm text-gray-500 mb-3">No data downloaded yet. Select data to begin.</p>
          <button
            onClick={() => navigate("/data")}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-medium transition-colors"
          >
            Browse MAST Data
          </button>
        </div>
      )}

      {/* Download progress (shown during ingestion) */}
      <DownloadProgress />

      {/* Pipeline stage flow */}
      <div className="flex items-center gap-1 mb-4 overflow-x-auto pb-2">
        {STAGE_NAMES.map((name, i) => {
          const stage = stages[name];
          const color =
            stage.status === "running"
              ? STAGE_COLORS[name]
              : stage.status === "completed"
                ? "#22c55e"
                : stage.status === "failed"
                  ? "#ef4444"
                  : "#374151";
          return (
            <div key={name} className="flex items-center">
              <div
                className="px-3 py-1 rounded text-xs font-medium border"
                style={{ backgroundColor: `${color}33`, color, borderColor: color }}
              >
                {STAGE_LABELS[name]}
              </div>
              {i < STAGE_NAMES.length - 1 && (
                <div className="w-4 h-px bg-gray-700 mx-0.5" />
              )}
            </div>
          );
        })}
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {STAGE_NAMES.map((name) => (
          <StageCard key={name} name={name} onRunStage={handleRunStage} pipelineRunning={overallStatus === "running"} />
        ))}
      </div>

      <LogViewer />
    </div>
  );
}
