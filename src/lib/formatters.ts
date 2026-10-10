export function formatRA(ra: number): string {
  const hours = ra / 15;
  const h = Math.floor(hours);
  const m = Math.floor((hours - h) * 60);
  const s = ((hours - h) * 60 - m) * 60;
  return h.toString().padStart(2, "0") + "h " + m.toString().padStart(2, "0") + "m " + s.toFixed(2).padStart(5, "0") + "s";
}

export function formatDec(dec: number): string {
  const sign = dec >= 0 ? "+" : "-";
  const absDec = Math.abs(dec);
  const d = Math.floor(absDec);
  const m = Math.floor((absDec - d) * 60);
  const s = ((absDec - d) * 60 - m) * 60;
  return sign + d.toString().padStart(2, "0") + "\u00b0 " + m.toString().padStart(2, "0") + "\u0027 " + s.toFixed(1).padStart(4, "0") + "\u0022";
}

export function formatFlux(flux: number): string {
  if (Math.abs(flux) < 0.01 || Math.abs(flux) >= 1e4) {
    return flux.toExponential(2);
  }
  return flux.toFixed(3);
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return seconds.toFixed(1) + "s";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m + "m " + s.toFixed(0) + "s";
}


/** Format a size given in megabytes as "123 MB" or "1.2 GB". */
export function formatSize(mb: number): string {
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(0)} MB`;
}
