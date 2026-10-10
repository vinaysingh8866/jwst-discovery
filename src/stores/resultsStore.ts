import { create } from "zustand";
import { invoke } from "@tauri-apps/api/core";
import { errorMessage, isNotFound } from "@/lib/errors";

export interface AnomalyCandidate {
  id: number;
  rank: number;
  anomaly_score: number;
  ra: number;
  dec: number;
  x: number;
  y: number;
  ellipticity?: number | null;
  r_half_pix?: number | null;
  stellarity?: number | null;
  dropout_flag?: string | null;
  red_excess?: boolean | null;
  is_agn_candidate?: boolean | null;
  object_class?: string | null;
  z_phot_eazy?: number | null;
  z_phot_chi2?: number | null;
  z_phot_quality?: string | null;
  novel?: boolean | null;
  ned_match?: boolean | null;
  ned_name?: string | null;
  ned_type?: string | null;
  gaia_match?: boolean | null;
  chandra_match?: boolean | null;
  chandra_flux?: number | null;
  chandra_hardness?: number | null;
  wise_match?: boolean | null;
  wise_w1?: number | null;
  wise_w2?: number | null;
  wise_w1w2?: number | null;
  xmm_match?: boolean | null;
  xmm_flux?: number | null;
  vlass_match?: boolean | null;
  vlass_flux?: number | null;
  spitzer_match?: boolean | null;
  spitzer_ch1?: number | null;
  spitzer_ch2?: number | null;
  erosita_match?: boolean | null;
  erosita_flux?: number | null;
  best_photo_z?: number | null;
  best_spec_z?: number | null;
  max_sn?: number | null;
  median_sn?: number | null;
  n_bands_sn5?: number | null;
  agn_confidence?: number | null;
  mw_class?: string | null;
  z_agree?: boolean | null;
  z_source?: string | null;
  z_catastrophic?: boolean | null;
  mw_photometry?: Array<{
    name: string;
    wavelength_um: number;
    flux_ujy: number;
    instrument: string;
    mag_vega?: number;
  }> | null;
  mu_best?: number | null;
  intrinsic_mag?: Record<string, number> | null;
  stellar_mass?: number | null;
  sfr?: number | null;
  cigale_chi2?: number | null;
  is_cluster_member?: boolean | null;
  is_mw_contaminated?: boolean | null;
  cutout_detection?: string;
  sed_plot?: string;
  [key: string]: unknown;
}

export interface CandidatesReport {
  n_candidates: number;
  n_total_sources: number;
  n_total_novel?: number;
  filters: string[];
  tier_summary?: {
    tier_1_gold: number;
    tier_2_silver: number;
    tier_3_bronze: number;
  };
  classification?: Record<string, number>;
  novel_classification?: Record<string, number>;
  mw_summary?: {
    n_agn_candidates?: number;
    n_strong_agn?: number;
    mw_class_counts?: Record<string, number>;
    n_chandra?: number;
    n_wise?: number;
    n_xmm?: number;
    n_vlass?: number;
    n_spitzer?: number;
    n_erosita?: number;
  };
  candidates: AnomalyCandidate[];
}

export interface SourceSummary {
  id: number;
  ra: number;
  dec: number;
  x: number;
  y: number;
  anomaly_score: number | null;
  anomaly_rank: number | null;
  stellarity?: number | null;
  dropout_flag?: string | null;
  red_excess?: boolean | null;
  is_agn_candidate?: boolean | null;
  object_class?: string | null;
  z_phot_eazy?: number | null;
  z_phot_quality?: string | null;
  novel?: boolean | null;
  ned_match?: boolean | null;
  gaia_match?: boolean | null;
  chandra_match?: boolean | null;
  wise_match?: boolean | null;
  xmm_match?: boolean | null;
  vlass_match?: boolean | null;
  spitzer_match?: boolean | null;
  erosita_match?: boolean | null;
  best_photo_z?: number | null;
  best_spec_z?: number | null;
  max_sn?: number | null;
  median_sn?: number | null;
  n_bands_sn5?: number | null;
  agn_confidence?: number | null;
  mw_class?: string | null;
  z_agree?: boolean | null;
  [key: string]: unknown;
}

export interface SourcesSummaryReport {
  n_sources: number;
  filters: string[];
  columns: string[];
  sources: SourceSummary[];
}

interface ResultsStore {
  workspace: string | null;
  candidatesReport: CandidatesReport | null;
  sourcesSummary: SourcesSummaryReport | null;
  loading: boolean;
  error: string | null;
  /** The workspace has no report yet (candidates.json missing); not an error. */
  notFound: boolean;
  imageCache: Record<string, string>; // path -> data URL

  setWorkspace: (ws: string) => void;
  loadCandidates: (workspace: string) => Promise<void>;
  loadSourcesSummary: (workspace: string) => Promise<void>;
  loadImage: (workspace: string, relativePath: string) => Promise<string>;
  clearResults: () => void;
}

export const useResultsStore = create<ResultsStore>((set, get) => ({
  workspace: null,
  candidatesReport: null,
  sourcesSummary: null,
  loading: false,
  error: null,
  notFound: false,
  imageCache: {},

  setWorkspace: (ws) => set({ workspace: ws }),

  loadCandidates: async (workspace) => {
    set({ loading: true, error: null, notFound: false });
    try {
      const path = `${workspace}/report/candidates.json`;
      const text = await invoke<string>("read_text_file", { path });
      const data = JSON.parse(text) as CandidatesReport;
      set({ candidatesReport: data, loading: false, workspace, notFound: false });
    } catch (e) {
      if (isNotFound(e)) {
        set({ candidatesReport: null, loading: false, workspace, notFound: true, error: null });
      } else {
        set({ error: errorMessage(e), loading: false });
      }
    }
  },

  loadSourcesSummary: async (workspace) => {
    set({ loading: true, error: null });
    try {
      const path = `${workspace}/report/sources_summary.json`;
      const text = await invoke<string>("read_text_file", { path });
      const data = JSON.parse(text) as SourcesSummaryReport;
      set({ sourcesSummary: data, loading: false, workspace });
    } catch (e) {
      set({ error: errorMessage(e), loading: false });
    }
  },

  loadImage: async (workspace, relativePath) => {
    const cacheKey = `${workspace}/${relativePath}`;
    const cached = get().imageCache[cacheKey];
    if (cached) return cached;

    const fullPath = `${workspace}/report/${relativePath}`;
    const base64 = await invoke<string>("read_binary_file_base64", { path: fullPath });
    const ext = relativePath.split('.').pop()?.toLowerCase();
    const mime = ext === 'jpg' || ext === 'jpeg' ? 'image/jpeg' : 'image/png';
    const dataUrl = `data:${mime};base64,${base64}`;

    set((state) => ({
      imageCache: { ...state.imageCache, [cacheKey]: dataUrl },
    }));
    return dataUrl;
  },

  clearResults: () =>
    set({
      candidatesReport: null,
      sourcesSummary: null,
      loading: false,
      error: null,
      notFound: false,
      imageCache: {},
    }),
}));
