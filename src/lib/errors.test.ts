import { describe, it, expect } from "vitest";
import { errorMessage, isNotFound } from "./errors";

describe("errorMessage", () => {
  it("unwraps Error, string and message objects", () => {
    expect(errorMessage(new Error("boom"))).toBe("boom");
    expect(errorMessage("plain")).toBe("plain");
    expect(errorMessage({ message: "from object" })).toBe("from object");
  });

  it("unwraps Tauri's externally tagged AppError", () => {
    expect(errorMessage({ Io: "/x/candidates.json: No such file or directory (os error 2)" }))
      .toBe("Io: /x/candidates.json: No such file or directory (os error 2)");
    expect(errorMessage({ Sidecar: "Sidecar not running" })).toBe("Sidecar: Sidecar not running");
  });

  it("never yields [object Object]", () => {
    expect(errorMessage({ a: 1, b: 2 })).toBe('{"a":1,"b":2}');
    expect(errorMessage(undefined)).toBe("undefined");
  });
});

describe("isNotFound", () => {
  it("recognises missing-file errors from the host", () => {
    expect(isNotFound({ Io: "/x: No such file or directory (os error 2)" })).toBe(true);
    expect(isNotFound({ Io: "/x: Permission denied (os error 13)" })).toBe(false);
  });
});
