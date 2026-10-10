import { useState, useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import { usePipelineStore, sidecarStatusOf } from "@/stores/pipelineStore";
import { useConfigStore } from "@/stores/configStore";
import { useResultsStore } from "@/stores/resultsStore";

interface WorkspaceInfo {
  name: string;
  path: string;
  has_report: boolean;
  has_raw: boolean;
  n_fits: number;
}

export function TopBar() {
  const overallStatus = usePipelineStore((s) => s.overallStatus);
  const sidecar = usePipelineStore((s) => s.sidecar);
  const sidecarStatus = sidecarStatusOf(sidecar);
  const config = useConfigStore((s) => s.config);
  const updateConfig = useConfigStore((s) => s.updateConfig);
  const clearResults = useResultsStore((s) => s.clearResults);

  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[]>([]);
  const [open, setOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    invoke<WorkspaceInfo[]>("list_workspaces")
      .then(setWorkspaces)
      .catch(() => {});
  }, []);

  // Close dropdown on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const switchWorkspace = (ws: WorkspaceInfo) => {
    updateConfig({
      target_field: ws.name,
      workspace: ws.path,
    });
    clearResults();
    setOpen(false);
  };

  const sidecarColor: Record<string, string> = {
    starting: "bg-yellow-500 animate-pulse",
    ready: "bg-green-500",
    unavailable: "bg-red-500",
  };

  const statusColor: Record<string, string> = {
    idle: "bg-gray-500",
    running: "bg-blue-500 animate-pulse",
    completed: "bg-green-500",
    failed: "bg-red-500",
  };

  return (
    <header className="h-10 bg-gray-900 border-b border-gray-800 flex items-center px-4 gap-3 justify-between">
      <div className="flex items-center gap-4">
        <div
          className="flex items-center gap-2"
          title={sidecar.message ?? sidecar.launch ?? "Python pipeline process"}
          data-testid="sidecar-status"
        >
          <div className={`w-2 h-2 rounded-full ${sidecarColor[sidecarStatus]}`} />
          <span className="text-xs text-gray-400 capitalize">Sidecar: {sidecarStatus}</span>
        </div>
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${statusColor[overallStatus]}`} />
          <span className="text-xs text-gray-400 capitalize">
            Pipeline: {overallStatus}
          </span>
        </div>
      </div>

      {/* Workspace selector */}
      <div className="relative" ref={dropdownRef}>
        <button
          onClick={() => {
            if (!open) {
              invoke<WorkspaceInfo[]>("list_workspaces")
                .then(setWorkspaces)
                .catch(() => {});
            }
            setOpen(!open);
          }}
          className="flex items-center gap-2 px-3 py-1 rounded-md bg-gray-800 hover:bg-gray-700 border border-gray-700 text-xs transition-colors"
        >
          <svg className="w-3.5 h-3.5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
          </svg>
          <span className="text-gray-300 font-medium">{config.target_field}</span>
          <svg className={`w-3 h-3 text-gray-500 transition-transform ${open ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {open && workspaces.length > 0 && (
          <div className="absolute right-0 top-full mt-1 w-64 bg-gray-900 border border-gray-700 rounded-lg shadow-xl z-50 overflow-hidden flex flex-col max-h-[70vh]">
            <div className="px-3 py-2 border-b border-gray-800 shrink-0">
              <span className="text-[10px] text-gray-500 uppercase tracking-wider font-medium">Workspaces</span>
            </div>
            <div className="overflow-y-auto flex-1">
            {workspaces.map((ws) => {
              const active = config.target_field === ws.name;
              return (
                <button
                  key={ws.name}
                  onClick={() => switchWorkspace(ws)}
                  className={`w-full text-left px-3 py-2 flex items-center justify-between hover:bg-gray-800 transition-colors ${
                    active ? "bg-blue-500/10 border-l-2 border-blue-500" : "border-l-2 border-transparent"
                  }`}
                >
                  <div>
                    <div className={`text-xs font-medium ${active ? "text-blue-400" : "text-gray-300"}`}>
                      {ws.name}
                    </div>
                    <div className="text-[10px] text-gray-600 mt-0.5">
                      {ws.n_fits} FITS files
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {ws.has_report && (
                      <span className="text-[9px] px-1.5 py-0.5 rounded bg-green-500/15 text-green-400/80 border border-green-500/20">
                        results
                      </span>
                    )}
                    {active && (
                      <svg className="w-3.5 h-3.5 text-blue-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                  </div>
                </button>
              );
            })}
            </div>
          </div>
        )}
      </div>
    </header>
  );
}
