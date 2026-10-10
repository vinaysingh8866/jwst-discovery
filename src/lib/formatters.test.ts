import { describe, it, expect } from "vitest";
import { formatRA, formatDec, formatFlux, formatDuration, formatSize } from "./formatters";

describe("formatRA", () => {
  it("formats zero as 00h 00m 00.00s", () => {
    expect(formatRA(0)).toBe("00h 00m 00.00s");
  });

  it("converts degrees to sexagesimal hours", () => {
    expect(formatRA(180)).toBe("12h 00m 00.00s");
    // 53.126 deg = 3.54173 h = 3h 32m 30.24s
    expect(formatRA(53.126)).toBe("03h 32m 30.24s");
  });

  it("zero-pads seconds below ten", () => {
    // 15.025 deg = 1.001667 h = 1h 00m 06.00s
    expect(formatRA(15.025)).toBe("01h 00m 06.00s");
  });
});

describe("formatDec", () => {
  it("prefixes positive declinations with +", () => {
    expect(formatDec(0)).toBe("+00° 00' 00.0\"");
    expect(formatDec(62.238)).toBe("+62° 14' 16.8\"");
  });

  it("prefixes negative declinations with - and pads seconds", () => {
    expect(formatDec(-27.802)).toBe("-27° 48' 07.2\"");
  });
});

describe("formatFlux", () => {
  it("uses exponential notation for very small or very large values", () => {
    expect(formatFlux(0.005)).toBe("5.00e-3");
    expect(formatFlux(12345)).toBe("1.23e+4");
    expect(formatFlux(0)).toBe("0.00e+0");
  });

  it("uses three decimals for mid-range values", () => {
    expect(formatFlux(1.5)).toBe("1.500");
    expect(formatFlux(-0.25)).toBe("-0.250");
    expect(formatFlux(9999.9)).toBe("9999.900");
  });
});

describe("formatDuration", () => {
  it("shows sub-minute durations in seconds with one decimal", () => {
    expect(formatDuration(0)).toBe("0.0s");
    expect(formatDuration(45.26)).toBe("45.3s");
  });

  it("splits minutes and whole seconds", () => {
    expect(formatDuration(60)).toBe("1m 0s");
    expect(formatDuration(125)).toBe("2m 5s");
    expect(formatDuration(3599.6)).toBe("59m 60s");
  });
});

describe("formatSize", () => {
  it("formats megabytes below 1 GiB as whole MB", () => {
    expect(formatSize(0.4)).toBe("0 MB");
    expect(formatSize(512)).toBe("512 MB");
    expect(formatSize(1023.9)).toBe("1024 MB");
  });

  it("formats 1024 MB and above as GB with one decimal", () => {
    expect(formatSize(1024)).toBe("1.0 GB");
    expect(formatSize(2560)).toBe("2.5 GB");
  });
});
