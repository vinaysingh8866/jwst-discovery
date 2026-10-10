import { usePipelineStore, sidecarStatusOf } from "@/stores/pipelineStore";

/**
 * Banner explaining why pipeline features are not available yet. Renders
 * nothing once the sidecar has answered the startup ping.
 */
export function SidecarNotice() {
  const sidecar = usePipelineStore((s) => s.sidecar);
  const status = sidecarStatusOf(sidecar);
  if (status === "ready") return null;

  if (status === "starting") {
    return (
      <div
        role="status"
        data-testid="sidecar-notice"
        className="mb-4 flex items-center gap-3 rounded-lg border border-yellow-500/30 bg-yellow-500/10 px-4 py-3 text-sm text-yellow-200"
      >
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-yellow-400 border-t-transparent" />
        <div>
          <div className="font-medium">Starting the pipeline process</div>
          <div className="text-xs text-yellow-200/70">
            The packaged pipeline unpacks on first launch and can take up to a minute. Archive searches will run as
            soon as it is ready.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      role="alert"
      data-testid="sidecar-notice"
      className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200"
    >
      <div className="font-medium">Pipeline process is not running</div>
      <div className="mt-1 break-words text-xs text-red-200/80">
        {sidecar.message ?? "The Python sidecar did not start. See docs/BUILDING.md for the JWST_SIDECAR_BIN and JWST_DEV_SIDECAR overrides."}
      </div>
    </div>
  );
}
