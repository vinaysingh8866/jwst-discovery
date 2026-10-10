import { homeDir } from "@tauri-apps/api/path";
import { invoke } from "@tauri-apps/api/core";
import { errorMessage } from "@/lib/errors";

/**
 * Workspace path resolution.
 *
 * Every per-field workspace lives under `~/.jwst-discovery/workspaces/<field>`.
 * The home directory is resolved through the Tauri path API exactly once and
 * cached; a failure is surfaced as a rejected promise (never silently replaced
 * by a fallback such as `/tmp`), and the cache is cleared so a later call can
 * retry.
 */

/** Directory, relative to the user's home, that holds all field workspaces. */
export const WORKSPACES_SUBDIR = ".jwst-discovery/workspaces";

/** Minimal slice of the pipeline config needed to locate a workspace. */
export interface WorkspaceConfig {
  /** Explicit override; when set it is used verbatim. */
  workspace: string | null;
  /** Human-readable target name, sanitized into a directory name. */
  target_field: string;
}

export class WorkspaceError extends Error {
  constructor(message: string, readonly cause?: unknown) {
    super(message);
    this.name = "WorkspaceError";
  }
}

let rootPromise: Promise<string> | null = null;

/**
 * Turn a target field name into a filesystem-safe directory name.
 * Mirrors the sanitization used by the Python sidecar.
 */
export function sanitizeFieldName(field: string): string {
  return field.replace(/[^a-zA-Z0-9_-]/g, "_");
}

function describe(err: unknown): string {
  return errorMessage(err);
}

/**
 * Resolve `~/.jwst-discovery/workspaces`. The underlying `homeDir()` call is
 * made once per session and its result cached. Rejects with a
 * {@link WorkspaceError} if the home directory cannot be determined.
 */
export function getWorkspaceRoot(): Promise<string> {
  if (!rootPromise) {
    // The Rust host owns the answer (it honours JWST_WORKSPACE_ROOT, like the
    // CLI). Fall back to ~/.jwst-discovery/workspaces if the command is
    // unavailable, e.g. in tests or an older host.
    rootPromise = Promise.resolve()
      .then(() => invoke<string>("get_workspace_root"))
      .then((root) => {
        const trimmed = root.replace(/[\\/]+$/, "");
        if (!trimmed) throw new Error("workspace root resolved to an empty path");
        return trimmed;
      })
      .catch(() =>
        homeDir().then((home) => {
          const trimmed = home.replace(/[\\/]+$/, "");
          if (!trimmed) {
            throw new Error("home directory resolved to an empty path");
          }
          return `${trimmed}/${WORKSPACES_SUBDIR}`;
        }),
      )
      .catch((err: unknown) => {
        // Drop the failed promise so a subsequent call can retry.
        rootPromise = null;
        throw new WorkspaceError(
          `Could not resolve the workspace root directory: ${describe(err)}`,
          err,
        );
      });
  }
  return rootPromise;
}

/** Resolve the workspace directory for a given target field. */
export async function getWorkspacePath(field: string): Promise<string> {
  const root = await getWorkspaceRoot();
  return `${root}/${sanitizeFieldName(field)}`;
}

/**
 * Resolve the workspace for a pipeline config: an explicit `config.workspace`
 * wins, otherwise the path is derived from `config.target_field`.
 */
export function resolveWorkspace(config: WorkspaceConfig): Promise<string> {
  if (config.workspace) return Promise.resolve(config.workspace);
  return getWorkspacePath(config.target_field);
}

/** Clear the cached root. Intended for tests. */
export function resetWorkspaceRootCache(): void {
  rootPromise = null;
}
