/**
 * Routing for `sidecar:error` events.
 *
 * Every one-shot request to the Python sidecar (QueryMAST, QueryMastMissions,
 * ListKnownFields, ...) gets a request id, which the `invoke()` that issued it
 * resolves to. When such a request fails, the host emits a single
 * `sidecar:error` event carrying that id. Each panel keeps the id of its own
 * pending request and only reacts to the error that matches it, so a failure
 * in one panel never shows up under another panel's banner.
 */
export interface SidecarErrorEvent {
  /** Id of the request that failed; "unknown" when the sidecar had none. */
  requestId?: string | null;
  message: string;
}

/**
 * True when `event` reports the failure of the request `pendingId`.
 *
 * Returns false when there is no pending request (nothing of ours can have
 * failed) or when the ids differ (somebody else's request failed).
 */
export function isOwnError(event: SidecarErrorEvent, pendingId: string | null | undefined): boolean {
  if (!pendingId) return false;
  return event.requestId === pendingId;
}
