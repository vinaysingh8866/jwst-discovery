import { useEffect, useMemo, useRef } from "react";
import Plotly from "plotly.js-basic-dist-min";
import type { AnomalyCandidate } from "@/stores/resultsStore";
import { PIVOT_WAVELENGTH_UM } from "@/lib/constants";

/** Interactive Plotly SED for a single candidate: NIRCam photometry, limits and MW overlays. */
export function InteractiveSED({
  candidate,
  filters,
}: {
  candidate: AnomalyCandidate;
  filters: string[];
}) {
  const { traces, layout } = useMemo(() => {
    const solidWl: number[] = [], solidFlux: number[] = [], solidErr: number[] = [], solidText: string[] = [];
    const margWl: number[] = [], margFlux: number[] = [], margErr: number[] = [], margText: string[] = [];
    const ndWl: number[] = [], ndFlux: number[] = [], ndText: string[] = [];

    for (const filt of filters) {
      const wl = PIVOT_WAVELENGTH_UM[filt];
      if (!wl) continue;
      const flux = candidate[`flux_${filt}`] as number | null | undefined;
      const fluxerr = candidate[`fluxerr_${filt}`] as number | null | undefined;
      const sn = candidate[`sn_${filt}`] as number | null | undefined;
      const mag = candidate[`mag_${filt}`] as number | null | undefined;

      const hasFlux = flux != null && isFinite(flux) && flux > 0;
      const snVal = sn != null && isFinite(sn) ? sn : 0;
      const errVal = fluxerr != null && isFinite(fluxerr) ? fluxerr : 0;
      const magStr = mag != null && isFinite(mag) ? `${mag.toFixed(2)} AB` : "—";

      if (hasFlux && snVal >= 5) {
        solidWl.push(wl);
        solidFlux.push(flux!);
        solidErr.push(errVal);
        solidText.push(`${filt}  ${wl.toFixed(3)} \u00b5m<br>Flux: ${flux!.toFixed(3)} \u00b5Jy<br>S/N: ${snVal.toFixed(1)}<br>Mag: ${magStr}`);
      } else if (hasFlux && snVal >= 3) {
        margWl.push(wl);
        margFlux.push(flux!);
        margErr.push(errVal);
        margText.push(`${filt}  ${wl.toFixed(3)} \u00b5m<br>Flux: ${flux!.toFixed(3)} \u00b5Jy (marginal)<br>S/N: ${snVal.toFixed(1)}<br>Mag: ${magStr}`);
      } else {
        const upperLimit = errVal > 0 ? 2 * errVal : undefined;
        if (upperLimit) {
          ndWl.push(wl);
          ndFlux.push(upperLimit);
          ndText.push(`${filt}  ${wl.toFixed(3)} \u00b5m<br>2\u03c3 upper limit: ${upperLimit.toFixed(3)} \u00b5Jy<br>S/N: ${snVal.toFixed(1)}`);
        }
      }
    }

    const plotTraces: Array<Record<string, unknown>> = [];

    if (solidWl.length > 0) {
      plotTraces.push({
        x: solidWl,
        y: solidFlux,
        error_y: {
          type: "data" as const,
          array: solidErr,
          visible: true,
          color: "#60a5fa",
          thickness: 1,
        },
        mode: "lines+markers" as const,
        type: "scatter" as const,
        name: "S/N \u2265 5",
        marker: { color: "#60a5fa", size: 7, symbol: "circle" },
        line: { color: "#60a5fa", width: 1.5 },
        text: solidText,
        hoverinfo: "text" as const,
      });
    }

    if (margWl.length > 0) {
      plotTraces.push({
        x: margWl,
        y: margFlux,
        error_y: {
          type: "data" as const,
          array: margErr,
          visible: true,
          color: "#fbbf24",
          thickness: 1,
        },
        mode: "markers" as const,
        type: "scatter" as const,
        name: "3 \u2264 S/N < 5",
        marker: {
          color: "rgba(0,0,0,0)",
          size: 7,
          symbol: "circle",
          line: { color: "#fbbf24", width: 1.5 },
        },
        text: margText,
        hoverinfo: "text" as const,
      });
    }

    if (ndWl.length > 0) {
      plotTraces.push({
        x: ndWl,
        y: ndFlux,
        mode: "markers" as const,
        type: "scatter" as const,
        name: "Upper limit",
        marker: { color: "#6b7280", size: 8, symbol: "triangle-down" },
        text: ndText,
        hoverinfo: "text" as const,
      });
    }

    // Multi-wavelength overlay (Spitzer / WISE)
    const mwPhot = candidate.mw_photometry;
    if (mwPhot && mwPhot.length > 0) {
      const grouped: Record<string, { wl: number[]; fl: number[]; text: string[] }> = {};
      for (const pt of mwPhot) {
        if (!grouped[pt.instrument]) grouped[pt.instrument] = { wl: [], fl: [], text: [] };
        grouped[pt.instrument].wl.push(pt.wavelength_um);
        grouped[pt.instrument].fl.push(pt.flux_ujy);
        grouped[pt.instrument].text.push(
          `${pt.name}  ${pt.wavelength_um.toFixed(3)} \u00b5m<br>Flux: ${pt.flux_ujy.toFixed(2)} \u00b5Jy${pt.mag_vega != null ? `<br>Mag: ${pt.mag_vega.toFixed(2)} Vega` : ""}`
        );
      }
      const mwStyles: Record<string, { color: string; symbol: string; label: string }> = {
        spitzer: { color: "#16a34a", symbol: "square", label: "Spitzer IRAC" },
        wise: { color: "#ea580c", symbol: "diamond", label: "CatWISE" },
      };
      for (const [inst, data] of Object.entries(grouped)) {
        const s = mwStyles[inst] || { color: "#a855f7", symbol: "star", label: inst };
        plotTraces.push({
          x: data.wl, y: data.fl,
          mode: "markers" as const, type: "scatter" as const,
          name: s.label,
          marker: { color: s.color, size: 9, symbol: s.symbol, line: { color: "white", width: 1 } },
          text: data.text, hoverinfo: "text" as const,
        });
      }
    }

    const plotLayout: Record<string, unknown> = {
      xaxis: {
        title: { text: "Wavelength (\u00b5m)", font: { size: 11, color: "#9ca3af" } },
        color: "#6b7280",
        gridcolor: "#374151",
        zerolinecolor: "#374151",
        range: [0.5, 5.5],
      },
      yaxis: {
        title: { text: "Flux (\u00b5Jy)", font: { size: 11, color: "#9ca3af" } },
        type: "log",
        color: "#6b7280",
        gridcolor: "#374151",
        zerolinecolor: "#374151",
      },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "#1f2937",
      margin: { l: 50, r: 10, t: 10, b: 40 },
      showlegend: true,
      legend: {
        x: 1,
        xanchor: "right",
        y: 1,
        bgcolor: "rgba(31,41,55,0.8)",
        font: { size: 10, color: "#9ca3af" },
      },
      hovermode: "closest" as const,
    };

    return { traces: plotTraces, layout: plotLayout };
  }, [candidate, filters]);

  const plotRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = plotRef.current;
    if (!el || traces.length === 0) return;
    const plotConfig = {
      displayModeBar: true,
      modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"],
      displaylogo: false,
      responsive: true,
    };
    Plotly.newPlot(el, traces, layout, plotConfig);
    return () => {
      Plotly.purge(el);
    };
  }, [traces, layout]);

  if (traces.length === 0) {
    return (
      <div className="w-full bg-gray-800 rounded flex items-center justify-center text-gray-600 text-xs" style={{ height: 180 }}>
        No photometry data
      </div>
    );
  }

  return <div ref={plotRef} style={{ width: "100%", height: 200 }} />;
}
