import { useEffect, useState } from "react";
import { useConfigStore } from "@/stores/configStore";
import { resolveWorkspace } from "@/lib/workspace";
import { errorMessage } from "@/lib/errors";

export interface WorkspaceResolution {
  /** Absolute workspace directory, or `null` while resolving or on failure. */
  workspace: string | null;
  /** Human-readable reason the workspace could not be resolved. */
  error: string | null;
}

/**
 * Resolve the current workspace directory from the pipeline config.
 *
 * Returns `{ workspace: null, error: null }` until the Tauri path API has
 * answered; callers should treat a `null` workspace as "not ready yet" and
 * render `error` when it is set instead of falling back to a guess.
 */
export function useWorkspace(): WorkspaceResolution {
  const configWorkspace = useConfigStore((s) => s.config.workspace);
  const targetField = useConfigStore((s) => s.config.target_field);
  const [state, setState] = useState<WorkspaceResolution>({ workspace: null, error: null });

  useEffect(() => {
    let cancelled = false;
    resolveWorkspace({ workspace: configWorkspace, target_field: targetField })
      .then((workspace) => {
        if (!cancelled) setState({ workspace, error: null });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({ workspace: null, error: errorMessage(err) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [configWorkspace, targetField]);

  return state;
}
