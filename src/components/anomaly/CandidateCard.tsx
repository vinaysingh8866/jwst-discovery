import type { AnomalyCandidate } from "@/stores/resultsStore";
import { formatRA, formatDec } from "@/lib/formatters";
import { MW_CLASS_LABELS } from "@/lib/constants";
import { CutoutImage } from "@/components/shared/CutoutImage";
import { InteractiveSED } from "./InteractiveSED";
import {
  ScoreBadge,
  SnBadge,
  TagBadge,
  ClassBadge,
  AgnBar,
  MwClassBadge,
  RedshiftBadge,
  CLASS_LABELS,
} from "./Badges";

/** Expandable card for one anomaly candidate: header badges, SED, cutouts and property table. */
export function CandidateCard({
  candidate,
  workspace,
  isExpanded,
  onToggle,
  filters,
}: {
  candidate: AnomalyCandidate;
  workspace: string;
  isExpanded: boolean;
  onToggle: () => void;
  filters: string[];
}) {
  const isNovelOnly = candidate.novel && candidate.rank == null;
  return (
    <div
      data-testid="candidate-card"
      className={`bg-gray-900 rounded-lg border transition-colors ${
        isExpanded ? "border-blue-500/50 ring-1 ring-blue-500/20"
        : isNovelOnly ? "border-emerald-500/30 hover:border-emerald-500/50"
        : "border-gray-800 hover:border-gray-700"
      }`}
    >
      {/* Header */}
      <button
        onClick={onToggle}
        className="w-full text-left p-3 flex items-start gap-3"
      >
        {/* Detection cutout thumbnail */}
        <div className="w-16 h-16 rounded overflow-hidden flex-shrink-0 bg-gray-800">
          {candidate.cutout_detection && (
            <CutoutImage
              workspace={workspace}
              relativePath={candidate.cutout_detection}
              alt={`Source ${candidate.id}`}
            />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            {candidate.rank != null ? (
              <span className="text-sm font-mono font-medium">#{candidate.rank}</span>
            ) : (
              <span className="text-sm font-mono font-medium text-emerald-400">NEW</span>
            )}
            <span className="text-xs text-gray-500">ID {candidate.id}</span>
            {candidate.anomaly_score != null && isFinite(candidate.anomaly_score) && (
              <ScoreBadge score={candidate.anomaly_score} />
            )}
            <SnBadge maxSn={candidate.max_sn} nBandsSn5={candidate.n_bands_sn5} />
            <AgnBar confidence={candidate.agn_confidence} />
          </div>

          <div className="flex flex-wrap gap-1 mb-1">
            {candidate.novel && (
              <TagBadge label="NOVEL" color="bg-emerald-500/20 text-emerald-400 border border-emerald-500/30" />
            )}
            <ClassBadge objectClass={candidate.object_class} />
            <MwClassBadge mwClass={candidate.mw_class} />
            <RedshiftBadge z={candidate.z_phot_eazy} quality={candidate.z_phot_quality} catalogZ={candidate.best_photo_z} />
            {candidate.dropout_flag && candidate.dropout_flag !== "none" && (
              <TagBadge label={candidate.dropout_flag} color="bg-purple-500/20 text-purple-400" />
            )}
            {candidate.is_agn_candidate && !candidate.mw_class && (
              <TagBadge label="AGN/LRD" color="bg-pink-500/20 text-pink-400" />
            )}
            {candidate.red_excess && (
              <TagBadge label="RED EXCESS" color="bg-red-500/20 text-red-400" />
            )}
            {candidate.ned_match && (
              <TagBadge label="NED" color="bg-blue-500/20 text-blue-400" />
            )}
            {candidate.gaia_match && (
              <TagBadge label="GAIA" color="bg-cyan-500/20 text-cyan-400" />
            )}
            {!candidate.mw_class && (
              <>
                {candidate.chandra_match && (
                  <TagBadge label="X-ray" color="bg-orange-500/20 text-orange-400" />
                )}
                {candidate.xmm_match && !candidate.chandra_match && (
                  <TagBadge label="XMM" color="bg-orange-500/20 text-orange-300" />
                )}
                {candidate.wise_match && (
                  <TagBadge
                    label={candidate.wise_w1w2 != null && Number(candidate.wise_w1w2) > 0.8 ? "WISE AGN" : "WISE"}
                    color={candidate.wise_w1w2 != null && Number(candidate.wise_w1w2) > 0.8
                      ? "bg-pink-500/20 text-pink-400" : "bg-amber-500/20 text-amber-400"}
                  />
                )}
                {candidate.vlass_match && (
                  <TagBadge label="Radio" color="bg-teal-500/20 text-teal-400" />
                )}
                {candidate.spitzer_match && (
                  <TagBadge label="Spitzer" color="bg-rose-500/20 text-rose-300" />
                )}
                {candidate.erosita_match && (
                  <TagBadge label="eROSITA" color="bg-sky-500/20 text-sky-400" />
                )}
              </>
            )}
          </div>

          <div className="text-[11px] text-gray-500 font-mono">
            {formatRA(candidate.ra)} {formatDec(candidate.dec)}
          </div>
        </div>

        <svg
          className={`w-4 h-4 text-gray-500 transition-transform flex-shrink-0 mt-1 ${isExpanded ? "rotate-180" : ""}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Expanded detail */}
      {isExpanded && (
        <div className="border-t border-gray-800 p-3 space-y-3">
          {/* SED plot — interactive */}
          <div>
            <p className="text-xs text-gray-500 mb-1">Spectral Energy Distribution</p>
            <InteractiveSED candidate={candidate} filters={filters} />
          </div>

          {/* Filter cutouts */}
          <div>
            <p className="text-xs text-gray-500 mb-1">Multi-band Cutouts</p>
            <div className="grid grid-cols-6 gap-1">
              {/* Detection */}
              {candidate.cutout_detection && (
                <div className="text-center">
                  <div className="w-full aspect-square bg-gray-800 rounded overflow-hidden">
                    <CutoutImage
                      workspace={workspace}
                      relativePath={candidate.cutout_detection}
                      alt="Detection"
                    />
                  </div>
                  <span className="text-[10px] text-gray-500">Det</span>
                </div>
              )}
              {/* Per-filter */}
              {filters.slice(0, 11).map((filt) => {
                const path = `cutouts/src${String(candidate.id).padStart(6, "0")}_${filt}.png`;
                return (
                  <div key={filt} className="text-center">
                    <div className="w-full aspect-square bg-gray-800 rounded overflow-hidden">
                      <CutoutImage workspace={workspace} relativePath={path} alt={filt} />
                    </div>
                    <span className="text-[10px] text-gray-500">{filt}</span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Properties table */}
          <div>
            <p className="text-xs text-gray-500 mb-1">Properties</p>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              {candidate.anomaly_score != null && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Anomaly Score</span>
                  <span className="font-mono">{candidate.anomaly_score.toFixed(4)}</span>
                </div>
              )}
              {candidate.stellarity != null && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Stellarity</span>
                  <span className="font-mono">{Number(candidate.stellarity).toFixed(3)}</span>
                </div>
              )}
              {candidate.ellipticity != null && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Ellipticity</span>
                  <span className="font-mono">{Number(candidate.ellipticity).toFixed(3)}</span>
                </div>
              )}
              {candidate.r_half_pix != null && (
                <div className="flex justify-between">
                  <span className="text-gray-500">r_half (pix)</span>
                  <span className="font-mono">{Number(candidate.r_half_pix).toFixed(2)}</span>
                </div>
              )}
              {candidate.max_sn != null && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Max S/N</span>
                  <span className="font-mono">{Number(candidate.max_sn).toFixed(1)}</span>
                </div>
              )}
              {candidate.median_sn != null && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Median S/N</span>
                  <span className="font-mono">{Number(candidate.median_sn).toFixed(1)}</span>
                </div>
              )}
              {candidate.n_bands_sn5 != null && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Bands S/N&gt;=5</span>
                  <span className="font-mono">{candidate.n_bands_sn5}</span>
                </div>
              )}
              {candidate.object_class && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Class</span>
                  <span className="font-mono">{CLASS_LABELS[candidate.object_class] || candidate.object_class}</span>
                </div>
              )}
              {candidate.z_phot_eazy != null && isFinite(candidate.z_phot_eazy) && candidate.z_phot_eazy > 0 && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Photo-z (EAZY)</span>
                  <span className="font-mono">
                    {Number(candidate.z_phot_eazy).toFixed(3)}
                    {candidate.z_phot_quality && (
                      <span className={`ml-1 text-[10px] ${
                        candidate.z_phot_quality === "good" ? "text-green-400" :
                        candidate.z_phot_quality === "uncertain" ? "text-yellow-400" : "text-red-400"
                      }`}>
                        [{candidate.z_phot_quality}]
                      </span>
                    )}
                  </span>
                </div>
              )}
              {candidate.z_phot_chi2 != null && isFinite(candidate.z_phot_chi2 as number) && (candidate.z_phot_chi2 as number) > 0 && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Photo-z chi2</span>
                  <span className="font-mono">{Number(candidate.z_phot_chi2).toFixed(1)}</span>
                </div>
              )}
              {candidate.best_photo_z != null && isFinite(candidate.best_photo_z) && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Catalog photo-z</span>
                  <span className="font-mono">{Number(candidate.best_photo_z).toFixed(3)}</span>
                </div>
              )}
              {candidate.best_spec_z != null && isFinite(candidate.best_spec_z) && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Spec-z</span>
                  <span className="font-mono">{Number(candidate.best_spec_z).toFixed(4)}</span>
                </div>
              )}
              {candidate.agn_confidence != null && candidate.agn_confidence > 0 && (
                <div className="flex justify-between">
                  <span className="text-gray-500">AGN Confidence</span>
                  <span className={`font-mono ${Number(candidate.agn_confidence) >= 0.6 ? "text-red-400" : Number(candidate.agn_confidence) >= 0.3 ? "text-orange-400" : ""}`}>
                    {(Number(candidate.agn_confidence) * 100).toFixed(0)}%
                  </span>
                </div>
              )}
              {candidate.mw_class && (
                <div className="flex justify-between">
                  <span className="text-gray-500">MW Class</span>
                  <span className="font-mono">{MW_CLASS_LABELS[candidate.mw_class] || candidate.mw_class}</span>
                </div>
              )}
              {candidate.z_agree != null && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Photo-z Agreement</span>
                  <span className={`font-mono ${candidate.z_catastrophic ? "text-red-400 font-bold" : candidate.z_agree ? "text-green-400" : "text-orange-400"}`}>
                    {candidate.z_catastrophic ? "CATASTROPHIC" : candidate.z_agree ? "Agree" : "Disagree"}
                    {candidate.z_source && <span className="text-gray-500 ml-1">(vs {candidate.z_source})</span>}
                  </span>
                </div>
              )}
              {candidate.mu_best != null && isFinite(Number(candidate.mu_best)) && Number(candidate.mu_best) > 1.0 && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Lensing</span>
                  <span className="font-mono text-cyan-400">
                    {"\u00b5"} = {Number(candidate.mu_best).toFixed(2)}
                    <span className="text-gray-500 ml-1">({(2.5 * Math.log10(Number(candidate.mu_best))).toFixed(1)} mag)</span>
                  </span>
                </div>
              )}
              {candidate.stellar_mass != null && isFinite(Number(candidate.stellar_mass)) && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Stellar Mass</span>
                  <span className="font-mono">{Number(candidate.stellar_mass).toExponential(2)} M{"\u2609"}</span>
                </div>
              )}
              {candidate.sfr != null && isFinite(Number(candidate.sfr)) && (
                <div className="flex justify-between">
                  <span className="text-gray-500">SFR</span>
                  <span className="font-mono">{Number(candidate.sfr).toFixed(1)} M{"\u2609"}/yr</span>
                </div>
              )}
              {candidate.ned_name && candidate.ned_name !== "" && (
                <div className="flex justify-between col-span-2">
                  <span className="text-gray-500">NED</span>
                  <span className="font-mono truncate ml-2">{candidate.ned_name} ({candidate.ned_type})</span>
                </div>
              )}
              {candidate.chandra_flux != null && isFinite(Number(candidate.chandra_flux)) && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Chandra X-ray</span>
                  <span className="font-mono">{Number(candidate.chandra_flux).toExponential(2)}</span>
                </div>
              )}
              {candidate.wise_w1w2 != null && isFinite(Number(candidate.wise_w1w2)) && (
                <div className="flex justify-between">
                  <span className="text-gray-500">WISE W1-W2</span>
                  <span className={`font-mono ${Number(candidate.wise_w1w2) > 0.8 ? "text-pink-400" : ""}`}>
                    {Number(candidate.wise_w1w2).toFixed(2)}
                    {Number(candidate.wise_w1w2) > 0.8 ? " (AGN)" : ""}
                  </span>
                </div>
              )}
              {candidate.vlass_flux != null && isFinite(Number(candidate.vlass_flux)) && (
                <div className="flex justify-between">
                  <span className="text-gray-500">Radio 3GHz</span>
                  <span className="font-mono">{Number(candidate.vlass_flux).toFixed(3)} mJy</span>
                </div>
              )}
            </div>

            {/* Magnitudes & S/N */}
            <p className="text-xs text-gray-500 mt-2 mb-1">Magnitudes & S/N</p>
            <div className="flex flex-wrap gap-2 text-xs">
              {filters.map((filt) => {
                const magKey = `mag_${filt}`;
                const snKey = `sn_${filt}`;
                const mag = candidate[magKey];
                const sn = candidate[snKey];
                if (mag == null && sn == null) return null;
                return (
                  <div key={filt} className="bg-gray-800 px-2 py-0.5 rounded font-mono">
                    <span className="text-gray-500">{filt}</span>{" "}
                    {mag != null && <span>{Number(mag).toFixed(2)}</span>}
                    {sn != null && (
                      <span className={`ml-1 ${Number(sn) >= 5 ? "text-green-400" : "text-orange-400"}`}>
                        ({Number(sn).toFixed(1)})
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
