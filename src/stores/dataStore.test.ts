import { describe, it, expect, beforeEach } from "vitest";
import { useDataStore, type MastQueryResult } from "./dataStore";

const result: MastQueryResult = {
  observations: 3,
  totalSizeGb: 1,
  target: "x",
  cached: false,
  cachedAt: null,
  programs: [
    {
      program_id: "1210",
      observations: 2,
      instruments: ["NIRCAM"],
      targets: [],
      total_size_mb: 10,
      mosaic_count: 2,
      filters: [
        { filter: "F200W", filename: "a", size_bytes: 1, size_mb: 1 },
        { filter: "F444W", filename: "b", size_bytes: 1, size_mb: 1 },
      ],
    },
  ],
};

beforeEach(() => {
  useDataStore.setState({ queryResult: null, selectedItems: new Set(), downloadedFiles: {}, pendingDownload: null });
});

describe("selection", () => {
  it("toggleItem adds and removes a program:filter key", () => {
    useDataStore.getState().toggleItem("1210:F200W");
    expect(useDataStore.getState().selectedItems.has("1210:F200W")).toBe(true);
    useDataStore.getState().toggleItem("1210:F200W");
    expect(useDataStore.getState().selectedItems.size).toBe(0);
  });

  it("selectAllInProgram / clearProgram operate only on that program", () => {
    useDataStore.getState().setQueryResult(result);
    useDataStore.getState().toggleItem("9999:F090W");
    useDataStore.getState().selectAllInProgram("1210");
    expect([...useDataStore.getState().selectedItems].sort()).toEqual(["1210:F200W", "1210:F444W", "9999:F090W"]);
    useDataStore.getState().clearProgram("1210");
    expect([...useDataStore.getState().selectedItems]).toEqual(["9999:F090W"]);
  });

  it("selectAllInProgram is a no-op for an unknown program", () => {
    useDataStore.getState().setQueryResult(result);
    const before = useDataStore.getState().selectedItems;
    useDataStore.getState().selectAllInProgram("nope");
    expect(useDataStore.getState().selectedItems).toBe(before);
  });
});

describe("pending download hand-off", () => {
  it("consumePendingDownload returns the selections once and then null", () => {
    const sel = [{ programId: "1210", filters: ["F200W"], forceRedownload: false }];
    useDataStore.getState().setPendingDownload(sel);
    expect(useDataStore.getState().consumePendingDownload()).toEqual(sel);
    expect(useDataStore.getState().consumePendingDownload()).toBeNull();
  });
});
