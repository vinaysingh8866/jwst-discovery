import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { SidecarNotice } from "./SidecarNotice";
import { usePipelineStore } from "@/stores/pipelineStore";

beforeEach(() => usePipelineStore.getState().applySidecarHealth({ alive: false, ready: false }));
afterEach(cleanup);

describe("SidecarNotice", () => {
  it("explains the wait while the sidecar is starting", () => {
    usePipelineStore.getState().applySidecarHealth({ alive: true, ready: false });
    render(<SidecarNotice />);
    expect(screen.getByRole("status").textContent).toContain("Starting the pipeline process");
  });

  it("shows the launch error when the sidecar is unavailable", () => {
    usePipelineStore.getState().applySidecarHealth({ alive: false, ready: false, message: "binary not found at /x" });
    render(<SidecarNotice />);
    expect(screen.getByRole("alert").textContent).toContain("binary not found at /x");
  });

  it("renders nothing once ready", () => {
    usePipelineStore.getState().applySidecarHealth({ alive: true, ready: true });
    render(<SidecarNotice />);
    expect(screen.queryByTestId("sidecar-notice")).toBeNull();
  });
});
