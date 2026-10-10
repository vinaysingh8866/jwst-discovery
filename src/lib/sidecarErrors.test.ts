import { describe, it, expect } from "vitest";
import { isOwnError } from "./sidecarErrors";

describe("isOwnError", () => {
  it("matches the error whose requestId equals the pending request", () => {
    expect(isOwnError({ requestId: "req-1", message: "MAST down" }, "req-1")).toBe(true);
  });

  it("ignores errors from other requests (e.g. MastMissions while a MAST query is pending)", () => {
    expect(isOwnError({ requestId: "req-2", message: "missions failed" }, "req-1")).toBe(false);
  });

  it("ignores every error while nothing is pending", () => {
    expect(isOwnError({ requestId: "req-1", message: "late error" }, null)).toBe(false);
    expect(isOwnError({ requestId: "req-1", message: "late error" }, undefined)).toBe(false);
    expect(isOwnError({ requestId: "req-1", message: "late error" }, "")).toBe(false);
  });

  it("does not treat an id-less error as ours", () => {
    expect(isOwnError({ requestId: null, message: "boom" }, "req-1")).toBe(false);
    expect(isOwnError({ message: "boom" }, "req-1")).toBe(false);
    expect(isOwnError({ requestId: "unknown", message: "boom" }, "req-1")).toBe(false);
  });
});
