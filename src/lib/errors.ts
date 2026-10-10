/**
 * Turn anything a `catch` can receive into a readable string.
 *
 * Tauri delivers Rust `AppError`s to `invoke().catch()` as externally tagged
 * objects such as `{"Io": "/path: No such file or directory (os error 2)"}`;
 * `String(e)` on those yields "[object Object]".
 */
export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  if (err && typeof err === "object") {
    const obj = err as Record<string, unknown>;
    if (typeof obj.message === "string") return obj.message;
    const keys = Object.keys(obj);
    if (keys.length === 1 && typeof obj[keys[0]] === "string") return `${keys[0]}: ${obj[keys[0]]}`;
    try {
      return JSON.stringify(err);
    } catch {
      /* fall through */
    }
  }
  return String(err);
}

/** True for "file or directory does not exist" errors from the Rust host. */
export function isNotFound(err: unknown): boolean {
  return /no such file or directory|os error 2\b|not found/i.test(errorMessage(err));
}
