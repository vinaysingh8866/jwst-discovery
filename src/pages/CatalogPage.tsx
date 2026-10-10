import { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useWorkspace } from "@/hooks/useWorkspace";
import { useResultsStore } from "@/stores/resultsStore";
import { formatRA, formatDec } from "@/lib/formatters";

type SortDir = "asc" | "desc";

interface ColumnDef {
  key: string;
  label: string;
  width: number;
  format?: (val: unknown) => string;
  sortable?: boolean;
}

const NUM_FMT = (digits: number) => (val: unknown) => {
  if (val == null) return "--";
  const n = Number(val);
  return isNaN(n) ? "--" : n.toFixed(digits);
};

const INT_FMT = (val: unknown) => {
  if (val == null || val === 0) return "--";
  return String(val);
};

const BOOL_FMT = (val: unknown) => (val === true ? "Yes" : val === false ? "No" : "--");

const BASE_COLUMNS: ColumnDef[] = [
  { key: "anomaly_rank", label: "Rank", width: 60, format: INT_FMT, sortable: true },
  { key: "id", label: "ID", width: 70, format: INT_FMT, sortable: true },
  { key: "ra", label: "RA", width: 140, format: (v) => v != null ? formatRA(Number(v)) : "--", sortable: true },
  { key: "dec", label: "Dec", width: 130, format: (v) => v != null ? formatDec(Number(v)) : "--", sortable: true },
  { key: "anomaly_score", label: "Score", width: 70, format: NUM_FMT(4), sortable: true },
  { key: "object_class", label: "Class", width: 90, format: (v) => v ? String(v) : "--", sortable: true },
  { key: "z_phot_eazy", label: "z_phot", width: 65, format: NUM_FMT(2), sortable: true },
  { key: "z_phot_quality", label: "z Qual", width: 65, format: (v) => v && v !== "none" ? String(v) : "--", sortable: true },
  { key: "stellarity", label: "Star?", width: 60, format: NUM_FMT(2), sortable: true },
  { key: "dropout_flag", label: "Dropout", width: 90, format: (v) => v && v !== "none" ? String(v) : "--" },
  { key: "red_excess", label: "Red", width: 50, format: BOOL_FMT },
  { key: "novel", label: "Novel", width: 55, format: BOOL_FMT },
  { key: "agn_confidence", label: "AGN Conf", width: 75, format: NUM_FMT(2), sortable: true },
  { key: "mw_class", label: "MW Class", width: 100, format: (v) => v ? String(v) : "--", sortable: true },
  { key: "z_agree", label: "z Agree", width: 60, format: BOOL_FMT },
  { key: "ned_match", label: "NED", width: 50, format: BOOL_FMT },
  { key: "gaia_match", label: "Gaia", width: 50, format: BOOL_FMT },
];

export function CatalogPage() {
  const navigate = useNavigate();
  const { sourcesSummary, loading, error, loadSourcesSummary } = useResultsStore();
  const { workspace, error: workspaceError } = useWorkspace();
  const [sortCol, setSortCol] = useState<string>("anomaly_rank");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [search, setSearch] = useState("");
  const [filterNovel, setFilterNovel] = useState(false);
  const [filterDropout, setFilterDropout] = useState(false);
  const [filterAGN, setFilterAGN] = useState(false);
  const [filterMwAgn, setFilterMwAgn] = useState(false);
  const [filterHighZ, setFilterHighZ] = useState(false);

  useEffect(() => {
    if (!workspace) return;
    if (!sourcesSummary) {
      loadSourcesSummary(workspace);
    }
  }, [workspace, sourcesSummary, loadSourcesSummary]);

  const reload = () => {
    if (workspace) loadSourcesSummary(workspace);
  };

  // Build columns including mag columns from data
  const columns = useMemo(() => {
    const cols = [...BASE_COLUMNS];
    if (sourcesSummary) {
      for (const filt of sourcesSummary.filters) {
        cols.push({
          key: `mag_${filt}`,
          label: filt,
          width: 70,
          format: NUM_FMT(2),
          sortable: true,
        });
      }
    }
    return cols;
  }, [sourcesSummary]);

  // Filter + sort sources
  const sortedSources = useMemo(() => {
    if (!sourcesSummary) return [];
    let sources = sourcesSummary.sources;

    if (filterNovel) {
      sources = sources.filter((s) => s.novel === true);
    }
    if (filterDropout) {
      sources = sources.filter((s) => s.dropout_flag && s.dropout_flag !== "none");
    }
    if (filterAGN) {
      sources = sources.filter((s) => s.is_agn_candidate === true || s.object_class === "agn_candidate");
    }
    if (filterMwAgn) {
      sources = sources.filter((s) => (s.agn_confidence ?? 0) >= 0.3);
    }
    if (filterHighZ) {
      sources = sources.filter((s) =>
        s.object_class === "high_z_candidate" ||
        (s.z_phot_eazy != null && Number(s.z_phot_eazy) > 6 && s.z_phot_quality === "good")
      );
    }
    if (search) {
      const term = search.toLowerCase();
      sources = sources.filter((s) =>
        String(s.id).includes(term) ||
        (s.dropout_flag && s.dropout_flag.toLowerCase().includes(term)) ||
        (s.object_class && String(s.object_class).toLowerCase().includes(term))
      );
    }

    const sorted = [...sources].sort((a, b) => {
      const av = a[sortCol];
      const bv = b[sortCol];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      const cmp = Number(av) - Number(bv);
      if (isNaN(cmp)) return String(av).localeCompare(String(bv));
      return sortDir === "asc" ? cmp : -cmp;
    });

    return sorted;
  }, [sourcesSummary, sortCol, sortDir, search, filterNovel, filterDropout, filterAGN, filterMwAgn, filterHighZ]);

  const handleSort = (key: string) => {
    if (sortCol === key) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortCol(key);
      setSortDir("asc");
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <div className="w-8 h-8 border-2 border-gray-600 border-t-blue-400 rounded-full animate-spin mx-auto mb-3" />
          <p className="text-sm text-gray-500">Loading source catalog...</p>
        </div>
      </div>
    );
  }

  if (workspaceError) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center max-w-md">
          <p className="text-sm text-gray-400 mb-2">Cannot locate the workspace directory</p>
          <p className="text-xs text-red-400/70 font-mono break-all">{workspaceError}</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center max-w-md">
          <p className="text-sm text-gray-400 mb-2">No catalog available yet</p>
          <p className="text-xs text-gray-600 mb-4">
            Run the full pipeline to generate the source catalog.
          </p>
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

  if (!sourcesSummary) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500">
        <p className="text-sm">No catalog data. Run the pipeline first.</p>
      </div>
    );
  }

  const totalWidth = columns.reduce((s, c) => s + c.width, 0);
  const ROW_HEIGHT = 28;
  const HEADER_HEIGHT = 32;

  return (
    <div className="absolute inset-0 flex flex-col p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3 flex-shrink-0">
        <div>
          <h2 className="text-xl font-bold">Source Catalog</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            {sortedSources.length.toLocaleString()} of {sourcesSummary.n_sources.toLocaleString()} sources
            {sourcesSummary.filters.length > 0 && ` across ${sourcesSummary.filters.length} filters`}
          </p>
        </div>
        <button
          onClick={reload}
          className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-xs"
        >
          Reload
        </button>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3 mb-3 flex-shrink-0">
        <input
          type="text"
          placeholder="Search by ID..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="px-3 py-1.5 bg-gray-900 border border-gray-700 rounded text-sm w-48 focus:outline-none focus:border-blue-500"
        />
        <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer">
          <input
            type="checkbox"
            checked={filterNovel}
            onChange={(e) => setFilterNovel(e.target.checked)}
            className="rounded border-gray-600"
          />
          Novel only
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer">
          <input
            type="checkbox"
            checked={filterDropout}
            onChange={(e) => setFilterDropout(e.target.checked)}
            className="rounded border-gray-600"
          />
          Dropouts only
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer">
          <input
            type="checkbox"
            checked={filterAGN}
            onChange={(e) => setFilterAGN(e.target.checked)}
            className="rounded border-gray-600"
          />
          AGN candidates
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer">
          <input
            type="checkbox"
            checked={filterMwAgn}
            onChange={(e) => setFilterMwAgn(e.target.checked)}
            className="rounded border-gray-600"
          />
          MW AGN
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer">
          <input
            type="checkbox"
            checked={filterHighZ}
            onChange={(e) => setFilterHighZ(e.target.checked)}
            className="rounded border-gray-600"
          />
          High-z (z&gt;6)
        </label>
        <div className="flex-1" />
        <span className="text-xs text-gray-600">
          Click headers to sort
        </span>
      </div>

      {/* Table - uses flex-1 + min-h-0 to fill remaining space */}
      <div className="flex-1 min-h-0 bg-gray-900 border border-gray-800 rounded-lg flex flex-col overflow-hidden">
        {/* Sticky header */}
        <div className="flex-shrink-0 overflow-x-auto">
          <div
            className="flex border-b border-gray-700 bg-gray-900"
            style={{ minWidth: totalWidth }}
          >
            {columns.map((col) => (
              <div
                key={col.key}
                className={`flex-shrink-0 px-2 text-xs font-medium text-gray-400 select-none ${
                  col.sortable ? "cursor-pointer hover:text-white" : ""
                }`}
                style={{ width: col.width, height: HEADER_HEIGHT, lineHeight: `${HEADER_HEIGHT}px` }}
                onClick={() => col.sortable && handleSort(col.key)}
              >
                {col.label}
                {sortCol === col.key && (
                  <span className="ml-1 text-blue-400">
                    {sortDir === "asc" ? "\u2191" : "\u2193"}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 min-h-0 overflow-auto">
          {sortedSources.slice(0, 2000).map((source, rowIdx) => {
            const isAnomaly = source.anomaly_rank != null && source.anomaly_rank > 0 && source.anomaly_rank <= 50;
            return (
              <div
                key={source.id}
                data-testid="catalog-row"
                className={`flex border-b border-gray-800/50 hover:bg-gray-800/50 ${
                  isAnomaly ? "bg-blue-500/5 cursor-pointer" : ""
                } ${rowIdx % 2 === 0 ? "" : "bg-gray-900/50"}`}
                style={{ minWidth: totalWidth, height: ROW_HEIGHT }}
                onClick={() => { if (isAnomaly) navigate("/anomalies"); }}
              >
                {columns.map((col) => {
                  const val = source[col.key];
                  const formatted = col.format ? col.format(val) : val != null ? String(val) : "--";
                  const isHighlight =
                    (col.key === "novel" && val === true) ||
                    (col.key === "dropout_flag" && val && val !== "none") ||
                    (col.key === "red_excess" && val === true) ||
                    (col.key === "object_class" && val && val !== "galaxy" && val !== "ambiguous") ||
                    (col.key === "z_phot_quality" && val === "good") ||
                    (col.key === "agn_confidence" && val != null && Number(val) >= 0.3) ||
                    (col.key === "mw_class" && val != null && val !== "") ||
                    (col.key === "z_agree" && val === true);
                  return (
                    <div
                      key={col.key}
                      className={`flex-shrink-0 px-2 text-xs font-mono truncate ${
                        isHighlight ? "text-emerald-400" : "text-gray-300"
                      }`}
                      style={{ width: col.width, lineHeight: `${ROW_HEIGHT}px` }}
                      title={String(formatted)}
                    >
                      {formatted}
                    </div>
                  );
                })}
              </div>
            );
          })}
          {sortedSources.length > 2000 && (
            <div className="text-center py-3 text-xs text-gray-600">
              Showing first 2,000 of {sortedSources.length.toLocaleString()} results. Use filters to narrow down.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
