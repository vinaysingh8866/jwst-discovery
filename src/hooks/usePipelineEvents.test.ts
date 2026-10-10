import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { listen } from "@tauri-apps/api/event";
import { usePipelineEvents } from "./usePipelineEvents";
import { usePipelineStore } from "@/stores/pipelineStore";

vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(() => Promise.resolve({ alive: true, ready: false })) }));
const mockedListen = vi.mocked(listen);

type Handler = (event: { payload: unknown }) => void;

/** Install a fake `listen` that records handlers and returns per-event unlisten spies. */
function installFakeListen() {
  const handlers: Record<string, Handler> = {};
  const unlisteners: Record<string, ReturnType<typeof vi.fn>> = {};
  mockedListen.mockImplementation(((name: string, handler: Handler) => {
    handlers[name] = handler;
    unlisteners[name] = vi.fn();
    return Promise.resolve(unlisteners[name]);
  }) as unknown as typeof listen);
  const emit = (name: string, payload: unknown) => act(() => handlers[name]({ payload }));
  return { handlers, unlisteners, emit };
}

beforeEach(() => {
  usePipelineStore.getState().reset();
  mockedListen.mockReset();
});

describe("usePipelineEvents", () => {
  it("subscribes to the pipeline and sidecar events and seeds sidecar health on mount", async () => {
    installFakeListen();
    renderHook(() => usePipelineEvents());
    await act(async () => {});
    const names = mockedListen.mock.calls.map(([name]) => name).sort();
    expect(names).toEqual(["pipeline:complete", "pipeline:log", "pipeline:stage-update", "sidecar:health"]);
    expect(usePipelineStore.getState().sidecar).toEqual({ alive: true, ready: false });
  });

  it("forwards sidecar:health events to the store", async () => {
    const { emit } = installFakeListen();
    renderHook(() => usePipelineEvents());
    await act(async () => {});
    emit("sidecar:health", { alive: true, ready: true, launch: "bundled" });
    expect(usePipelineStore.getState().sidecar.ready).toBe(true);
  });

  it("forwards stage-update events (Progress, StageComplete, Error) to the store", async () => {
    const { emit } = installFakeListen();
    renderHook(() => usePipelineEvents());
    await act(async () => {});

    await emit("pipeline:stage-update", { stageName: "detect", status: "running", progress: 0.5, message: "half" });
    expect(usePipelineStore.getState().stages.detect).toMatchObject({ status: "running", progress: 0.5, message: "half" });

    await emit("pipeline:stage-update", {
      stageName: "detect",
      status: "completed",
      progress: 1,
      message: "Completed in 1.0s",
      result: { n: 1 },
    });
    expect(usePipelineStore.getState().stages.detect).toMatchObject({ status: "completed", result: { n: 1 } });

    await emit("pipeline:stage-update", { stageName: null, status: "failed", progress: 0, message: "boom" });
    expect(usePipelineStore.getState().overallStatus).toBe("failed");
  });

  it("forwards log and completion events", async () => {
    const { emit } = installFakeListen();
    renderHook(() => usePipelineEvents());
    await act(async () => {});

    await emit("pipeline:log", { level: "error", message: "bad" });
    expect(usePipelineStore.getState().logs).toHaveLength(1);
    expect(usePipelineStore.getState().logs[0]).toMatchObject({ level: "error", message: "bad" });

    await emit("pipeline:complete", { stages_completed: 9 });
    expect(usePipelineStore.getState().overallStatus).toBe("completed");
  });

  it("unlistens from every event on unmount", async () => {
    const { unlisteners } = installFakeListen();
    const { unmount } = renderHook(() => usePipelineEvents());
    await act(async () => {});
    unmount();
    for (const name of ["pipeline:stage-update", "pipeline:log", "pipeline:complete"]) {
      expect(unlisteners[name]).toHaveBeenCalledTimes(1);
    }
  });

  it("detaches a listener that resolves after the hook has already unmounted", async () => {
    const unlisten = vi.fn();
    let resolve!: (fn: () => void) => void;
    mockedListen.mockImplementation((() => new Promise<() => void>((r) => { resolve = r; })) as unknown as typeof listen);
    const { unmount } = renderHook(() => usePipelineEvents());
    unmount();
    await act(async () => {
      resolve(unlisten);
    });
    expect(unlisten).toHaveBeenCalled();
  });
});
