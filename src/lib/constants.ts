import type { StageName } from "@/types/pipeline";

export const STAGE_NAMES: StageName[] = [
  "ingest",
  "preprocess",
  "detect",
  "deblend",
  "measure",
  "characterize",
  "crossmatch",
  "anomaly",
  "report",
];

export const STAGE_LABELS: Record<StageName, string> = {
  ingest: "Data Ingestion",
  preprocess: "Preprocessing",
  detect: "Source Detection",
  deblend: "Deblending",
  measure: "Photometry",
  characterize: "Characterization",
  crossmatch: "Cross-Match",
  anomaly: "Anomaly Detection",
  report: "Report",
};

export const STAGE_COLORS: Record<StageName, string> = {
  ingest: "#3b82f6",
  preprocess: "#8b5cf6",
  detect: "#ec4899",
  deblend: "#d946ef",
  measure: "#f97316",
  characterize: "#eab308",
  crossmatch: "#22c55e",
  anomaly: "#14b8a6",
  report: "#06b6d4",
};

export const NIRCAM_FILTERS = [
  "F070W", "F090W", "F115W", "F150W", "F200W",
  "F277W", "F356W", "F410M", "F444W",
];

/** NIRCam pivot wavelengths in microns (all 20 filters). */
export const PIVOT_WAVELENGTH_UM: Record<string, number> = {
  F070W: 0.704, F090W: 0.901, F115W: 1.154, F140M: 1.405,
  F150W: 1.501, F162M: 1.627, F182M: 1.845, F200W: 1.990,
  F210M: 2.093, F250M: 2.503, F277W: 2.786, F300M: 2.996,
  F335M: 3.365, F356W: 3.563, F360M: 3.621, F410M: 4.092,
  F430M: 4.280, F444W: 4.421, F460M: 4.624, F480M: 4.834,
};

/** Multi-wavelength pivot wavelengths in microns. */
export const MW_WAVELENGTH_UM: Record<string, number> = {
  IRAC_3_6: 3.550, IRAC_4_5: 4.493,
  WISE_W1: 3.368, WISE_W2: 4.618,
};

/** AGN confidence thresholds. */
export const AGN_THRESHOLD = 0.3;
export const AGN_STRONG_THRESHOLD = 0.6;

/** MW sub-classification display labels. */
export const MW_CLASS_LABELS: Record<string, string> = {
  xray_agn: "X-ray AGN",
  obscured_agn: "Obscured AGN",
  radio_agn: "Radio AGN",
  radio_sfg: "Radio SFG",
  xray_source: "X-ray Source",
  ir_excess: "IR Excess",
};

/** Tailwind badge colors per MW class. */
export const MW_CLASS_COLORS: Record<string, string> = {
  xray_agn: "bg-purple-600 text-white",
  obscured_agn: "bg-red-700 text-white",
  radio_agn: "bg-blue-600 text-white",
  radio_sfg: "bg-sky-500 text-white",
  xray_source: "bg-violet-500 text-white",
  ir_excess: "bg-amber-600 text-white",
};

export const COLORMAPS = [
  "grayscale", "viridis", "inferno", "magma", "plasma", "cubehelix", "heat",
];

export const SCALES = ["linear", "log", "sqrt", "asinh", "zscale"];

