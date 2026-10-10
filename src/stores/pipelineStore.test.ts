import { describe, it, expect, beforeEach } from "vitest";
import { usePipelineStore, isStageName, normalizeLogLevel } from "./pipelineStore";
import { STAGE_NAMES } from "@/lib/constants";

const store = () => usePipelineStore.getState();

beforeEach(() => {
  store().reset();
});

describe("initial state / reset", () => {
  it("starts with every stage idle", () => {
    expect(Object.keys(store().stages)).toEqual(STAGE_NAMES);
    for (const s of Object.values(store().stages)) {
      expect(s).toMatchObject({ status: "idle", progress: 0, message: "" });
    }
    expect(store().overallStatus).toBe("idle");
    expect(store().logs).toEqual([]);
  });
});

describe("applyStageUpdate — Progress", () => {
  it("updates the named stage and leaves the overall status alone", () => {
    store().setOverallStatus("running");
    store().applyStageUpdate({ stageName: "detect", status: "running", progress: 0.4, message: "Detecting" });
    expect(store().stages.detect).toMatchObject({ status: "running", progress: 0.4, message: "Detecting" });
    expect(store().stages.ingest.status).toBe("idle");
    expect(store().overallStatus).toBe("running");
  });

  it("ignores unknown stage names without throwing", () => {
    const before = store().stages;
    store().applyStageUpdate({ stageName: "ref_catalogs", status: "running", progress: 0.1, message: "" });
    expect(store().stages).toBe(before);
  });
});

describe("applyStageUpdate — StageComplete", () => {
  it("stores the stage result and drops to idle when nothing else is running", () => {
    store().setOverallStatus("running");
    store().applyStageUpdate({
      stageName: "measure",
      status: "completed",
      progress: 1,
      message: "Completed in 3.2s",
      result: { n_sources: 42 },
    });
    expect(store().stages.measure).toMatchObject({ status: "completed", progress: 1, result: { n_sources: 42 } });
    expect(store().overallStatus).toBe("idle");
  });

  it("keeps the overall status while another stage is still running", () => {
    store().setOverallStatus("running");
    store().applyStageUpdate({ stageName: "crossmatch", status: "running", progress: 0.2, message: "" });
    store().applyStageUpdate({ stageName: "measure", status: "completed", progress: 1, message: "" });
    expect(store().overallStatus).toBe("running");
  });
});

describe("applyStageUpdate — Error", () => {
  it("marks the stage and the pipeline as failed", () => {
    store().applyStageUpdate({ stageName: "detect", status: "failed", progress: 0, message: "SEP overflow" });
    expect(store().stages.detect).toMatchObject({ status: "failed", message: "SEP overflow" });
    expect(store().overallStatus).toBe("failed");
  });

  it("handles a null stage (error outside any stage) by only failing the pipeline", () => {
    const before = store().stages;
    store().applyStageUpdate({ stageName: null, status: "failed", progress: 0, message: "sidecar crashed" });
    expect(store().stages).toBe(before);
    expect(store().overallStatus).toBe("failed");
  });
});

describe("applyLog / appendLog", () => {
  it("normalizes the sidecar level and records the timestamp", () => {
    store().applyLog({ level: "WARNING", message: "slow query" }, "2026-01-01T00:00:00.000Z");
    expect(store().logs).toEqual([{ level: "warn", message: "slow query", timestamp: "2026-01-01T00:00:00.000Z" }]);
  });

  it("defaults the timestamp to now", () => {
    store().applyLog({ level: "info", message: "hi" });
    expect(Date.parse(store().logs[0].timestamp)).not.toBeNaN();
  });

  it("caps the log buffer at 5000 entries", () => {
    for (let i = 0; i < 5005; i++) {
      store().appendLog({ level: "info", message: `m${i}`, timestamp: "t" });
    }
    expect(store().logs).toHaveLength(5000);
    expect(store().logs[0].message).toBe("m5");
    expect(store().logs[4999].message).toBe("m5004");
  });

  it("clearLogs empties the buffer", () => {
    store().appendLog({ level: "info", message: "x", timestamp: "t" });
    store().clearLogs();
    expect(store().logs).toEqual([]);
  });
});

describe("applyPipelineComplete", () => {
  it("sets the overall status to completed", () => {
    store().applyPipelineComplete();
    expect(store().overallStatus).toBe("completed");
  });
});

describe("helpers", () => {
  it("isStageName accepts only the nine pipeline stages", () => {
    expect(isStageName("deblend")).toBe(true);
    expect(isStageName("ref_catalogs")).toBe(false);
    expect(isStageName(null)).toBe(false);
  });

  it("normalizeLogLevel maps Python logging names onto UI levels", () => {
    expect(normalizeLogLevel("DEBUG")).toBe("debug");
    expect(normalizeLogLevel("warning")).toBe("warn");
    expect(normalizeLogLevel("CRITICAL")).toBe("error");
    expect(normalizeLogLevel("weird")).toBe("info");
  });
});

describe("sidecar health", () => {
  it("starts unavailable and collapses health payloads into three states", async () => {
    const { sidecarStatusOf } = await import("./pipelineStore");
    expect(sidecarStatusOf(store().sidecar)).toBe("unavailable");
    store().applySidecarHealth({ alive: true, ready: false, launch: "bundled binary /x" });
    expect(sidecarStatusOf(store().sidecar)).toBe("starting");
    store().applySidecarHealth({ alive: true, ready: true });
    expect(sidecarStatusOf(store().sidecar)).toBe("ready");
    store().applySidecarHealth({ alive: false, ready: false, message: "exited" });
    expect(sidecarStatusOf(store().sidecar)).toBe("unavailable");
    expect(store().sidecar.message).toBe("exited");
  });

  it("is not cleared by reset()", () => {
    store().applySidecarHealth({ alive: true, ready: true });
    store().reset();
    expect(store().sidecar.ready).toBe(true);
  });
});
