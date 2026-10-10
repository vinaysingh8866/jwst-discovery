import { create } from "zustand";
import { invoke } from "@tauri-apps/api/core";
import { getWorkspaceRoot } from "@/lib/workspace";
import type { AnomalyCandidate, CandidatesReport } from "./resultsStore";
import { errorMessage } from "@/lib/errors";

export interface FieldSummary {
  field: string;
  workspace: string;
  n_sources: number;
  n_anomalies: number;
  n_novel: number;
  n_agn: number;
  n_strong_agn: number;
  n_highz: number;
  n_dropouts: number;
  mw_class_counts: Record<string, number>;
  tier_counts: Record<string, number>;
  candidates: AnomalyCandidate[];
  mw_summary?: CandidatesReport["mw_summary"];
  filters: string[];
}

interface ComparisonState {
  fields: FieldSummary[];
  loading: boolean;
  error: string | null;
  loadAllFields: () => Promise<void>;
}

const AGN_THRESHOLD = 0.3;
const AGN_STRONG_THRESHOLD = 0.6;

export function summarizeField(
  field: string,
  workspace: string,
  report: CandidatesReport,
): FieldSummary {
  const candidates = report.candidates;
  const n_novel = candidates.filter((c) => c.novel === true).length;
  const n_agn = candidates.filter(
    (c) => (c.agn_confidence ?? 0) >= AGN_THRESHOLD,
  ).length;
  const n_strong_agn = candidates.filter(
    (c) => (c.agn_confidence ?? 0) >= AGN_STRONG_THRESHOLD,
  ).length;
  const n_highz = candidates.filter(
    (c) =>
      c.object_class === "high_z_candidate" ||
      (c.z_phot_eazy != null &&
        Number(c.z_phot_eazy) > 6 &&
        c.z_phot_quality === "good"),
  ).length;
  const n_dropouts = candidates.filter(
    (c) => c.dropout_flag && c.dropout_flag !== "none",
  ).length;

  const mw_class_counts: Record<string, number> = {};
  const tier_counts: Record<string, number> = {
    gold: 0,
    silver: 0,
    bronze: 0,
  };
  for (const c of candidates) {
    if (c.mw_class) {
      mw_class_counts[c.mw_class] =
        (mw_class_counts[c.mw_class] || 0) + 1;
    }
    const tier = (c as Record<string, unknown>).confidence_tier;
    if (tier === 1) tier_counts.gold++;
    else if (tier === 2) tier_counts.silver++;
    else if (tier === 3) tier_counts.bronze++;
  }

  return {
    field,
    workspace,
    n_sources: report.n_total_sources,
    n_anomalies: report.n_candidates,
    n_novel,
    n_agn,
    n_strong_agn,
    n_highz,
    n_dropouts,
    mw_class_counts,
    tier_counts,
    candidates,
    mw_summary: report.mw_summary,
    filters: report.filters,
  };
}

export const useComparisonStore = create<ComparisonState>((set) => ({
  fields: [],
  loading: false,
  error: null,

  loadAllFields: async () => {
    set({ loading: true, error: null });
    try {
      const base = await getWorkspaceRoot();
      const dirs: string[] = await invoke("list_directory", { path: base });

      const fields: FieldSummary[] = [];

      for (const dirName of dirs) {
        const workspace = `${base}/${dirName}`;
        const reportPath = `${workspace}/report/candidates.json`;
        try {
          const text = await invoke<string>("read_text_file", {
            path: reportPath,
          });
          const report = JSON.parse(text) as CandidatesReport;
          fields.push(summarizeField(dirName, workspace, report));
        } catch {
          // Field doesn't have a report yet — skip
        }
      }

      // Sort by source count descending
      fields.sort((a, b) => b.n_sources - a.n_sources);
      set({ fields, loading: false });
    } catch (e) {
      set({ error: errorMessage(e), loading: false });
    }
  },
}));
