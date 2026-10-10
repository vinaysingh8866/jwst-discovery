import { AGN_THRESHOLD, MW_CLASS_LABELS, MW_CLASS_COLORS } from "@/lib/constants";

export function ScoreBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color =
    pct >= 80 ? "bg-red-500/20 text-red-400 border-red-500/30" :
    pct >= 60 ? "bg-orange-500/20 text-orange-400 border-orange-500/30" :
    pct >= 40 ? "bg-yellow-500/20 text-yellow-400 border-yellow-500/30" :
    "bg-gray-700 text-gray-400 border-gray-600";
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded border ${color}`}>
      {pct}%
    </span>
  );
}

export function SnBadge({ maxSn, nBandsSn5 }: { maxSn: number | null | undefined; nBandsSn5: number | null | undefined }) {
  if (maxSn == null) return null;
  const sn = Math.round(maxSn);
  const bands = nBandsSn5 ?? 0;

  const color =
    sn >= 10 && bands >= 3 ? "bg-green-500/20 text-green-400 border-green-500/30" :
    sn >= 5 && bands >= 2 ? "bg-yellow-500/20 text-yellow-400 border-yellow-500/30" :
    "bg-red-500/20 text-red-400 border-red-500/30";

  return (
    <span className={`text-xs px-1.5 py-0.5 rounded border ${color}`} title={`${bands} bands with S/N >= 5`}>
      S/N {sn}
    </span>
  );
}

export function TagBadge({ label, color }: { label: string; color: string }) {
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded ${color}`}>
      {label}
    </span>
  );
}

export const CLASS_COLORS: Record<string, string> = {
  star: "bg-yellow-500/20 text-yellow-400 border border-yellow-500/30",
  galaxy: "bg-blue-500/20 text-blue-400 border border-blue-500/30",
  agn_candidate: "bg-pink-500/20 text-pink-400 border border-pink-500/30",
  high_z_candidate: "bg-violet-500/20 text-violet-400 border border-violet-500/30",
  red_galaxy: "bg-red-500/20 text-red-400 border border-red-500/30",
  ambiguous: "bg-gray-600/20 text-gray-400 border border-gray-600/30",
};

export const CLASS_LABELS: Record<string, string> = {
  star: "Star",
  galaxy: "Galaxy",
  agn_candidate: "AGN",
  high_z_candidate: "High-z",
  red_galaxy: "Red Galaxy",
  ambiguous: "Ambiguous",
};

export function ClassBadge({ objectClass }: { objectClass: string | null | undefined }) {
  if (!objectClass) return null;
  const color = CLASS_COLORS[objectClass] || CLASS_COLORS.ambiguous;
  const label = CLASS_LABELS[objectClass] || objectClass;
  return <span className={`text-[10px] px-1.5 py-0.5 rounded ${color}`}>{label}</span>;
}

export function AgnBar({ confidence }: { confidence: number | null | undefined }) {
  if (confidence == null || confidence < AGN_THRESHOLD) return null;
  const pct = Math.round(confidence * 100);
  const color = confidence >= 0.6 ? "bg-red-500" : confidence >= 0.4 ? "bg-orange-500" : "bg-yellow-500";
  return (
    <span className="inline-flex items-center gap-1 text-[10px]" title={`AGN confidence: ${pct}%`}>
      <span className="w-12 h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <span className={`block h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </span>
      <span className="text-gray-400">{pct}%</span>
    </span>
  );
}

export function MwClassBadge({ mwClass }: { mwClass: string | null | undefined }) {
  if (!mwClass) return null;
  const label = MW_CLASS_LABELS[mwClass] || mwClass;
  const color = MW_CLASS_COLORS[mwClass] || "bg-gray-600 text-white";
  return <span className={`text-[10px] px-1.5 py-0.5 rounded ${color}`}>{label}</span>;
}

export function RedshiftBadge({ z, quality, catalogZ }: {
  z: number | null | undefined;
  quality: string | null | undefined;
  catalogZ?: number | null;
}) {
  const hasEazy = z != null && isFinite(z) && z > 0;
  const hasCatalog = catalogZ != null && isFinite(catalogZ) && catalogZ > 0;
  if (!hasEazy && !hasCatalog) return null;

  if (hasEazy) {
    const qColor =
      quality === "good" ? "text-green-400" :
      quality === "uncertain" ? "text-yellow-400" :
      "text-red-400";
    const qIcon = quality === "good" ? "" : quality === "uncertain" ? "~" : "?";
    return (
      <span className={`text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/20 border border-indigo-500/30 ${qColor}`}
        title={`EAZY photo-z (${quality || "unknown"})`}
      >
        z={z!.toFixed(2)}{qIcon}
      </span>
    );
  }

  return (
    <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-500/20 border border-slate-500/30 text-slate-300"
      title="Catalog photo-z (from reference catalogs)"
    >
      z={catalogZ!.toFixed(2)} cat
    </span>
  );
}
