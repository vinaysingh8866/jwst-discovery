import { useEffect, useRef, useMemo } from "react";
import { useComparisonStore, type FieldSummary } from "@/stores/comparisonStore";
import { MW_CLASS_LABELS, MW_CLASS_COLORS } from "@/lib/constants";

import Plotly from "plotly.js-basic-dist-min";

function FieldOverview({ fields }: { fields: FieldSummary[] }) {
  return (
    <section className="bg-gray-900 rounded-lg border border-gray-800 p-4">
      <h3 className="text-sm font-medium mb-3">Field Overview</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-gray-700 text-gray-400">
              <th className="text-left px-2 py-1.5">Field</th>
              <th className="text-right px-2 py-1.5">Sources</th>
              <th className="text-right px-2 py-1.5">Anomalies</th>
              <th className="text-right px-2 py-1.5">Novel</th>
              <th className="text-right px-2 py-1.5">Novel %</th>
              <th className="text-right px-2 py-1.5">Gold</th>
              <th className="text-right px-2 py-1.5">Silver</th>
              <th className="text-right px-2 py-1.5">Bronze</th>
              <th className="text-right px-2 py-1.5">AGN</th>
              <th className="text-right px-2 py-1.5">High-z</th>
              <th className="text-right px-2 py-1.5">Dropouts</th>
              <th className="text-right px-2 py-1.5">Filters</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((f) => (
              <tr key={f.field} className="border-b border-gray-800/50 hover:bg-gray-800/50">
                <td className="px-2 py-1.5 font-medium text-gray-200">{f.field}</td>
                <td className="text-right px-2 py-1.5 font-mono text-gray-300">{f.n_sources.toLocaleString()}</td>
                <td className="text-right px-2 py-1.5 font-mono text-gray-300">{f.n_anomalies}</td>
                <td className="text-right px-2 py-1.5 font-mono text-emerald-400">{f.n_novel}</td>
                <td className="text-right px-2 py-1.5 font-mono text-gray-400">
                  {f.n_sources > 0 ? ((f.n_novel / f.n_sources) * 100).toFixed(1) : "0"}%
                </td>
                <td className="text-right px-2 py-1.5 font-mono text-yellow-400">{f.tier_counts.gold}</td>
                <td className="text-right px-2 py-1.5 font-mono text-gray-300">{f.tier_counts.silver}</td>
                <td className="text-right px-2 py-1.5 font-mono text-orange-400">{f.tier_counts.bronze}</td>
                <td className="text-right px-2 py-1.5 font-mono text-purple-400">{f.n_agn}</td>
                <td className="text-right px-2 py-1.5 font-mono text-blue-400">{f.n_highz}</td>
                <td className="text-right px-2 py-1.5 font-mono text-gray-400">{f.n_dropouts}</td>
                <td className="text-right px-2 py-1.5 font-mono text-gray-500">{f.filters.length}</td>
              </tr>
            ))}
            {/* Totals row */}
            <tr className="border-t border-gray-600 font-medium">
              <td className="px-2 py-1.5 text-gray-400">Total</td>
              <td className="text-right px-2 py-1.5 font-mono text-gray-200">
                {fields.reduce((s, f) => s + f.n_sources, 0).toLocaleString()}
              </td>
              <td className="text-right px-2 py-1.5 font-mono text-gray-200">
                {fields.reduce((s, f) => s + f.n_anomalies, 0)}
              </td>
              <td className="text-right px-2 py-1.5 font-mono text-emerald-400">
                {fields.reduce((s, f) => s + f.n_novel, 0)}
              </td>
              <td className="text-right px-2 py-1.5" />
              <td className="text-right px-2 py-1.5 font-mono text-yellow-400">
                {fields.reduce((s, f) => s + f.tier_counts.gold, 0)}
              </td>
              <td className="text-right px-2 py-1.5 font-mono text-gray-300">
                {fields.reduce((s, f) => s + f.tier_counts.silver, 0)}
              </td>
              <td className="text-right px-2 py-1.5 font-mono text-orange-400">
                {fields.reduce((s, f) => s + f.tier_counts.bronze, 0)}
              </td>
              <td className="text-right px-2 py-1.5 font-mono text-purple-400">
                {fields.reduce((s, f) => s + f.n_agn, 0)}
              </td>
              <td className="text-right px-2 py-1.5 font-mono text-blue-400">
                {fields.reduce((s, f) => s + f.n_highz, 0)}
              </td>
              <td className="text-right px-2 py-1.5 font-mono text-gray-400">
                {fields.reduce((s, f) => s + f.n_dropouts, 0)}
              </td>
              <td className="text-right px-2 py-1.5" />
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}

function AgnCensus({ fields }: { fields: FieldSummary[] }) {
  const chartRef = useRef<HTMLDivElement>(null);

  // Collect all MW classes across fields
  const allClasses = useMemo(() => {
    const classes = new Set<string>();
    fields.forEach((f) => Object.keys(f.mw_class_counts).forEach((c) => classes.add(c)));
    return Array.from(classes).sort();
  }, [fields]);

  // Top AGN across all fields
  const topAgn = useMemo(() => {
    const all = fields.flatMap((f) =>
      f.candidates
        .filter((c) => (c.agn_confidence ?? 0) >= 0.3)
        .map((c) => ({ ...c, _field: f.field })),
    );
    all.sort((a, b) => (b.agn_confidence ?? 0) - (a.agn_confidence ?? 0));
    return all.slice(0, 10);
  }, [fields]);

  useEffect(() => {
    if (!chartRef.current || fields.length === 0) return;

    const fieldNames = fields.map((f) => f.field);
    const traces = allClasses.map((cls) => ({
      x: fieldNames,
      y: fields.map((f) => f.mw_class_counts[cls] || 0),
      name: MW_CLASS_LABELS[cls] || cls,
      type: "bar" as const,
    }));

    Plotly.newPlot(
      chartRef.current,
      traces,
      {
        barmode: "stack",
        title: { text: "AGN by MW Class per Field", font: { color: "#ccc", size: 13 } },
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        font: { color: "#9ca3af", size: 10 },
        xaxis: { color: "#9ca3af" },
        yaxis: { color: "#9ca3af", title: "Count" },
        margin: { t: 40, b: 40, l: 50, r: 20 },
        legend: { orientation: "h", y: -0.2 },
        height: 280,
      },
      { responsive: true, displayModeBar: false },
    );
  }, [fields, allClasses]);

  return (
    <section className="bg-gray-900 rounded-lg border border-gray-800 p-4">
      <h3 className="text-sm font-medium mb-3">AGN Census</h3>
      <div className="grid grid-cols-2 gap-4">
        <div ref={chartRef} />
        <div>
          <h4 className="text-xs text-gray-400 mb-2">Top 10 AGN Candidates (All Fields)</h4>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-700 text-gray-500">
                <th className="text-left px-1 py-1">Field</th>
                <th className="text-right px-1 py-1">ID</th>
                <th className="text-right px-1 py-1">AGN Conf</th>
                <th className="text-left px-1 py-1">MW Class</th>
                <th className="text-right px-1 py-1">z_phot</th>
              </tr>
            </thead>
            <tbody>
              {topAgn.map((c, i) => (
                <tr key={i} className="border-b border-gray-800/50">
                  <td className="px-1 py-0.5 text-gray-400">{(c as Record<string, unknown>)._field as string}</td>
                  <td className="text-right px-1 py-0.5 font-mono text-gray-300">{c.id}</td>
                  <td className="text-right px-1 py-0.5 font-mono text-purple-400">
                    {(c.agn_confidence ?? 0).toFixed(2)}
                  </td>
                  <td className="px-1 py-0.5">
                    {c.mw_class && (
                      <span className={`px-1 py-0.5 rounded text-[9px] ${MW_CLASS_COLORS[c.mw_class] || "bg-gray-700 text-gray-300"}`}>
                        {MW_CLASS_LABELS[c.mw_class] || c.mw_class}
                      </span>
                    )}
                  </td>
                  <td className="text-right px-1 py-0.5 font-mono text-gray-400">
                    {c.z_phot_eazy != null ? Number(c.z_phot_eazy).toFixed(2) : "--"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function HighZCandidates({ fields }: { fields: FieldSummary[] }) {
  const chartRef = useRef<HTMLDivElement>(null);

  const highzSources = useMemo(() => {
    return fields.flatMap((f) =>
      f.candidates
        .filter(
          (c) =>
            c.z_phot_eazy != null &&
            Number(c.z_phot_eazy) > 6 &&
            c.z_phot_quality === "good",
        )
        .map((c) => ({ ...c, _field: f.field })),
    );
  }, [fields]);

  useEffect(() => {
    if (!chartRef.current || fields.length === 0) return;

    const colors = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6"];
    const traces = fields.map((f, idx) => {
      const candidates = f.candidates.filter(
        (c) => c.z_phot_eazy != null && Number(c.z_phot_eazy) > 0,
      );
      return {
        x: candidates.map((c) => Number(c.z_phot_eazy)),
        y: candidates.map((c) => c.anomaly_score),
        mode: "markers" as const,
        type: "scatter" as const,
        name: f.field,
        marker: {
          color: colors[idx % colors.length],
          size: 4,
          opacity: 0.6,
        },
      };
    });

    Plotly.newPlot(
      chartRef.current,
      traces,
      {
        title: { text: "Photo-z vs Anomaly Score", font: { color: "#ccc", size: 13 } },
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        font: { color: "#9ca3af", size: 10 },
        xaxis: { title: "z_phot (EAZY)", color: "#9ca3af" },
        yaxis: { title: "Anomaly Score", color: "#9ca3af" },
        margin: { t: 40, b: 50, l: 50, r: 20 },
        legend: { orientation: "h", y: -0.25 },
        height: 300,
        shapes: [
          {
            type: "line",
            x0: 6,
            x1: 6,
            y0: 0,
            y1: 1,
            line: { dash: "dash", color: "#ef4444", width: 1 },
          },
        ],
      },
      { responsive: true, displayModeBar: false },
    );
  }, [fields]);

  return (
    <section className="bg-gray-900 rounded-lg border border-gray-800 p-4">
      <h3 className="text-sm font-medium mb-3">
        High-z Candidates (z &gt; 6)
        <span className="ml-2 text-xs text-gray-500">{highzSources.length} total</span>
      </h3>
      <div className="grid grid-cols-2 gap-4">
        <div ref={chartRef} />
        <div className="max-h-[300px] overflow-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-gray-900">
              <tr className="border-b border-gray-700 text-gray-500">
                <th className="text-left px-1 py-1">Field</th>
                <th className="text-right px-1 py-1">ID</th>
                <th className="text-right px-1 py-1">z_phot</th>
                <th className="text-center px-1 py-1">z Agree</th>
                <th className="text-left px-1 py-1">z Source</th>
                <th className="text-right px-1 py-1">Score</th>
              </tr>
            </thead>
            <tbody>
              {highzSources
                .sort((a, b) => Number(b.z_phot_eazy) - Number(a.z_phot_eazy))
                .map((c, i) => (
                  <tr
                    key={i}
                    className={`border-b border-gray-800/50 ${
                      c.z_catastrophic === true ? "bg-red-500/10" : ""
                    }`}
                  >
                    <td className="px-1 py-0.5 text-gray-400">{(c as Record<string, unknown>)._field as string}</td>
                    <td className="text-right px-1 py-0.5 font-mono text-gray-300">{c.id}</td>
                    <td className="text-right px-1 py-0.5 font-mono text-blue-400">
                      {Number(c.z_phot_eazy).toFixed(2)}
                    </td>
                    <td className="text-center px-1 py-0.5">
                      {c.z_agree === true ? (
                        <span className="text-emerald-400">Yes</span>
                      ) : c.z_agree === false ? (
                        <span className="text-red-400">No</span>
                      ) : (
                        <span className="text-gray-600">--</span>
                      )}
                    </td>
                    <td className="px-1 py-0.5 text-gray-500">{c.z_source || "--"}</td>
                    <td className="text-right px-1 py-0.5 font-mono text-gray-400">
                      {c.anomaly_score.toFixed(3)}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function CrossFieldOverlap({ fields }: { fields: FieldSummary[] }) {
  // MW class heatmap data
  const allClasses = useMemo(() => {
    const classes = new Set<string>();
    fields.forEach((f) => Object.keys(f.mw_class_counts).forEach((c) => classes.add(c)));
    return Array.from(classes).sort();
  }, [fields]);

  // Dropout distribution
  const dropoutDist = useMemo(() => {
    const dist: Record<string, Record<string, number>> = {};
    for (const f of fields) {
      dist[f.field] = {};
      for (const c of f.candidates) {
        if (c.dropout_flag && c.dropout_flag !== "none") {
          dist[f.field][c.dropout_flag] = (dist[f.field][c.dropout_flag] || 0) + 1;
        }
      }
    }
    return dist;
  }, [fields]);

  return (
    <section className="bg-gray-900 rounded-lg border border-gray-800 p-4">
      <h3 className="text-sm font-medium mb-3">Cross-field Comparison</h3>
      <div className="grid grid-cols-2 gap-4">
        {/* MW Class Heatmap */}
        <div>
          <h4 className="text-xs text-gray-400 mb-2">MW Classification Distribution</h4>
          {allClasses.length > 0 ? (
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-700 text-gray-500">
                  <th className="text-left px-1 py-1">Class</th>
                  {fields.map((f) => (
                    <th key={f.field} className="text-right px-1 py-1">{f.field}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {allClasses.map((cls) => (
                  <tr key={cls} className="border-b border-gray-800/50">
                    <td className="px-1 py-0.5">
                      <span className={`px-1 py-0.5 rounded text-[9px] ${MW_CLASS_COLORS[cls] || "bg-gray-700 text-gray-300"}`}>
                        {MW_CLASS_LABELS[cls] || cls}
                      </span>
                    </td>
                    {fields.map((f) => (
                      <td key={f.field} className="text-right px-1 py-0.5 font-mono text-gray-400">
                        {f.mw_class_counts[cls] || 0}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-xs text-gray-600">No MW classifications available. Run MW crossmatch on fields first.</p>
          )}
        </div>
        {/* Dropout Distribution */}
        <div>
          <h4 className="text-xs text-gray-400 mb-2">Dropout Distribution</h4>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-700 text-gray-500">
                <th className="text-left px-1 py-1">Field</th>
                <th className="text-right px-1 py-1">Dropouts</th>
                <th className="text-left px-2 py-1">Breakdown</th>
              </tr>
            </thead>
            <tbody>
              {fields.map((f) => {
                const dd = dropoutDist[f.field] || {};
                return (
                  <tr key={f.field} className="border-b border-gray-800/50">
                    <td className="px-1 py-0.5 text-gray-400">{f.field}</td>
                    <td className="text-right px-1 py-0.5 font-mono text-gray-300">{f.n_dropouts}</td>
                    <td className="px-2 py-0.5 text-gray-500">
                      {Object.entries(dd)
                        .sort((a, b) => b[1] - a[1])
                        .map(([flag, count]) => `${flag}:${count}`)
                        .join(", ") || "--"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <h4 className="text-xs text-gray-400 mt-4 mb-2">Novel Rate Comparison</h4>
          <div className="space-y-1">
            {fields.map((f) => {
              const pct = f.n_sources > 0 ? (f.n_novel / f.n_sources) * 100 : 0;
              return (
                <div key={f.field} className="flex items-center gap-2">
                  <span className="text-xs text-gray-400 w-28 truncate">{f.field}</span>
                  <div className="flex-1 h-3 bg-gray-800 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-emerald-500/50 rounded-full"
                      style={{ width: `${Math.min(pct, 100)}%` }}
                    />
                  </div>
                  <span className="text-xs font-mono text-gray-500 w-12 text-right">
                    {pct.toFixed(1)}%
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}

export function ComparisonPage() {
  const { fields, loading, error, loadAllFields } = useComparisonStore();

  useEffect(() => {
    if (fields.length === 0 && !loading) {
      loadAllFields();
    }
  }, [fields.length, loading, loadAllFields]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <div className="w-8 h-8 border-2 border-gray-600 border-t-blue-400 rounded-full animate-spin mx-auto mb-3" />
          <p className="text-sm text-gray-500">Loading field data...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center max-w-md">
          <p className="text-sm text-gray-400 mb-2">Failed to load field data</p>
          <p className="text-xs text-gray-600 mb-4">{error}</p>
          <button
            onClick={loadAllFields}
            className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-xs"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (fields.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500">
        <div className="text-center">
          <p className="text-sm">No processed fields found.</p>
          <p className="text-xs text-gray-600 mt-1">
            Run the pipeline on at least one field to see cross-field comparison.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="absolute inset-0 overflow-auto p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">Cross-field Comparison</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            {fields.length} fields, {fields.reduce((s, f) => s + f.n_sources, 0).toLocaleString()} total sources
          </p>
        </div>
        <button
          onClick={loadAllFields}
          className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-xs"
        >
          Reload
        </button>
      </div>

      <FieldOverview fields={fields} />
      <AgnCensus fields={fields} />
      <HighZCandidates fields={fields} />
      <CrossFieldOverlap fields={fields} />
    </div>
  );
}
