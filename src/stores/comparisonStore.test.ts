import { describe, it, expect, vi, beforeEach } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { homeDir } from "@tauri-apps/api/path";
import { useComparisonStore, summarizeField } from "./comparisonStore";
import { resetWorkspaceRootCache } from "@/lib/workspace";
import type { AnomalyCandidate, CandidatesReport } from "./resultsStore";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/path", () => ({ homeDir: vi.fn() }));
const mockedInvoke = vi.mocked(invoke);
const mockedHomeDir = vi.mocked(homeDir);

function c(overrides: Partial<AnomalyCandidate> & Record<string, unknown> = {}): AnomalyCandidate {
  return { id: 1, rank: 1, anomaly_score: 0.5, ra: 0, dec: 0, x: 0, y: 0, ...overrides };
}

function makeReport(candidates: AnomalyCandidate[], n_total_sources = 100): CandidatesReport {
  return { n_candidates: candidates.length, n_total_sources, filters: ["F200W"], candidates };
}

beforeEach(() => {
  useComparisonStore.setState({ fields: [], loading: false, error: null });
  resetWorkspaceRootCache();
  mockedInvoke.mockReset();
  mockedHomeDir.mockReset();
});

describe("summarizeField", () => {
  it("derives per-field counts from the candidate list", () => {
    const report = makeReport([
      c({ novel: true, agn_confidence: 0.35, confidence_tier: 1, mw_class: "xray_agn" }),
      c({ novel: true, agn_confidence: 0.7, confidence_tier: 2, mw_class: "xray_agn", dropout_flag: "F115W" }),
      c({ z_phot_eazy: 8.1, z_phot_quality: "good", confidence_tier: 3, dropout_flag: "none" }),
      c({ object_class: "high_z_candidate", confidence_tier: 9 }),
    ]);
    const summary = summarizeField("abell2744", "/ws/abell2744", report);
    expect(summary).toMatchObject({
      field: "abell2744",
      workspace: "/ws/abell2744",
      n_sources: 100,
      n_anomalies: 4,
      n_novel: 2,
      n_agn: 2,
      n_strong_agn: 1,
      n_highz: 2,
      n_dropouts: 1,
      mw_class_counts: { xray_agn: 2 },
      tier_counts: { gold: 1, silver: 1, bronze: 1 },
      filters: ["F200W"],
    });
  });
});

describe("loadAllFields", () => {
  it("lists the workspace root, skips fields without a report and sorts by source count", async () => {
    mockedHomeDir.mockResolvedValue("/home/u");
    mockedInvoke.mockImplementation(async (cmd, args) => {
      const a = args as { path: string };
      if (cmd === "list_directory") {
        expect(a.path).toBe("/home/u/.jwst-discovery/workspaces");
        return ["small", "no_report", "big"];
      }
      if (a.path.includes("/no_report/")) throw new Error("missing");
      const n = a.path.includes("/big/") ? 5000 : 10;
      return JSON.stringify(makeReport([c()], n));
    });

    await useComparisonStore.getState().loadAllFields();

    const { fields, loading, error } = useComparisonStore.getState();
    expect(error).toBeNull();
    expect(loading).toBe(false);
    expect(fields.map((f) => f.field)).toEqual(["big", "small"]);
    expect(fields[0].workspace).toBe("/home/u/.jwst-discovery/workspaces/big");
  });

  it("surfaces a workspace-root failure as a store error instead of guessing a path", async () => {
    mockedHomeDir.mockRejectedValue(new Error("no home"));
    await useComparisonStore.getState().loadAllFields();
    const { fields, loading, error } = useComparisonStore.getState();
    expect(fields).toEqual([]);
    expect(loading).toBe(false);
    expect(error).toContain("no home");
    // Only the root lookup itself may have been attempted; no workspace reads.
    expect(mockedInvoke.mock.calls.filter(([cmd]) => cmd !== "get_workspace_root")).toHaveLength(0);
  });
});
