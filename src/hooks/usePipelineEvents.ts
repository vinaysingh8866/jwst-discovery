import { useEffect } from "react";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { usePipelineStore, type StageUpdateEvent, type LogEvent, type SidecarHealth } from "@/stores/pipelineStore";

interface PipelineCompletePayload {
  stages_completed: number;
}

/**
 * Subscribe to the pipeline events emitted by the Rust sidecar manager and
 * forward them to the pipeline store. All reduction logic lives in the store
 * so it can be tested without Tauri.
 */
export function usePipelineEvents() {
  const applyStageUpdate = usePipelineStore((s) => s.applyStageUpdate);
  const applyLog = usePipelineStore((s) => s.applyLog);
  const applyPipelineComplete = usePipelineStore((s) => s.applyPipelineComplete);
  const applySidecarHealth = usePipelineStore((s) => s.applySidecarHealth);

  useEffect(() => {
    let disposed = false;
    const unlisteners: (() => void)[] = [];

    const register = (promise: Promise<() => void>) =>
      promise.then((unlisten) => {
        // The component may already have unmounted while the listener was
        // being registered; detach immediately in that case.
        if (disposed) unlisten();
        else unlisteners.push(unlisten);
      });

    register(listen<StageUpdateEvent>("pipeline:stage-update", (event) => applyStageUpdate(event.payload)));
    register(listen<LogEvent>("pipeline:log", (event) => applyLog(event.payload)));
    register(listen<PipelineCompletePayload>("pipeline:complete", () => applyPipelineComplete()));
    register(listen<SidecarHealth>("sidecar:health", (event) => applySidecarHealth(event.payload)));

    // Health events emitted before the webview loaded are lost; ask once.
    invoke<SidecarHealth>("sidecar_status")
      .then((health) => {
        if (!disposed) applySidecarHealth(health);
      })
      .catch(() => {});

    return () => {
      disposed = true;
      unlisteners.forEach((unlisten) => unlisten());
    };
  }, [applyStageUpdate, applyLog, applyPipelineComplete, applySidecarHealth]);
}
