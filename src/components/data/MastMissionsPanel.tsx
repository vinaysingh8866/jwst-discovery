import { useState, useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { useConfigStore } from "@/stores/configStore";
import { isOwnError, type SidecarErrorEvent } from "@/lib/sidecarErrors";

interface MastMissionsProgram {
  program_id: string;
  pi_name: string;
  observations: number;
  instruments: string[];
  filters: string[];
  total_exposure_hrs: number;
}

export function MastMissionsPanel() {
  const config = useConfigStore((s) => s.config);
  const [collapsed, setCollapsed] = useState(true);
  const [querying, setQuerying] = useState(false);
  const [programs, setPrograms] = useState<MastMissionsProgram[]>([]);
  const [totalObs, setTotalObs] = useState(0);
  /** Request id of the discovery in flight; `sidecar:error` is routed on it. */
  const pendingId = useRef<string | null>(null);

  useEffect(() => {
    const unlisten = listen<{ totalObservations: number; programs: MastMissionsProgram[] }>(
      "mast:missions-result",
      (event) => {
        setPrograms(event.payload.programs);
        setTotalObs(event.payload.totalObservations);
        pendingId.current = null;
        setQuerying(false);
      }
    );
    return () => { unlisten.then((fn) => fn()); };
  }, []);

  // Errors from the sidecar: only the one for *our* request, never the main
  // MAST query's (that one is shown by the Data page).
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const unlisten = listen<SidecarErrorEvent>("sidecar:error", (event) => {
      if (!isOwnError(event.payload, pendingId.current)) return;
      pendingId.current = null;
      setQuerying(false);
      setError(event.payload.message);
    });
    return () => { unlisten.then((fn) => fn()); };
  }, []);

  const handleDiscover = async () => {
    setError(null);
    setQuerying(true);
    try {
      pendingId.current = await invoke<string>("query_mast_missions", {
        config: JSON.parse(JSON.stringify(config)),
      });
    } catch {
      pendingId.current = null;
      setQuerying(false);
    }
  };

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 mb-4 overflow-hidden">
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="w-full flex items-center justify-between p-4 hover:bg-gray-800/50 transition-colors text-left"
      >
        <div className="flex items-center gap-3">
          <svg className={`w-3 h-3 text-gray-500 transition-transform ${collapsed ? "" : "rotate-90"}`} fill="currentColor" viewBox="0 0 20 20">
            <path d="M6 6l8 4-8 4V6z" />
          </svg>
          <h3 className="text-sm font-medium">JWST Observations (MastMissions)</h3>
          <span className="text-xs text-gray-500">All programs at this position</span>
        </div>
        {programs.length > 0 && (
          <span className="text-xs text-gray-400">
            {totalObs} obs, {programs.length} programs
          </span>
        )}
      </button>

      {!collapsed && (
        <div className="border-t border-gray-800 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-xs text-gray-500">
              Discover all JWST programs covering RA {config.center_ra.toFixed(3)}, Dec {config.center_dec.toFixed(3)}
            </p>
            <button
              onClick={handleDiscover}
              disabled={querying}
              className="text-xs px-3 py-1.5 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:text-gray-500 rounded transition-colors flex items-center gap-1.5"
            >
              {querying && (
                <span className="inline-block w-3 h-3 border-2 border-blue-300 border-t-transparent rounded-full animate-spin" />
              )}
              {querying ? "Discovering..." : "Discover Observations"}
            </button>
          </div>

          {programs.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-gray-500 border-b border-gray-800">
                    <th className="text-left py-1.5 pr-3">Program</th>
                    <th className="text-left py-1.5 pr-3">PI</th>
                    <th className="text-left py-1.5 pr-3">Instruments</th>
                    <th className="text-left py-1.5 pr-3">Filters</th>
                    <th className="text-right py-1.5 pr-3">Obs</th>
                    <th className="text-right py-1.5">Exposure</th>
                  </tr>
                </thead>
                <tbody>
                  {programs.map((p) => (
                    <tr key={p.program_id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                      <td className="py-1.5 pr-3 font-mono text-blue-400">{p.program_id}</td>
                      <td className="py-1.5 pr-3 text-gray-300 max-w-[120px] truncate">{p.pi_name || "—"}</td>
                      <td className="py-1.5 pr-3 text-gray-400">{p.instruments.join(", ")}</td>
                      <td className="py-1.5 pr-3 text-gray-400 max-w-[200px] truncate font-mono">
                        {p.filters.join(", ")}
                      </td>
                      <td className="py-1.5 pr-3 text-right text-gray-300">{p.observations}</td>
                      <td className="py-1.5 text-right text-gray-300">{p.total_exposure_hrs}h</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {programs.length === 0 && !querying && (
            <p className="text-xs text-gray-600 text-center py-2">
              Click "Discover Observations" to search all JWST programs at this position
            </p>
          )}
        </div>
      )}
      {error && (
        <div role="alert" className="px-4 pb-3 text-xs text-red-300 break-words">
          MAST Missions query failed: {error}
        </div>
      )}
    </div>
  );
}
