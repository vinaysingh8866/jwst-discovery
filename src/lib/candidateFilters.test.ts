import { describe, it, expect } from "vitest";
import type { AnomalyCandidate } from "@/stores/resultsStore";
import { countCandidates, filterCandidates, isHighZ, matchesFilterMode } from "./candidateFilters";

let nextId = 1;
function candidate(overrides: Partial<AnomalyCandidate> = {}): AnomalyCandidate {
  return {
    id: nextId++,
    rank: 1,
    anomaly_score: 0.5,
    ra: 0,
    dec: 0,
    x: 0,
    y: 0,
    ...overrides,
  };
}

describe("isHighZ", () => {
  it("requires a good EAZY fit above z=6 or an explicit high-z class", () => {
    expect(isHighZ(candidate({ z_phot_eazy: 7.2, z_phot_quality: "good" }))).toBe(true);
    expect(isHighZ(candidate({ z_phot_eazy: 7.2, z_phot_quality: "uncertain" }))).toBe(false);
    expect(isHighZ(candidate({ z_phot_eazy: 5.9, z_phot_quality: "good" }))).toBe(false);
    expect(isHighZ(candidate({ object_class: "high_z_candidate" }))).toBe(true);
  });
});

describe("matchesFilterMode", () => {
  it("treats 'all' as a pass-through", () => {
    expect(matchesFilterMode(candidate(), "all")).toBe(true);
  });

  it("maps dropout, galaxy and x-ray buckets onto the right fields", () => {
    expect(matchesFilterMode(candidate({ dropout_flag: "none" }), "dropout")).toBe(false);
    expect(matchesFilterMode(candidate({ dropout_flag: "F115W" }), "dropout")).toBe(true);
    expect(matchesFilterMode(candidate({ object_class: "red_galaxy" }), "galaxy")).toBe(true);
    expect(matchesFilterMode(candidate({ erosita_match: true }), "xray")).toBe(true);
    expect(matchesFilterMode(candidate({ vlass_match: true }), "xray")).toBe(false);
  });
});

describe("filterCandidates", () => {
  const list = [
    candidate({ id: 101, novel: true, max_sn: 20, n_bands_sn5: 4 }),
    candidate({ id: 202, novel: false, max_sn: 3, n_bands_sn5: 1 }),
    candidate({ id: 1010, novel: true, max_sn: 8, n_bands_sn5: 2 }),
  ];

  it("applies S/N thresholds and the mode together", () => {
    const out = filterCandidates(list, { searchTerm: "", mode: "novel", minSn: 5, minBandsSn5: 3 });
    expect(out.map((c) => c.id)).toEqual([101]);
  });

  it("lets an ID search override every other filter", () => {
    const out = filterCandidates(list, { searchTerm: " 10 ", mode: "novel", minSn: 50, minBandsSn5: 11 });
    expect(out.map((c) => c.id)).toEqual([101, 1010]);
  });

  it("treats missing S/N fields as zero", () => {
    const out = filterCandidates([candidate({ id: 5 })], { searchTerm: "", mode: "all", minSn: 1, minBandsSn5: 0 });
    expect(out).toHaveLength(0);
  });
});

describe("countCandidates", () => {
  it("counts each bucket independently", () => {
    const counts = countCandidates([
      candidate({ novel: true, dropout_flag: "F090W", agn_confidence: 0.35 }),
      candidate({ red_excess: true, is_agn_candidate: true, chandra_match: true, vlass_match: true }),
      candidate({ object_class: "high_z_candidate", agn_confidence: 0.1 }),
    ]);
    expect(counts).toEqual({
      nNovel: 1,
      nDropout: 1,
      nRedExcess: 1,
      nAgn: 1,
      nMwAgn: 1,
      nXray: 1,
      nRadio: 1,
      nHighZ: 1,
    });
  });
});
