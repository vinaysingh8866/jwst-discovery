import type { AnomalyCandidate } from "@/stores/resultsStore";
import { AGN_THRESHOLD } from "./constants";

/** Quick-filter buckets offered on the Anomaly page. */
export type FilterMode =
  | "all"
  | "novel"
  | "dropout"
  | "red_excess"
  | "star"
  | "galaxy"
  | "agn"
  | "high_z"
  | "mw_agn"
  | "xray"
  | "radio";

export function isDropout(c: AnomalyCandidate): boolean {
  return !!c.dropout_flag && c.dropout_flag !== "none";
}

export function isAgnOrLrd(c: AnomalyCandidate): boolean {
  return c.is_agn_candidate === true || c.object_class === "agn_candidate";
}

export function isMwAgn(c: AnomalyCandidate): boolean {
  return (c.agn_confidence ?? 0) >= AGN_THRESHOLD;
}

export function isXray(c: AnomalyCandidate): boolean {
  return c.chandra_match === true || c.xmm_match === true || c.erosita_match === true;
}

export function isRadio(c: AnomalyCandidate): boolean {
  return c.vlass_match === true;
}

/** z > 6 with a "good" EAZY fit, or explicitly classified as a high-z candidate. */
export function isHighZ(c: AnomalyCandidate): boolean {
  return (
    c.object_class === "high_z_candidate" ||
    (c.z_phot_eazy != null && c.z_phot_eazy > 6 && c.z_phot_quality === "good")
  );
}

export function matchesFilterMode(c: AnomalyCandidate, mode: FilterMode): boolean {
  switch (mode) {
    case "novel":
      return c.novel === true;
    case "dropout":
      return isDropout(c);
    case "red_excess":
      return c.red_excess === true;
    case "star":
      return c.object_class === "star";
    case "galaxy":
      return c.object_class === "galaxy" || c.object_class === "red_galaxy";
    case "agn":
      return isAgnOrLrd(c);
    case "high_z":
      return isHighZ(c);
    case "mw_agn":
      return isMwAgn(c);
    case "xray":
      return isXray(c);
    case "radio":
      return isRadio(c);
    default:
      return true;
  }
}

export interface CandidateFilterOptions {
  /** ID substring search; when non-empty it overrides every other filter. */
  searchTerm: string;
  mode: FilterMode;
  minSn: number;
  minBandsSn5: number;
}

export function filterCandidates(
  candidates: AnomalyCandidate[],
  { searchTerm, mode, minSn, minBandsSn5 }: CandidateFilterOptions,
): AnomalyCandidate[] {
  const term = searchTerm.trim();
  return candidates.filter((c) => {
    if (term !== "") {
      return String(c.id).includes(term);
    }
    const sn = c.max_sn ?? 0;
    const bands = c.n_bands_sn5 ?? 0;
    if (sn < minSn || bands < minBandsSn5) return false;
    return matchesFilterMode(c, mode);
  });
}

export interface CandidateCounts {
  nNovel: number;
  nDropout: number;
  nRedExcess: number;
  nAgn: number;
  nMwAgn: number;
  nXray: number;
  nRadio: number;
  nHighZ: number;
}

/** Per-bucket counts used for the filter buttons and the stats bar. */
export function countCandidates(candidates: AnomalyCandidate[]): CandidateCounts {
  const counts: CandidateCounts = {
    nNovel: 0,
    nDropout: 0,
    nRedExcess: 0,
    nAgn: 0,
    nMwAgn: 0,
    nXray: 0,
    nRadio: 0,
    nHighZ: 0,
  };
  for (const c of candidates) {
    if (c.novel) counts.nNovel++;
    if (isDropout(c)) counts.nDropout++;
    if (c.red_excess) counts.nRedExcess++;
    if (isAgnOrLrd(c)) counts.nAgn++;
    if (isMwAgn(c)) counts.nMwAgn++;
    if (isXray(c)) counts.nXray++;
    if (isRadio(c)) counts.nRadio++;
    if (isHighZ(c)) counts.nHighZ++;
  }
  return counts;
}
