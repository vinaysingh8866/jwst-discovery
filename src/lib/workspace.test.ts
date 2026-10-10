import { describe, it, expect, vi, beforeEach } from "vitest";
import { homeDir } from "@tauri-apps/api/path";
import { invoke } from "@tauri-apps/api/core";
import {
  WORKSPACES_SUBDIR,
  WorkspaceError,
  getWorkspacePath,
  getWorkspaceRoot,
  resetWorkspaceRootCache,
  resolveWorkspace,
  sanitizeFieldName,
} from "./workspace";

vi.mock("@tauri-apps/api/path", () => ({
  homeDir: vi.fn(),
}));
vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

const mockedHomeDir = vi.mocked(homeDir);
const mockedInvoke = vi.mocked(invoke);

beforeEach(() => {
  resetWorkspaceRootCache();
  mockedHomeDir.mockReset();
  mockedInvoke.mockReset();
  // Default: host command unavailable, so the homeDir fallback is exercised.
  mockedInvoke.mockRejectedValue(new Error("no such command"));
});

describe("getWorkspaceRoot via the host", () => {
  it("prefers the Rust get_workspace_root command (JWST_WORKSPACE_ROOT aware)", async () => {
    mockedInvoke.mockResolvedValueOnce("/data/jwst/");
    await expect(getWorkspaceRoot()).resolves.toBe("/data/jwst");
    expect(mockedHomeDir).not.toHaveBeenCalled();
  });
});

describe("sanitizeFieldName", () => {
  it("replaces every character outside [A-Za-z0-9_-] with an underscore", () => {
    expect(sanitizeFieldName("JADES-GOODS-S")).toBe("JADES-GOODS-S");
    expect(sanitizeFieldName("Abell 2744 / v2.1")).toBe("Abell_2744___v2_1");
    expect(sanitizeFieldName("")).toBe("");
  });
});

describe("getWorkspaceRoot", () => {
  it("appends the workspaces subdirectory to the home directory", async () => {
    mockedHomeDir.mockResolvedValue("/Users/astro");
    await expect(getWorkspaceRoot()).resolves.toBe(`/Users/astro/${WORKSPACES_SUBDIR}`);
  });

  it("strips trailing slashes from the home directory", async () => {
    mockedHomeDir.mockResolvedValue("/Users/astro/");
    await expect(getWorkspaceRoot()).resolves.toBe("/Users/astro/.jwst-discovery/workspaces");
  });

  it("calls the Tauri path API only once and caches the result", async () => {
    mockedHomeDir.mockResolvedValue("/home/x");
    const [a, b] = await Promise.all([getWorkspaceRoot(), getWorkspaceRoot()]);
    await getWorkspacePath("f");
    expect(a).toBe(b);
    expect(mockedHomeDir).toHaveBeenCalledTimes(1);
  });

  it("rejects with a WorkspaceError (and never falls back to /tmp) when the home dir is unavailable", async () => {
    mockedHomeDir.mockRejectedValue(new Error("path API unavailable"));
    const err = await getWorkspaceRoot().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(WorkspaceError);
    expect((err as Error).message).toContain("path API unavailable");
    expect((err as Error).message).not.toContain("/tmp");
  });

  it("rejects when the home directory resolves to an empty string", async () => {
    mockedHomeDir.mockResolvedValue("");
    await expect(getWorkspaceRoot()).rejects.toBeInstanceOf(WorkspaceError);
  });

  it("clears the cache after a failure so a later call can retry", async () => {
    mockedHomeDir.mockRejectedValueOnce(new Error("boom")).mockResolvedValue("/home/y");
    await expect(getWorkspaceRoot()).rejects.toBeInstanceOf(WorkspaceError);
    await expect(getWorkspaceRoot()).resolves.toBe("/home/y/.jwst-discovery/workspaces");
    expect(mockedHomeDir).toHaveBeenCalledTimes(2);
  });
});

describe("getWorkspacePath / resolveWorkspace", () => {
  it("builds <root>/<sanitized field>", async () => {
    mockedHomeDir.mockResolvedValue("/home/z");
    await expect(getWorkspacePath("MACS J0416")).resolves.toBe(
      "/home/z/.jwst-discovery/workspaces/MACS_J0416",
    );
  });

  it("returns an explicit config.workspace verbatim without touching the path API", async () => {
    await expect(
      resolveWorkspace({ workspace: "/data/custom", target_field: "ignored" }),
    ).resolves.toBe("/data/custom");
    expect(mockedHomeDir).not.toHaveBeenCalled();
  });

  it("derives the path from target_field when config.workspace is null", async () => {
    mockedHomeDir.mockResolvedValue("/home/z");
    await expect(
      resolveWorkspace({ workspace: null, target_field: "JADES-GOODS-S" }),
    ).resolves.toBe("/home/z/.jwst-discovery/workspaces/JADES-GOODS-S");
  });
});
