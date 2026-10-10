import { useState, useEffect } from "react";
import { useResultsStore } from "@/stores/resultsStore";
import { useWorkspace } from "@/hooks/useWorkspace";
import {
  type FilterMode,
  filterCandidates,
  countCandidates,
} from "@/lib/candidateFilters";
import { CandidateCard } from "@/components/anomaly/CandidateCard";

export function AnomalyPage() {
  const { candidatesReport, sourcesSummary, loading, error, notFound, loadCandidates, loadSourcesSummary } = useResultsStore();
  const { workspace, error: workspaceError } = useWorkspace();
  const [filterMode, setFilterMode] = useState<FilterMode>("all");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [minSn, setMinSn] = useState(0);
  const [minBandsSn5, setMinBandsSn5] = useState(0);
  const [searchId, setSearchId] = useState("");

  useEffect(() => {
    if (!workspace) return;
    if (!candidatesReport) {
      loadCandidates(workspace);
    }
    if (!sourcesSummary) {
      loadSourcesSummary(workspace);
    }
  }, [workspace, candidatesReport, sourcesSummary, loadCandidates, loadSourcesSummary]);

  // Auto-expand when searching by ID and exactly one result
  useEffect(() => {
    const term = searchId.trim();
    if (!term) return;
    const candidates = candidatesReport?.candidates;
    if (!candidates) return;
    const match = candidates.filter((c) => String(c.id).includes(term));
    if (match.length === 1) {
      setExpandedId(match[0].id);
    }
  }, [searchId, candidatesReport]);

  const reload = () => {
    if (!workspace) return;
    loadCandidates(workspace);
    loadSourcesSummary(workspace);
  };

  if (workspaceError) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center max-w-md">
          <p className="text-sm text-gray-400 mb-2">Cannot locate the workspace directory</p>
          <p className="text-xs text-red-400/70 mb-2 font-mono break-all">{workspaceError}</p>
          <p className="text-xs text-gray-600">Set an explicit workspace path in Config.</p>
        </div>
      </div>
    );
  }

  if (loading || !workspace) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <div className="w-8 h-8 border-2 border-gray-600 border-t-blue-400 rounded-full animate-spin mx-auto mb-3" />
          <p className="text-sm text-gray-500">Loading anomaly candidates...</p>
        </div>
      </div>
    );
  }

  if (notFound) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center max-w-md">
          <p className="text-sm text-gray-300 mb-2">No pipeline results for this workspace yet</p>
          <p className="text-xs text-gray-500 mb-2">
            Nothing has been written to <span className="font-mono">report/candidates.json</span>. Download the field on
            the Data page and run the pipeline, or point the app at an existing workspace root with{" "}
            <span className="font-mono">JWST_WORKSPACE_ROOT</span>.
          </p>
          <p className="text-xs text-gray-600 mb-4 font-mono break-all">Workspace: {workspace}</p>
          <button
            onClick={reload}
            className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-xs"
          >
            Check again
          </button>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center max-w-md">
          <p className="text-sm text-gray-300 mb-2">Could not load anomaly candidates</p>
          <p className="text-xs text-red-400/80 mb-2 font-mono break-all">{error}</p>
          <p className="text-xs text-gray-600 mb-4 font-mono break-all">Workspace: {workspace}</p>
          <button
            onClick={reload}
            className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-xs"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!candidatesReport || candidatesReport.candidates.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500">
        <p className="text-sm">No anomaly candidates found. Run the pipeline first.</p>
      </div>
    );
  }

  const { candidates, filters, n_total_sources } = candidatesReport;

  const filtered = filterCandidates(candidates, {
    searchTerm: searchId,
    mode: filterMode,
    minSn,
    minBandsSn5,
  });

  // Stats — novel count from all sources
  const counts = countCandidates(candidates);
  const nNovelTotal = candidatesReport.n_total_novel ?? (sourcesSummary
    ? sourcesSummary.sources.filter((s) => s.novel).length
    : counts.nNovel);

  const filterButtons: { mode: FilterMode; label: string; count?: number }[] = [
    { mode: "all", label: "All", count: candidates.length },
    { mode: "novel", label: "Novel", count: counts.nNovel },
    { mode: "high_z", label: "High-z", count: counts.nHighZ },
    { mode: "agn", label: "AGN/LRD", count: counts.nAgn },
    { mode: "mw_agn", label: "MW AGN", count: counts.nMwAgn },
    { mode: "xray", label: "X-ray", count: counts.nXray },
    { mode: "radio", label: "Radio", count: counts.nRadio },
    { mode: "dropout", label: "Dropouts", count: counts.nDropout },
    { mode: "red_excess", label: "Red Excess", count: counts.nRedExcess },
    { mode: "star", label: "Stars" },
    { mode: "galaxy", label: "Galaxies" },
  ];

  const stats = [
    { label: "Total Sources", value: n_total_sources.toLocaleString(), color: "text-blue-400" },
    { label: "Novel Objects", value: nNovelTotal, color: "text-emerald-400" },
    { label: "High-z (z>6)", value: counts.nHighZ, color: "text-violet-400" },
    { label: "AGN / LRD", value: counts.nAgn, color: "text-pink-400" },
    { label: "MW AGN", value: candidatesReport.mw_summary?.n_agn_candidates ?? counts.nMwAgn, color: "text-orange-400" },
    { label: "X-ray Sources", value: candidatesReport.mw_summary?.n_chandra ?? counts.nXray, color: "text-amber-400" },
    { label: "Dropouts", value: counts.nDropout, color: "text-purple-400" },
  ];

  return (
    <div className="absolute inset-0 flex flex-col p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4 flex-shrink-0">
        <div>
          <h2 className="text-xl font-bold">Anomaly Candidates</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            {candidates.length} candidates from {n_total_sources.toLocaleString()} sources
          </p>
        </div>
        <button
          onClick={reload}
          className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-xs"
        >
          Reload
        </button>
      </div>

      {/* Stats bar */}
      <div className="grid grid-cols-7 gap-3 mb-4 flex-shrink-0">
        {stats.map((stat) => (
          <div key={stat.label} className="bg-gray-900 border border-gray-800 rounded-lg p-3">
            <p className="text-xs text-gray-500">{stat.label}</p>
            <p className={`text-lg font-bold ${stat.color}`}>{stat.value}</p>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="flex items-center gap-4 mb-4 flex-shrink-0 flex-wrap">
        {/* ID search */}
        <div className="relative">
          <input
            type="text"
            placeholder="Search ID..."
            value={searchId}
            onChange={(e) => setSearchId(e.target.value)}
            className="w-28 px-2.5 py-1 bg-gray-800 border border-gray-700 rounded-full text-xs font-mono text-gray-300 placeholder-gray-600 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30"
          />
          {searchId && (
            <button
              onClick={() => setSearchId("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
            >
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>

        <div className="h-5 w-px bg-gray-700" />

        <div className="flex gap-1.5">
          {filterButtons.map((fb) => (
            <button
              key={fb.mode}
              onClick={() => { setFilterMode(fb.mode); setSearchId(""); }}
              className={`px-3 py-1 rounded-full text-xs transition-colors ${
                filterMode === fb.mode && !searchId
                  ? "bg-blue-600 text-white"
                  : "bg-gray-800 text-gray-400 hover:bg-gray-700"
              }`}
            >
              {fb.label}
              {fb.count != null && (
                <span className="ml-1 opacity-60">({fb.count})</span>
              )}
            </button>
          ))}
        </div>

        <div className="h-5 w-px bg-gray-700" />

        {/* S/N sliders */}
        <div className="flex items-center gap-1.5">
          <label className="text-[11px] text-gray-500 whitespace-nowrap">Min S/N</label>
          <input
            type="range"
            min={0} max={50} step={1}
            value={minSn}
            onChange={(e) => setMinSn(Number(e.target.value))}
            className="w-20 h-1 accent-blue-500"
          />
          <span className="text-xs font-mono text-gray-400 w-6 text-right">{minSn}</span>
        </div>

        <div className="flex items-center gap-1.5">
          <label className="text-[11px] text-gray-500 whitespace-nowrap">Min bands S/N&gt;5</label>
          <input
            type="range"
            min={0} max={11} step={1}
            value={minBandsSn5}
            onChange={(e) => setMinBandsSn5(Number(e.target.value))}
            className="w-20 h-1 accent-blue-500"
          />
          <span className="text-xs font-mono text-gray-400 w-4 text-right">{minBandsSn5}</span>
        </div>

        <span className="text-[11px] text-gray-600">
          {filtered.length} shown
        </span>
      </div>

      {/* Candidates list */}
      <div className="flex-1 min-h-0 overflow-y-auto space-y-2 pr-1">
        {filtered.length === 0 ? (
          <p className="text-sm text-gray-600 text-center py-8">
            No candidates match this filter.
          </p>
        ) : (
          filtered.map((c) => (
            <CandidateCard
              key={c.id}
              candidate={c}
              workspace={workspace}
              filters={filters}
              isExpanded={expandedId === c.id}
              onToggle={() => setExpandedId(expandedId === c.id ? null : c.id)}
            />
          ))
        )}
      </div>
    </div>
  );
}
