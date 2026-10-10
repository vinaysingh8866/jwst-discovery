import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { useConfigStore } from "@/stores/configStore";
import { useDataStore } from "@/stores/dataStore";
import type { MastQueryResult } from "@/stores/dataStore";
import { useWorkspace } from "@/hooks/useWorkspace";
import { usePipelineStore } from "@/stores/pipelineStore";
import { SidecarNotice } from "@/components/shared/SidecarNotice";
import { formatSize } from "@/lib/formatters";
import { FieldSetupPanel } from "@/components/data/FieldSetupPanel";
import { MastMissionsPanel } from "@/components/data/MastMissionsPanel";
import { errorMessage } from "@/lib/errors";
import { isOwnError, type SidecarErrorEvent } from "@/lib/sidecarErrors";

// ---------------------------------------------------------------------------
// Main Data Page
// ---------------------------------------------------------------------------
export function DataPage() {
  const navigate = useNavigate();
  const config = useConfigStore((s) => s.config);
  const queryResult = useDataStore((s) => s.queryResult);
  const selectedItems = useDataStore((s) => s.selectedItems);
  const downloadedFiles = useDataStore((s) => s.downloadedFiles);
  const setQueryResult = useDataStore((s) => s.setQueryResult);
  const toggleItem = useDataStore((s) => s.toggleItem);
  const selectAllInProgram = useDataStore((s) => s.selectAllInProgram);
  const clearProgram = useDataStore((s) => s.clearProgram);
  const setDownloadedFiles = useDataStore((s) => s.setDownloadedFiles);
  const setPendingDownload = useDataStore((s) => s.setPendingDownload);

  const [querying, setQuerying] = useState(false);
  const [expandedPrograms, setExpandedPrograms] = useState<Set<string>>(new Set());

  const { workspace, error: workspaceError } = useWorkspace();
  const sidecarReady = usePipelineStore((s) => s.sidecar.ready);
  const [queryError, setQueryError] = useState<string | null>(null);
  const [queryStatus, setQueryStatus] = useState<string | null>(null);
  const queryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** Request id of the MAST query in flight; `sidecar:error` is routed on it. */
  const pendingQueryId = useRef<string | null>(null);

  const QUERY_TIMEOUT_MS = 180_000;
  const beginQuery = () => {
    setQuerying(true);
    setQueryError(null);
    if (queryTimer.current) clearTimeout(queryTimer.current);
    queryTimer.current = setTimeout(() => {
      setQuerying(false);
      setQueryError("MAST did not answer within 3 minutes. The archive may be slow; try again.");
    }, QUERY_TIMEOUT_MS);
  };
  const endQuery = () => {
    if (queryTimer.current) clearTimeout(queryTimer.current);
    queryTimer.current = null;
    pendingQueryId.current = null;
    setQuerying(false);
  };
  useEffect(() => () => { if (queryTimer.current) clearTimeout(queryTimer.current); }, []);

  // While a query runs, surface the sidecar's progress log lines under the spinner
  useEffect(() => {
    if (!querying) {
      setQueryStatus(null);
      return;
    }
    const unlisten = listen<{ level: string; message: string }>("pipeline:log", (event) => {
      const msg = event.payload.message.replace(/^.*jwst_pipeline:\s*/, "");
      if (/MAST|observation|program|cache|query/i.test(msg)) setQueryStatus(msg);
    });
    return () => { unlisten.then((fn) => fn()); };
  }, [querying]);

  // Errors from one-shot sidecar requests. Only the error carrying the id of
  // *our* MAST query belongs here; the MastMissions panel reports its own.
  useEffect(() => {
    const unlisten = listen<SidecarErrorEvent>("sidecar:error", (event) => {
      if (!isOwnError(event.payload, pendingQueryId.current)) return;
      endQuery();
      setQueryError(event.payload.message);
    });
    return () => { unlisten.then((fn) => fn()); };
  }, []);

  // Listen for MAST query results
  useEffect(() => {
    const unlisten = listen<MastQueryResult>("mast:query-result", (event) => {
      setQueryResult(event.payload);
      endQuery();
      if (event.payload.programs.length > 0) {
        setExpandedPrograms(new Set([event.payload.programs[0].program_id]));
      }
    });
    return () => { unlisten.then((fn) => fn()); };
  }, [setQueryResult]);

  // Listen for download manifest
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

  /** Send the query; the host answers with the request id the sidecar will echo. */
  const startQuery = (forceRefresh: boolean) => {
    beginQuery();
    invoke<string>("query_mast", { config: JSON.parse(JSON.stringify(config)), forceRefresh })
      .then((requestId) => { pendingQueryId.current = requestId; })
      .catch((e) => { endQuery(); setQueryError(errorMessage(e)); });
  };

  // Auto-query once the sidecar is ready (cached results load instantly)
  useEffect(() => {
    if (sidecarReady && !queryResult && !querying) {
      startQuery(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sidecarReady]);

  // Check for existing downloads
  useEffect(() => {
    if (workspace) {
      invoke("check_downloads", { workspace }).catch(() => {});
    }
  }, [workspace]);

  const handleQuery = (forceRefresh: boolean) => {
    if (forceRefresh) {
      setQueryResult(null);
    }
    startQuery(forceRefresh);
  };

  const toggleProgram = (pid: string) => {
    setExpandedPrograms((prev) => {
      const next = new Set(prev);
      if (next.has(pid)) next.delete(pid); else next.add(pid);
      return next;
    });
  };

  // Group selected items by program
  const selectedByProgram: Record<string, string[]> = {};
  for (const key of selectedItems) {
    const [pid, filt] = key.split(":");
    if (!selectedByProgram[pid]) selectedByProgram[pid] = [];
    selectedByProgram[pid].push(filt);
  }

  const handleProceed = (forceRedownload = false) => {
    const selections = Object.entries(selectedByProgram).map(([pid, filters]) => ({
      programId: pid,
      filters,
      forceRedownload,
    }));
    setPendingDownload(selections);
    navigate("/");
  };

  const totalSelected = selectedItems.size;
  const onDiskCount = Object.keys(downloadedFiles).length;

  return (
    <div className="absolute inset-0 overflow-auto p-4">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-xl font-bold">JWST Data Browser</h2>
          <p className="text-sm text-gray-500 mt-1">
            Browse and select JWST observations for your pipeline
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-right text-xs text-gray-500">
            <div>{config.target_field}</div>
            <div>RA {config.center_ra.toFixed(3)}, Dec {config.center_dec.toFixed(3)}</div>
          </div>
        </div>
      </div>

      {workspaceError && (
        <div className="bg-red-500/5 border border-red-500/20 rounded-lg p-3 mb-4 text-xs text-red-400 font-mono break-all">
          Cannot locate the workspace directory: {workspaceError}
        </div>
      )}

      {/* Field Setup Panel */}
      <FieldSetupPanel workspace={workspace ?? ""} />

      {/* MAST Missions Discovery Panel */}
      <MastMissionsPanel />

      {/* On-disk summary */}
      {onDiskCount > 0 && (
        <div className="bg-green-500/5 border border-green-500/20 rounded-lg p-3 mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <svg className="w-4 h-4 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
            <span className="text-sm text-green-400">
              {onDiskCount} filter{onDiskCount !== 1 ? "s" : ""} already downloaded
            </span>
            <span className="text-xs text-gray-500">
              ({formatSize(Object.values(downloadedFiles).reduce((s, f) => s + f.size_mb, 0))} on disk)
            </span>
          </div>
          <button
            onClick={() => navigate("/")}
            className="text-xs px-3 py-1.5 bg-green-600 hover:bg-green-700 rounded-lg font-medium transition-colors"
          >
            Go to Pipeline
          </button>
        </div>
      )}

      <SidecarNotice />

      {queryError && (
        <div role="alert" className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
          <div className="font-medium">Archive query failed</div>
          <div className="mt-1 break-words text-xs text-red-200/80">{queryError}</div>
        </div>
      )}

      {/* Search / Loading */}
      {!queryResult && !querying && (
        <div className="bg-gray-900 rounded-lg border border-gray-800 p-12 text-center">
          <div className="text-4xl mb-4">🔭</div>
          <h3 className="text-lg font-medium mb-2">Search the MAST Archive</h3>
          <p className="text-sm text-gray-500 mb-4 max-w-md mx-auto">
            Find all available JWST imaging data at your target position.
            Configure coordinates in the Config page.
          </p>
          <button
            onClick={() => handleQuery(true)}
            disabled={!sidecarReady}
            title={sidecarReady ? undefined : "Waiting for the pipeline process"}
            className="px-6 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:text-gray-400 rounded-lg text-sm font-medium transition-colors"
          >
            {sidecarReady ? "Search MAST Archive" : "Waiting for pipeline process…"}
          </button>
        </div>
      )}

      {querying && (
        <div className="bg-gray-900 rounded-lg border border-gray-800 p-12 text-center">
          <div className="inline-block w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full animate-spin mb-3" />
          <p className="text-sm text-gray-400">Querying MAST archive...</p>
          <p className="text-xs text-gray-600 mt-1">
            {queryStatus ?? "Searching all JWST programs at this position (MAST can take a minute)"}
          </p>
        </div>
      )}

      {queryResult && queryResult.programs.length === 0 && (
        <div className="bg-gray-900 rounded-lg border border-gray-800 p-12 text-center">
          <p className="text-sm text-yellow-400 mb-2">No JWST imaging data found at this position</p>
          <p className="text-xs text-gray-500 mb-4">Try different coordinates in Config</p>
          <button
            onClick={() => handleQuery(true)}
            className="px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm transition-colors"
          >
            Search Again
          </button>
        </div>
      )}

      {/* Program list */}
      {queryResult && queryResult.programs.length > 0 && (
        <>
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-3">
              <p className="text-sm text-gray-400">
                {queryResult.observations} observations across {queryResult.programs.length} programs
              </p>
              {queryResult.cached && queryResult.cachedAt && (
                <span className="text-xs text-yellow-500/70 px-1.5 py-0.5 rounded bg-yellow-500/10 border border-yellow-500/20">
                  cached {new Date(queryResult.cachedAt).toLocaleString()}
                </span>
              )}
            </div>
            <button
              onClick={() => handleQuery(true)}
              disabled={querying}
              className="text-xs text-blue-400 hover:text-blue-300 disabled:text-gray-600 flex items-center gap-1"
            >
              <svg className={`w-3 h-3 ${querying ? "animate-spin" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              Refresh
            </button>
          </div>

          <div className="space-y-2 mb-4">
            {queryResult.programs.map((prog) => {
              const expanded = expandedPrograms.has(prog.program_id);
              const selectedCount = prog.filters.filter((f) =>
                selectedItems.has(`${prog.program_id}:${f.filter}`)
              ).length;
              const onDiskInProg = prog.filters.filter((f) =>
                !!downloadedFiles[`${prog.program_id}:${f.filter}`]
              ).length;

              return (
                <div key={prog.program_id} className="bg-gray-900 rounded-lg border border-gray-800 overflow-hidden">
                  <button
                    onClick={() => toggleProgram(prog.program_id)}
                    className="w-full flex items-center justify-between p-3 hover:bg-gray-800/50 transition-colors text-left"
                  >
                    <div className="flex items-center gap-3">
                      <svg className={`w-3 h-3 text-gray-500 transition-transform ${expanded ? "rotate-90" : ""}`} fill="currentColor" viewBox="0 0 20 20">
                        <path d="M6 6l8 4-8 4V6z" />
                      </svg>
                      <span className="text-sm font-medium">PID {prog.program_id}</span>
                      <span className="text-xs text-gray-500">{prog.instruments.join(", ")}</span>
                      {onDiskInProg > 0 && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/15 text-green-400/80">
                          {onDiskInProg} on disk
                        </span>
                      )}
                      {selectedCount > 0 && (
                        <span className="text-xs px-1.5 py-0.5 rounded-full bg-blue-500/20 text-blue-400">
                          {selectedCount} selected
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 text-xs text-gray-500">
                      <span>{prog.mosaic_count} filters</span>
                      <span>{prog.observations} obs</span>
                      {prog.has_level3 && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400/70 border border-blue-500/20">
                          L3 mosaics
                        </span>
                      )}
                    </div>
                  </button>

                  {expanded && (
                    <div className="border-t border-gray-800 bg-gray-950/50 p-3">
                      {prog.targets.length > 0 && (
                        <p className="text-xs text-gray-600 mb-2">
                          Targets: {prog.targets.join(", ")}
                        </p>
                      )}
                      <div className="flex gap-2 mb-2">
                        <button onClick={() => selectAllInProgram(prog.program_id)} className="text-xs text-blue-400 hover:text-blue-300">
                          Select All
                        </button>
                        <button onClick={() => clearProgram(prog.program_id)} className="text-xs text-gray-500 hover:text-gray-400">
                          Clear
                        </button>
                      </div>
                      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-1.5">
                        {prog.filters.map((f) => {
                          const key = `${prog.program_id}:${f.filter}`;
                          const selected = selectedItems.has(key);
                          const existingFile = downloadedFiles[key];
                          const isOnDisk = !!existingFile;

                          return (
                            <label
                              key={key}
                              className={`flex items-center gap-2 p-2 rounded border cursor-pointer transition-colors ${
                                selected
                                  ? "border-blue-500/40 bg-blue-500/10"
                                  : "border-gray-800 bg-gray-800/30 hover:border-gray-700"
                              }`}
                            >
                              <input
                                type="checkbox"
                                checked={selected}
                                onChange={() => toggleItem(key)}
                                className="rounded border-gray-600 bg-gray-800 text-blue-500 focus:ring-blue-500 focus:ring-offset-0"
                              />
                              <span className="text-xs font-mono font-medium">{f.filter}</span>
                              {isOnDisk && (
                                <svg className="w-3 h-3 text-green-400/70 ml-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                                </svg>
                              )}
                            </label>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Action bar */}
          <div className="sticky bottom-0 bg-gray-950/95 backdrop-blur border-t border-gray-800 -mx-6 px-6 py-3 flex items-center justify-between">
            <p className="text-sm text-gray-400">
              {totalSelected} filter{totalSelected !== 1 ? "s" : ""} selected
              {totalSelected > 0 && (
                <> from {Object.keys(selectedByProgram).length} program{Object.keys(selectedByProgram).length !== 1 ? "s" : ""}</>
              )}
            </p>
            <div className="flex items-center gap-2">
              {/* Show "Start Pipeline" when all selected filters are already on disk */}
              {totalSelected > 0 && Array.from(selectedItems).every((key) => !!downloadedFiles[key]) && (
                <button
                  onClick={() => navigate("/")}
                  className="px-5 py-2 bg-green-600 hover:bg-green-700 rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
                >
                  Start Pipeline
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M13 7l5 5m0 0l-5 5m5-5H6" />
                  </svg>
                </button>
              )}
              <button
                onClick={() => handleProceed(false)}
                disabled={totalSelected === 0}
                className="px-5 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
              >
                {Array.from(selectedItems).every((key) => !!downloadedFiles[key])
                  ? "Re-download & Start"
                  : "Download & Start Pipeline"}
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
