import { useState, useEffect, useRef } from "react";
import { useConfigStore } from "@/stores/configStore";
import { useWorkspace } from "@/hooks/useWorkspace";
import { useResultsStore } from "@/stores/resultsStore";
import { formatRA, formatDec } from "@/lib/formatters";
import { errorMessage } from "@/lib/errors";

interface SelectedSource {
  id: number;
  ra: number;
  dec: number;
  rank?: number;
  score?: number;
  novel?: boolean;
  dropout_flag?: string;
}

export function ViewerPage() {
  const config = useConfigStore((s) => s.config);
  const { candidatesReport, sourcesSummary, loadCandidates, loadSourcesSummary } = useResultsStore();
  const { workspace, error: workspaceError } = useWorkspace();
  const [selected, setSelected] = useState<SelectedSource | null>(null);
  const [overlay, setOverlay] = useState<"anomalies" | "all" | "none">("anomalies");
  const [aladinReady, setAladinReady] = useState(false);
  const [aladinError, setAladinError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const aladinRef = useRef<unknown>(null);

  // Load data
  useEffect(() => {
    if (!workspace) return;
    if (!candidatesReport) loadCandidates(workspace);
    if (!sourcesSummary) loadSourcesSummary(workspace);
  }, [workspace, candidatesReport, sourcesSummary, loadCandidates, loadSourcesSummary]);

  // Initialize Aladin Lite
  useEffect(() => {
    if (!containerRef.current) return;
    let mounted = true;

    setAladinReady(false);
    setAladinError(null);

    if (!Number.isFinite(config.center_ra) || !Number.isFinite(config.center_dec)) {
      setAladinError("No target coordinates: set Center RA / Dec on the Config page or pick a field preset.");
      return;
    }

    // Aladin's init promise never settles if e.g. the WASM fetch is blocked;
    // do not leave the spinner up forever.
    const watchdog = setTimeout(() => {
      if (mounted) setAladinError("Aladin Lite did not finish loading within 30 s (sky survey tiles need network access).");
    }, 30_000);

    const initAladin = async () => {
      try {
        const A = await import("aladin-lite");
        const AladinLib = A.default || A;

        if (!mounted || !containerRef.current) return;

        // Aladin Lite v3 requires WASM init before use. The WASM binary is
        // embedded in aladin.js as a data: URL and fetched, so the packaged
        // app's CSP must allow `connect-src data:` (see docs/BUILDING.md).
        if (AladinLib.init) {
          await AladinLib.init;
        }

        if (!mounted || !containerRef.current) return;

        const aladin = AladinLib.aladin(containerRef.current, {
          target: `${config.center_ra} ${config.center_dec}`,
          fov: (config.field_size_arcmin / 60) * 2,
          survey: "P/DSS2/color",
          showCooGrid: true,
          cooFrame: "J2000",
        });

        // The constructor swallows WebGL failures and only logs them; without a
        // view we would report "ready" over an empty box.
        const view = (aladin as unknown as { view?: { wasm?: unknown } }).view;
        if (!view || !view.wasm) {
          throw new Error("Aladin Lite could not create its WebGL2 view (the canvas is empty).");
        }

        aladinRef.current = aladin;

        // Click handler
        aladin.on("objectClicked", (object: { data?: Record<string, unknown> }) => {
          if (object && object.data) {
            setSelected({
              id: Number(object.data.id),
              ra: Number(object.data.ra),
              dec: Number(object.data.dec),
              rank: object.data.rank != null ? Number(object.data.rank) : undefined,
              score: object.data.score != null ? Number(object.data.score) : undefined,
              novel: object.data.novel === true,
              dropout_flag: object.data.dropout_flag ? String(object.data.dropout_flag) : undefined,
            });
          }
        });

        if (mounted) {
          clearTimeout(watchdog);
          setAladinReady(true);
        }
      } catch (err) {
        console.warn("Aladin Lite failed to initialize:", err);
        if (mounted) {
          clearTimeout(watchdog);
          setAladinError(errorMessage(err));
        }
      }
    };

    // Give DOM a moment to be ready
    const timer = setTimeout(initAladin, 200);
    return () => {
      mounted = false;
      clearTimeout(timer);
      clearTimeout(watchdog);
    };
  }, [config.center_ra, config.center_dec, config.field_size_arcmin]);

  // Update overlay markers when data or overlay mode changes
  useEffect(() => {
    if (!aladinReady) return;
    const aladin = aladinRef.current as {
      addCatalog: (cat: unknown) => void;
      removeLayers: () => void;
    } | null;
    if (!aladin) return;

    try { aladin.removeLayers(); } catch { /* ok */ }

    if (overlay === "none") return;

    const addOverlay = async () => {
      try {
        const A = await import("aladin-lite");
        const AladinLib = A.default || A;
        if (AladinLib.init) await AladinLib.init;

        let sources: { ra: number; dec: number; id: number; rank?: number | null; score?: number | null; novel?: boolean | null; dropout_flag?: string | null }[] = [];

        if (overlay === "anomalies" && candidatesReport) {
          sources = candidatesReport.candidates.map((c) => ({
            ra: c.ra, dec: c.dec, id: c.id,
            rank: c.rank, score: c.anomaly_score,
            novel: c.novel, dropout_flag: c.dropout_flag,
          }));
        } else if (overlay === "all" && sourcesSummary) {
          const sorted = [...sourcesSummary.sources]
            .filter((s) => s.anomaly_rank != null && s.anomaly_rank > 0)
            .sort((a, b) => (a.anomaly_rank || 99999) - (b.anomaly_rank || 99999))
            .slice(0, 2000);
          sources = sorted.map((s) => ({
            ra: s.ra, dec: s.dec, id: s.id,
            rank: s.anomaly_rank, score: s.anomaly_score,
            novel: s.novel, dropout_flag: s.dropout_flag,
          }));
        }

        if (sources.length === 0) return;

        const cat = AladinLib.catalog({
          name: overlay === "anomalies" ? "Anomaly Candidates" : "All Sources",
          sourceSize: overlay === "anomalies" ? 14 : 8,
          color: overlay === "anomalies" ? "#ef4444" : "#3b82f6",
          shape: "circle",
          onClick: "showPopup",
        });

        for (const src of sources) {
          cat.addSources([
            AladinLib.source(src.ra, src.dec, {
              name: `Source ${src.id}`,
              id: src.id, ra: src.ra, dec: src.dec,
              rank: src.rank, score: src.score,
              novel: src.novel, dropout_flag: src.dropout_flag,
            }),
          ]);
        }

        aladin.addCatalog(cat);
      } catch (err) {
        console.warn("Failed to add overlay:", err);
      }
    };

    addOverlay();
  }, [aladinReady, overlay, candidatesReport, sourcesSummary]);

  // Scatter fallback data
  const scatterSources = overlay === "anomalies"
    ? (candidatesReport?.candidates || [])
    : overlay === "all"
      ? (sourcesSummary?.sources || []).filter((s) => s.anomaly_rank != null && s.anomaly_rank > 0).slice(0, 2000)
      : [];

  return (
    <div className="absolute inset-0 flex flex-col p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3 flex-shrink-0">
        <div>
          <h2 className="text-xl font-bold">Sky Viewer</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            {config.target_field} &mdash; RA {config.center_ra.toFixed(3)}, Dec {config.center_dec.toFixed(3)}
          </p>
        </div>
        <div className="flex gap-2">
          <select
            data-testid="overlay-select"
            value={overlay}
            onChange={(e) => setOverlay(e.target.value as typeof overlay)}
            className="px-3 py-1.5 bg-gray-900 border border-gray-700 rounded text-xs"
          >
            <option value="anomalies">Anomalies ({candidatesReport?.candidates.length || 0})</option>
            <option value="all">All Sources (top 2000)</option>
            <option value="none">No Overlay</option>
          </select>
        </div>
      </div>

      {workspaceError && (
        <div className="bg-red-500/5 border border-red-500/20 rounded-lg p-3 mb-3 text-xs text-red-400 font-mono break-all flex-shrink-0">
          Cannot locate the workspace directory: {workspaceError}
        </div>
      )}

      {/* Main content */}
      <div className="flex-1 min-h-0 flex gap-3">
        {/* Map / Scatter area */}
        <div className="flex-1 bg-gray-900 border border-gray-800 rounded-lg overflow-hidden relative">
          {/* Aladin container - always rendered so it can initialize */}
          <div
            ref={containerRef}
            data-testid="aladin-container"
            className="w-full h-full"
            style={{ display: aladinError ? "none" : "block" }}
          />

          {/* Loading overlay - shown while Aladin is initializing */}
          {!aladinReady && !aladinError && (
            <div className="absolute inset-0 flex items-center justify-center bg-gray-900 z-10">
              <div className="text-center">
                <div className="w-8 h-8 border-2 border-gray-600 border-t-blue-400 rounded-full animate-spin mx-auto mb-3" />
                <p className="text-sm text-gray-500">Loading sky viewer...</p>
              </div>
            </div>
          )}

          {/* Aladin failed: say why, and still plot the candidates if there are any */}
          {aladinError && (
            <>
              <div
                data-testid="viewer-error"
                role="alert"
                className="absolute top-2 left-2 right-2 z-10 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200"
              >
                <div className="font-medium">Sky viewer could not start</div>
                <div className="mt-0.5 break-words text-red-200/80 font-mono">{aladinError}</div>
                <div className="mt-1 text-red-200/60">
                  Survey tiles need network access to CDS (alasky.cds.unistra.fr) and a WebGL2-capable webview.
                  {scatterSources.length > 0
                    ? " Showing the candidate positions as a plain RA/Dec scatter instead."
                    : " Load a workspace with pipeline results to see candidate positions."}
                </div>
              </div>
              {scatterSources.length > 0 ? (
                <ScatterFallback
                  sources={scatterSources}
                  centerRa={config.center_ra}
                  centerDec={config.center_dec}
                  onSelect={setSelected}
                  selectedId={selected?.id}
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-xs text-gray-600">
                  No sources to plot for {config.target_field}.
                </div>
              )}
            </>
          )}
        </div>

        {/* Detail sidebar */}
        <div className="w-64 bg-gray-900 border border-gray-800 rounded-lg p-3 overflow-y-auto flex-shrink-0">
          {selected ? (
            <div className="space-y-3">
              <div>
                <h3 className="text-sm font-bold mb-1">Source {selected.id}</h3>
                {selected.rank && (
                  <p className="text-xs text-blue-400">Anomaly Rank #{selected.rank}</p>
                )}
              </div>

              <div className="space-y-1 text-xs">
                <div className="flex justify-between">
                  <span className="text-gray-500">RA</span>
                  <span className="font-mono">{formatRA(selected.ra)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">Dec</span>
                  <span className="font-mono">{formatDec(selected.dec)}</span>
                </div>
                {selected.score != null && (
                  <div className="flex justify-between">
                    <span className="text-gray-500">Score</span>
                    <span className="font-mono">{selected.score.toFixed(4)}</span>
                  </div>
                )}
              </div>

              <div className="flex flex-wrap gap-1">
                {selected.novel && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400">
                    NOVEL
                  </span>
                )}
                {selected.dropout_flag && selected.dropout_flag !== "none" && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-400">
                    {selected.dropout_flag}
                  </span>
                )}
              </div>

              {/* Cutout preview */}
              {workspace && selected.rank && selected.rank <= 50 && (
                <div>
                  <p className="text-xs text-gray-500 mb-1">Detection Cutout</p>
                  <div className="w-full aspect-square bg-gray-800 rounded overflow-hidden">
                    <CutoutPreview workspace={workspace} sourceId={selected.id} />
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="text-center text-gray-600 py-8">
              <svg className="w-8 h-8 mx-auto mb-2 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 15l-2 5L9 9l11 4-5 2zm0 0l5 5M7.188 2.239l.777 2.897M5.136 7.965l-2.898-.777M13.95 4.05l-2.122 2.122m-5.657 5.656l-2.12 2.122" />
              </svg>
              <p className="text-xs">Click a source on the map to see details</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** Canvas-based RA/Dec scatter plot fallback when Aladin can't load */
function ScatterFallback({
  sources,
  centerRa,
  centerDec,
  onSelect,
  selectedId,
}: {
  sources: { id: number; ra: number; dec: number; anomaly_score?: number | null; novel?: boolean | null; dropout_flag?: string | null; rank?: number; anomaly_rank?: number | null }[];
  centerRa: number;
  centerDec: number;
  onSelect: (s: SelectedSource) => void;
  selectedId?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 600, h: 400 });

  // Measure container
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const obs = new ResizeObserver((entries) => {
      for (const e of entries) {
        setSize({ w: e.contentRect.width, h: e.contentRect.height });
      }
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  // Draw
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || sources.length === 0) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    canvas.width = size.w * 2; // retina
    canvas.height = size.h * 2;
    ctx.scale(2, 2);

    // Background
    ctx.fillStyle = "#111827";
    ctx.fillRect(0, 0, size.w, size.h);

    // Compute bounds
    const ras = sources.map((s) => s.ra);
    const decs = sources.map((s) => s.dec);
    const raMin = Math.min(...ras) - 0.002;
    const raMax = Math.max(...ras) + 0.002;
    const decMin = Math.min(...decs) - 0.002;
    const decMax = Math.max(...decs) + 0.002;

    const margin = 30;
    const pw = size.w - margin * 2;
    const ph = size.h - margin * 2;

    const toX = (ra: number) => margin + ((raMax - ra) / (raMax - raMin)) * pw; // RA inverted
    const toY = (dec: number) => margin + ((decMax - dec) / (decMax - decMin)) * ph;

    // Grid
    ctx.strokeStyle = "#1f2937";
    ctx.lineWidth = 0.5;
    for (let i = 0; i <= 4; i++) {
      const x = margin + (i / 4) * pw;
      const y = margin + (i / 4) * ph;
      ctx.beginPath(); ctx.moveTo(x, margin); ctx.lineTo(x, margin + ph); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(margin, y); ctx.lineTo(margin + pw, y); ctx.stroke();
    }

    // Axis labels
    ctx.fillStyle = "#6b7280";
    ctx.font = "10px monospace";
    ctx.textAlign = "center";
    ctx.fillText("RA (deg)", size.w / 2, size.h - 4);
    ctx.save();
    ctx.translate(10, size.h / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText("Dec (deg)", 0, 0);
    ctx.restore();

    // Draw sources
    for (const src of sources) {
      const x = toX(src.ra);
      const y = toY(src.dec);
      const score = src.anomaly_score ?? 0;
      const r = 3 + score * 4;

      if (src.novel) {
        ctx.fillStyle = "#10b981";
      } else if (src.dropout_flag && src.dropout_flag !== "none") {
        ctx.fillStyle = "#a855f7";
      } else {
        const intensity = Math.round(100 + score * 155);
        ctx.fillStyle = `rgb(${intensity}, ${Math.round(intensity * 0.4)}, ${Math.round(intensity * 0.4)})`;
      }

      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();

      if (src.id === selectedId) {
        ctx.strokeStyle = "#3b82f6";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(x, y, r + 3, 0, Math.PI * 2);
        ctx.stroke();
      }
    }

    // Title
    ctx.fillStyle = "#9ca3af";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(`${sources.length} sources`, margin, margin - 8);
  }, [sources, size, selectedId, centerRa, centerDec]);

  // Click handler
  const handleClick = (e: React.MouseEvent) => {
    const canvas = canvasRef.current;
    if (!canvas || sources.length === 0) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const ras = sources.map((s) => s.ra);
    const decs = sources.map((s) => s.dec);
    const raMin = Math.min(...ras) - 0.002;
    const raMax = Math.max(...ras) + 0.002;
    const decMin = Math.min(...decs) - 0.002;
    const decMax = Math.max(...decs) + 0.002;
    const margin = 30;
    const pw = size.w - margin * 2;
    const ph = size.h - margin * 2;
    const toX = (ra: number) => margin + ((raMax - ra) / (raMax - raMin)) * pw;
    const toY = (dec: number) => margin + ((decMax - dec) / (decMax - decMin)) * ph;

    let closest: typeof sources[0] | null = null;
    let minDist = 15;
    for (const src of sources) {
      const dx = toX(src.ra) - mx;
      const dy = toY(src.dec) - my;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < minDist) {
        minDist = dist;
        closest = src;
      }
    }

    if (closest) {
      onSelect({
        id: closest.id,
        ra: closest.ra,
        dec: closest.dec,
        rank: closest.rank ?? (closest.anomaly_rank != null ? Number(closest.anomaly_rank) : undefined),
        score: closest.anomaly_score != null ? Number(closest.anomaly_score) : undefined,
        novel: closest.novel === true,
        dropout_flag: closest.dropout_flag ? String(closest.dropout_flag) : undefined,
      });
    }
  };

  return (
    <div ref={containerRef} className="w-full h-full relative">
      <canvas
        ref={canvasRef}
        className="w-full h-full cursor-crosshair"
        onClick={handleClick}
      />
      <div className="absolute top-2 right-2 text-[10px] text-gray-600 bg-gray-900/80 px-2 py-1 rounded">
        Scatter plot fallback (Aladin unavailable)
      </div>
    </div>
  );
}

function CutoutPreview({ workspace, sourceId }: { workspace: string; sourceId: number }) {
  const loadImage = useResultsStore((s) => s.loadImage);
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    const path = `cutouts/src${String(sourceId).padStart(6, "0")}_detection.png`;
    loadImage(workspace, path).then(setSrc).catch(() => {});
  }, [workspace, sourceId, loadImage]);

  if (!src) {
    return (
      <div className="w-full h-full flex items-center justify-center text-gray-600 text-xs">
        Loading...
      </div>
    );
  }

  return <img src={src} alt={`Source ${sourceId}`} className="w-full h-full object-contain" />;
}
