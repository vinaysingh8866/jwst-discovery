import { describe, it, expect, vi, beforeEach } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { useResultsStore, type CandidatesReport, type SourcesSummaryReport } from "./resultsStore";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
const mockedInvoke = vi.mocked(invoke);

const report: CandidatesReport = {
  n_candidates: 1,
  n_total_sources: 10,
  filters: ["F200W"],
  candidates: [{ id: 1, rank: 1, anomaly_score: 0.9, ra: 1, dec: 2, x: 3, y: 4 }],
};

const summary: SourcesSummaryReport = {
  n_sources: 2,
  filters: ["F200W"],
  columns: ["id"],
  sources: [
    { id: 1, ra: 0, dec: 0, x: 0, y: 0, anomaly_score: null, anomaly_rank: null },
    { id: 2, ra: 0, dec: 0, x: 0, y: 0, anomaly_score: 0.1, anomaly_rank: 1 },
  ],
};

beforeEach(() => {
  useResultsStore.getState().clearResults();
  useResultsStore.setState({ workspace: null });
  mockedInvoke.mockReset();
});

describe("loadCandidates", () => {
  it("reads <workspace>/report/candidates.json and stores the parsed report", async () => {
    mockedInvoke.mockResolvedValue(JSON.stringify(report));
    await useResultsStore.getState().loadCandidates("/ws/a");
    expect(mockedInvoke).toHaveBeenCalledWith("read_text_file", { path: "/ws/a/report/candidates.json" });
    expect(useResultsStore.getState()).toMatchObject({
      candidatesReport: report,
      workspace: "/ws/a",
      loading: false,
      error: null,
    });
  });

  it("records the error and clears loading on failure", async () => {
    mockedInvoke.mockRejectedValue("IO error: no such file");
    await useResultsStore.getState().loadCandidates("/ws/missing");
    expect(useResultsStore.getState()).toMatchObject({
      candidatesReport: null,
      loading: false,
      error: "IO error: no such file",
    });
  });
});

describe("loadSourcesSummary", () => {
  it("reads <workspace>/report/sources_summary.json", async () => {
    mockedInvoke.mockResolvedValue(JSON.stringify(summary));
    await useResultsStore.getState().loadSourcesSummary("/ws/b");
    expect(mockedInvoke).toHaveBeenCalledWith("read_text_file", { path: "/ws/b/report/sources_summary.json" });
    expect(useResultsStore.getState().sourcesSummary).toEqual(summary);
  });
});

describe("loadImage", () => {
  it("returns a data URL and serves repeat requests from the cache", async () => {
    mockedInvoke.mockResolvedValue("QUJD");
    const first = await useResultsStore.getState().loadImage("/ws", "cutouts/src000001_F200W.png");
    const second = await useResultsStore.getState().loadImage("/ws", "cutouts/src000001_F200W.png");
    expect(first).toBe("data:image/png;base64,QUJD");
    expect(second).toBe(first);
    expect(mockedInvoke).toHaveBeenCalledTimes(1);
    expect(mockedInvoke).toHaveBeenCalledWith("read_binary_file_base64", {
      path: "/ws/report/cutouts/src000001_F200W.png",
    });
  });

  it("picks the JPEG mime type from the extension", async () => {
    mockedInvoke.mockResolvedValue("//8=");
    await expect(useResultsStore.getState().loadImage("/ws", "sed/1.JPG")).resolves.toBe(
      "data:image/jpeg;base64,//8=",
    );
  });
});

describe("clearResults / setWorkspace", () => {
  it("clears reports, errors and the image cache", async () => {
    mockedInvoke.mockResolvedValue(JSON.stringify(report));
    await useResultsStore.getState().loadCandidates("/ws");
    useResultsStore.getState().setWorkspace("/elsewhere");
    expect(useResultsStore.getState().workspace).toBe("/elsewhere");
    useResultsStore.getState().clearResults();
    expect(useResultsStore.getState()).toMatchObject({
      candidatesReport: null,
      sourcesSummary: null,
      error: null,
      imageCache: {},
    });
  });
});

describe("missing report", () => {
  it("marks a missing candidates.json as notFound instead of an error", async () => {
    mockedInvoke.mockRejectedValueOnce({ Io: "/ws/report/candidates.json: No such file or directory (os error 2)" });
    await useResultsStore.getState().loadCandidates("/ws");
    const s = useResultsStore.getState();
    expect(s.notFound).toBe(true);
    expect(s.error).toBeNull();
    expect(s.candidatesReport).toBeNull();
  });

  it("reports other failures as readable text, never [object Object]", async () => {
    mockedInvoke.mockRejectedValueOnce({ Io: "/ws/report/candidates.json: Permission denied (os error 13)" });
    await useResultsStore.getState().loadCandidates("/ws");
    expect(useResultsStore.getState().error).toContain("Permission denied");
    expect(useResultsStore.getState().error).not.toContain("[object Object]");
  });
});
