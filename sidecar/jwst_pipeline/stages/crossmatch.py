"""Stage 6: Cross-match with external catalogs + artifact validation.

Queries NED, SDSS, Gaia, SIMBAD, HSC, HCV, PanSTARRS (legacy APIs) plus
Chandra, CatWISE, XMM-Newton, VLASS, Spitzer, eROSITA (TAP APIs), then
matches against all local reference catalogs.  Flags novel sources and runs
artifact / edge-of-mosaic validation.
"""
import logging
import math
import socket

import numpy as np
import polars as pl
from astropy import units as u
from astropy.coordinates import SkyCoord, search_around_sky
from astropy.io import fits

from jwst_pipeline.catalog_registry import (
    detect_field,
    get_catalogs_for_field,
    get_ref_catalogs_dir,
)
from jwst_pipeline.stages.base import PipelineStage
from jwst_pipeline.utils.catalog import read_catalog, write_catalog

logger = logging.getLogger(__name__)

# Global socket timeout to prevent any network call from blocking forever.
socket.setdefaulttimeout(60)


def _galactic_longitude(ra_deg: float, dec_deg: float) -> float:
    """Approximate galactic longitude (degrees) from equatorial coordinates."""
    ra_r = math.radians(ra_deg)
    dec_r = math.radians(dec_deg)
    ra_gp = math.radians(192.8595)   # RA of galactic north pole
    dec_gp = math.radians(27.1284)   # Dec of galactic north pole
    l_np = math.radians(122.932)     # l of north celestial pole
    sin_b = (math.sin(dec_gp) * math.sin(dec_r) +
             math.cos(dec_gp) * math.cos(dec_r) * math.cos(ra_r - ra_gp))
    cos_b = math.sqrt(max(0.0, 1.0 - sin_b ** 2))
    if cos_b < 1e-10:
        return 0.0
    sin_l = math.cos(dec_r) * math.sin(ra_r - ra_gp) / cos_b
    cos_l = ((math.sin(dec_r) - math.sin(dec_gp) * sin_b) /
             (math.cos(dec_gp) * cos_b))
    return math.degrees(l_np - math.atan2(sin_l, cos_l)) % 360


class CrossmatchStage(PipelineStage):
    name = "crossmatch"

    # ------------------------------------------------------------------ #
    #  Main entry point                                                    #
    # ------------------------------------------------------------------ #
    def run(self) -> dict:
        cat_dir = self.workspace / "catalogs"

        # ----- Load characterized catalog -----
        self.progress(0.0, "Loading characterized catalog...")
        self.check_cancel()

        char_path = cat_dir / "characterized_catalog.parquet"
        if not char_path.exists():
            raise FileNotFoundError(
                f"Characterized catalog not found: {char_path}. "
                "Run the characterize stage first."
            )

        cat = read_catalog(char_path)
        n_sources = cat.height
        logger.info("Loaded characterized catalog: %d sources", n_sources)

        ra = cat["ra"].to_numpy().astype(np.float64)
        dec = cat["dec"].to_numpy().astype(np.float64)

        # ----- Build SkyCoord & field geometry -----
        self.progress(0.02, "Building sky coordinates...")
        our_coords = SkyCoord(ra=ra * u.deg, dec=dec * u.deg)

        ra_center = float(np.nanmedian(ra))
        dec_center = float(np.nanmedian(dec))
        field_center = SkyCoord(ra=ra_center * u.deg, dec=dec_center * u.deg)

        ra_span = np.nanmax(ra) - np.nanmin(ra)
        dec_span = np.nanmax(dec) - np.nanmin(dec)
        field_radius_deg = max(ra_span, dec_span) / 2.0 * 1.1
        field_radius = field_radius_deg * u.deg
        logger.info(
            "Field center: RA=%.4f, Dec=%.4f, radius=%.3f deg",
            ra_center, dec_center, field_radius_deg,
        )
        self.progress(0.05, f"Field: ({ra_center:.3f}, {dec_center:.3f}), r={field_radius_deg:.2f} deg")

        # ----- Detect sky field for local catalog filtering -----
        field_name = self.config.get("field_name") or detect_field(ra_center, dec_center)
        if field_name:
            logger.info("Detected field: %s", field_name)
        else:
            logger.warning("Unknown field — only API catalogs will be used")
        local_catalogs = get_catalogs_for_field(field_name)
        logger.info("Using %d local catalogs for field %s", len(local_catalogs), field_name)

        xmatch_radius = float(self.config.get("crossmatch_radius_arcsec", 2.0))
        xmatch_sep = xmatch_radius * u.arcsec

        match_stats = {}

        # ============================================================== #
        #  API catalogs — legacy (NED, SDSS, Gaia, SIMBAD, HSC, HCV, PS1) #
        # ============================================================== #
        self.progress(0.05, "Querying NED...")
        self.check_cancel()
        ned_match, ned_name, ned_type, s = self._query_ned(
            field_center, field_radius_deg, our_coords, xmatch_sep, n_sources,
        )
        match_stats.update(s)
        self.progress(0.08, f"NED: {match_stats.get('ned_matches', 0)} matches")

        self.progress(0.08, "Querying SDSS...")
        self.check_cancel()
        sdss_match, sdss_specz, s = self._query_sdss(
            field_center, field_radius_deg, our_coords, xmatch_sep, n_sources,
        )
        match_stats.update(s)
        self.progress(0.11, f"SDSS: {match_stats.get('sdss_matches', 0)} matches")

        self.progress(0.11, "Querying Gaia...")
        self.check_cancel()
        gaia_match, gaia_pmra, gaia_result, s = self._query_gaia(
            field_center, field_radius, our_coords, xmatch_sep, n_sources,
        )
        match_stats.update(s)
        self.progress(0.14, f"Gaia: {match_stats.get('gaia_matches', 0)} matches")

        self.progress(0.14, "Querying SIMBAD...")
        self.check_cancel()
        simbad_match, simbad_otype, s = self._query_simbad(
            field_center, field_radius, our_coords, xmatch_sep, n_sources,
        )
        match_stats.update(s)
        self.progress(0.17, f"SIMBAD: {match_stats.get('simbad_matches', 0)} matches")

        self.progress(0.17, "Querying HSC...")
        self.check_cancel()
        hsc_match, hsc_nfilters, s = self._query_hsc(
            ra_center, dec_center, field_radius_deg, our_coords, xmatch_sep, n_sources,
        )
        match_stats.update(s)
        self.progress(0.20, f"HSC: {match_stats.get('hsc_matches', 0)} matches")

        self.progress(0.20, "Querying HCV...")
        self.check_cancel()
        hcv_match, hcv_autoclass, s = self._query_hcv(
            ra_center, dec_center, field_radius_deg, our_coords, xmatch_sep, n_sources,
        )
        match_stats.update(s)
        self.progress(0.23, f"HCV: {match_stats.get('hcv_matches', 0)} matches")

        self.progress(0.23, "Querying PanSTARRS...")
        self.check_cancel()
        ps1_match, ps1_gmag, s = self._query_panstarrs(
            ra_center, dec_center, field_radius_deg, our_coords, xmatch_sep, n_sources,
        )
        match_stats.update(s)
        self.progress(0.26, f"PanSTARRS: {match_stats.get('ps1_matches', 0)} matches")

        # ============================================================== #
        #  API catalogs — multi-wavelength TAP (Chandra, WISE, XMM, etc.) #
        # ============================================================== #
        self.progress(0.26, "Querying Chandra X-ray...")
        self.check_cancel()
        chandra_match, chandra_flux, chandra_hardness, s = self._query_chandra(
            ra_center, dec_center, field_radius_deg, our_coords, n_sources,
        )
        match_stats.update(s)
        self.progress(0.29, f"Chandra: {match_stats.get('chandra_matches', 0)} matches")

        self.progress(0.29, "Querying CatWISE2020...")
        self.check_cancel()
        wise_match, wise_w1, wise_w2, wise_w1w2, s = self._query_catwise(
            ra_center, dec_center, field_radius_deg, our_coords, n_sources,
        )
        match_stats.update(s)
        self.progress(0.32, f"CatWISE: {match_stats.get('wise_matches', 0)} matches")

        self.progress(0.32, "Querying XMM-Newton...")
        self.check_cancel()
        xmm_match, xmm_flux, xmm_det_ml, s = self._query_xmm(
            ra_center, dec_center, field_radius_deg, our_coords, n_sources,
        )
        match_stats.update(s)
        self.progress(0.35, f"XMM: {match_stats.get('xmm_matches', 0)} matches")

        self.progress(0.35, "Querying VLASS radio...")
        self.check_cancel()
        vlass_match, vlass_flux, s = self._query_vlass(
            ra_center, dec_center, field_radius_deg, our_coords, n_sources,
        )
        match_stats.update(s)
        self.progress(0.38, f"VLASS: {match_stats.get('vlass_matches', 0)} matches")

        self.progress(0.38, "Querying Spitzer SEIP...")
        self.check_cancel()
        spitzer_match, spitzer_ch1, spitzer_ch2, s = self._query_spitzer(
            ra_center, dec_center, field_radius_deg, our_coords, n_sources,
        )
        match_stats.update(s)
        self.progress(0.41, f"Spitzer: {match_stats.get('spitzer_matches', 0)} matches")

        self.progress(0.41, "Querying eROSITA...")
        self.check_cancel()
        erosita_match, erosita_flux, s = self._query_erosita(
            ra_center, dec_center, field_radius_deg, our_coords, n_sources,
        )
        match_stats.update(s)
        self.progress(0.44, f"eROSITA: {match_stats.get('erosita_matches', 0)} matches")

        # ============================================================== #
        #  Local catalogs — data-driven loop from registry                 #
        # ============================================================== #
        n_local = len(local_catalogs)
        local_results = {}  # name -> (match_array, z_array_or_None, mu_array_or_None)

        for i, cat_def in enumerate(local_catalogs):
            frac = 0.44 + i * (0.24 / max(n_local, 1))
            self.progress(frac, f"Matching {cat_def['label']}...")
            self.check_cancel()

            match_arr, z_arr, mu_arr = self._query_local_catalog(
                cat_def, our_coords, xmatch_sep, n_sources,
            )
            local_results[cat_def["name"]] = (match_arr, z_arr, mu_arr)
            n_matched = int(np.sum(match_arr))
            match_stats[f"{cat_def['name']}_matches"] = n_matched
            logger.info("%s: %d matches", cat_def["label"], n_matched)

        # ============================================================== #
        #  Artifact validation                                             #
        # ============================================================== #
        self.progress(0.68, "Running artifact validation...")
        self.check_cancel()
        val = self._validate_sources(cat, n_sources)
        bright_star, bright_dist = self._check_bright_stars(
            our_coords, gaia_result, n_sources,
        )
        ell = cat["ellipticity"].to_numpy().astype(np.float64) if "ellipticity" in cat.columns else np.zeros(n_sources)
        rh = (cat["r_half_pix"].to_numpy().astype(np.float64) if "r_half_pix" in cat.columns
              else np.full(n_sources, 99.0))
        ell_thresh = float(self.config.get("artifact_ellipticity_threshold", 0.8))
        size_thresh = float(self.config.get("artifact_size_threshold_pix", 1.5))
        is_artifact_morph = (ell > ell_thresh) & (rh < size_thresh) & np.isfinite(ell) & np.isfinite(rh)

        # Mosaic-edge artifact detection: large extended sources at coverage boundaries.
        # SEP detects the sharp brightness transition at mosaic edges as huge
        # elongated "sources" with spuriously high S/N.
        nan_frac = val.get("nan_frac", np.zeros(n_sources))
        fin_ell = np.isfinite(ell)
        fin_rh = np.isfinite(rh)
        is_mosaic_edge = np.zeros(n_sources, dtype=bool)
        # Very large + elongated (real faint galaxies are never 15+ px AND elongated)
        is_mosaic_edge |= (rh > 15.0) & (ell > 0.5) & fin_rh & fin_ell
        # Extremely large regardless of shape (no real faint source is 20+ px half-light)
        is_mosaic_edge |= (rh > 20.0) & fin_rh
        # Moderate size + sitting on a NaN boundary (>25% of neighborhood is NaN)
        is_mosaic_edge |= (nan_frac > 0.25) & (rh > 5.0) & fin_rh

        n_mosaic_edge = int(np.sum(is_mosaic_edge))
        logger.info("Mosaic-edge artifacts: %d", n_mosaic_edge)

        self.progress(0.78, f"Validation: {int(np.sum(val['at_edge']))} edge, "
                      f"{int(np.sum(bright_star))} near star, "
                      f"{int(np.sum(is_artifact_morph))} artifact morph, "
                      f"{n_mosaic_edge} mosaic edge")

        # ============================================================== #
        #  Novelty flag — union of ALL catalogs                            #
        # ============================================================== #
        self.progress(0.78, "Computing novelty flags...")
        self.check_cancel()

        any_match = (ned_match | sdss_match | gaia_match | simbad_match |
                     hsc_match | hcv_match | ps1_match |
                     chandra_match | wise_match | xmm_match |
                     vlass_match | spitzer_match | erosita_match)
        for match_arr, _, _ in local_results.values():
            any_match |= match_arr

        # Novel = not matched AND not an artifact AND has real photometry
        novel = ~any_match & ~is_mosaic_edge & ~is_artifact_morph

        # ---- Photometry quality filters for novel flag ----
        # 1) Require at least 1 band with S/N > 0 (kills ghost detections
        #    that SEP finds in the stack but have all-NaN forced photometry).
        max_sn_col = "max_sn"
        if max_sn_col in cat.columns:
            max_sn_arr = cat[max_sn_col].to_numpy().astype(np.float64)
            has_any_sn = np.isfinite(max_sn_arr) & (max_sn_arr > 0)
            n_ghost = int(np.sum(novel & ~has_any_sn))
            novel = novel & has_any_sn
            logger.info("Photometry filter: removed %d ghost detections (all-NaN S/N)", n_ghost)
        else:
            logger.warning("max_sn column missing — skipping ghost detection filter")

        # 2) Require at least N bands with finite flux (default 3).
        #    Sources with < 3 valid bands are almost always noise spikes.
        min_valid_key = "novel_min_valid_bands"
        min_valid_bands = int(self.config.get(min_valid_key, 3))
        n_valid_bands = np.zeros(n_sources, dtype=np.int32)
        for col in cat.columns:
            if col.startswith("flux_F") and not col.startswith("fluxerr"):
                vals = cat[col].to_numpy().astype(np.float64)
                n_valid_bands += (np.isfinite(vals) & (vals > 0)).astype(np.int32)
        has_enough_bands = n_valid_bands >= min_valid_bands
        n_too_few = int(np.sum(novel & ~has_enough_bands))
        novel = novel & has_enough_bands
        logger.info("Band count filter: removed %d novel sources with < %d valid bands",
                     n_too_few, min_valid_bands)

        # 3) Compact noise-spike filter: very elongated AND tiny objects
        #    that slipped past the artifact_morph threshold.
        compact_noise = (ell > 0.8) & (rh < 2.0) & fin_ell & fin_rh
        n_compact_noise = int(np.sum(novel & compact_noise))
        novel = novel & ~compact_noise
        logger.info("Compact noise filter: removed %d novel sources (ell>0.8 & rh<2px)",
                     n_compact_noise)

        # 4) Require at least 2 bands with S/N >= 5.
        #    Sources with only 1 high-S/N band are usually noise spikes
        #    that SEP found in the stack.
        n_bands_sn5_col = "n_bands_sn5"
        if n_bands_sn5_col in cat.columns:
            n_bands_sn5_arr = cat[n_bands_sn5_col].to_numpy().astype(np.int64)
            has_multi_band = n_bands_sn5_arr >= 2
            n_single_band = int(np.sum(novel & ~has_multi_band))
            novel = novel & has_multi_band
            logger.info("Multi-band S/N filter: removed %d novel sources with < 2 bands at S/N>=5",
                         n_single_band)

        # 5) Require max S/N >= 7 in at least one band.
        #    Faint noise fluctuations with S/N 2-6 are unreliable detections.
        if max_sn_col in cat.columns:
            strong_detection = np.isfinite(max_sn_arr) & (max_sn_arr >= 7.0)
            n_faint = int(np.sum(novel & ~strong_detection))
            novel = novel & strong_detection
            logger.info("Strong detection filter: removed %d novel sources with max S/N < 7",
                         n_faint)

        n_novel = int(np.sum(novel))
        n_known = int(np.sum(any_match))
        n_total_catalogs = 13 + n_local
        logger.info("Novel sources: %d, Known: %d, Filtered/artifacts excluded: %d (%d catalogs)",
                     n_novel, n_known, int(np.sum(~any_match) - n_novel), n_total_catalogs)
        match_stats["novel_sources"] = n_novel
        match_stats["artifact_excluded"] = int(np.sum(~any_match) - n_novel)

        # n_catalogs_matched (per source)
        n_catalogs_matched = (
            ned_match.astype(int) + sdss_match.astype(int) +
            gaia_match.astype(int) + simbad_match.astype(int) +
            hsc_match.astype(int) + hcv_match.astype(int) +
            ps1_match.astype(int) +
            chandra_match.astype(int) + wise_match.astype(int) +
            xmm_match.astype(int) + vlass_match.astype(int) +
            spitzer_match.astype(int) + erosita_match.astype(int)
        )
        for match_arr, _, _ in local_results.values():
            n_catalogs_matched += match_arr.astype(int)

        # Best redshifts — aggregate by z_type from registry
        best_spec_z = np.full(n_sources, np.nan)
        best_photo_z = np.full(n_sources, np.nan)

        # SDSS is an API spec-z source
        fill = np.isnan(best_spec_z) & np.isfinite(sdss_specz)
        best_spec_z[fill] = sdss_specz[fill]

        # Local catalogs — z and magnification
        mu_best = np.full(n_sources, np.nan)
        for cat_def in local_catalogs:
            _, z_arr, mu_arr = local_results[cat_def["name"]]
            if z_arr is not None:
                target = best_spec_z if cat_def.get("z_type") == "spec" else best_photo_z
                fill = np.isnan(target) & np.isfinite(z_arr)
                target[fill] = z_arr[fill]
            if mu_arr is not None:
                # Take the largest mu (strongest lens model value)
                better = np.isfinite(mu_arr) & (mu_arr > 0) & (
                    np.isnan(mu_best) | (mu_arr > mu_best)
                )
                mu_best[better] = mu_arr[better]
        n_with_mu = int(np.sum(np.isfinite(mu_best) & (mu_best > 1.0)))
        if n_with_mu > 0:
            logger.info("Lensing magnification: %d sources with mu > 1.0 (median=%.2f)",
                        n_with_mu, float(np.nanmedian(mu_best[np.isfinite(mu_best) & (mu_best > 1.0)])))

        # ============================================================== #
        #  Build output catalog                                            #
        # ============================================================== #
        self.progress(0.85, "Building crossmatch catalog...")
        self.check_cancel()

        out_dict: dict[str, list] = {}
        for col in cat.columns:
            out_dict[col] = cat[col].to_list()

        # --- API catalog columns (named, with extra metadata) ---
        out_dict["ned_match"] = ned_match.tolist()
        out_dict["ned_name"] = [str(s) for s in ned_name]
        out_dict["ned_type"] = [str(s) for s in ned_type]
        out_dict["sdss_match"] = sdss_match.tolist()
        out_dict["sdss_specz"] = sdss_specz.tolist()
        out_dict["gaia_match"] = gaia_match.tolist()
        out_dict["gaia_pmra"] = gaia_pmra.tolist()
        out_dict["simbad_match"] = simbad_match.tolist()
        out_dict["simbad_otype"] = [str(s) for s in simbad_otype]
        out_dict["hsc_match"] = hsc_match.tolist()
        out_dict["hsc_nfilters"] = hsc_nfilters.tolist()
        out_dict["hcv_match"] = hcv_match.tolist()
        out_dict["hcv_autoclass"] = hcv_autoclass.tolist()
        out_dict["ps1_match"] = ps1_match.tolist()
        out_dict["ps1_gmag"] = ps1_gmag.tolist()

        # --- Multi-wavelength API catalog columns ---
        out_dict["chandra_match"] = chandra_match.tolist()
        out_dict["chandra_flux"] = chandra_flux.tolist()
        out_dict["chandra_hardness"] = chandra_hardness.tolist()
        out_dict["wise_match"] = wise_match.tolist()
        out_dict["wise_w1"] = wise_w1.tolist()
        out_dict["wise_w2"] = wise_w2.tolist()
        out_dict["wise_w1w2"] = wise_w1w2.tolist()
        out_dict["xmm_match"] = xmm_match.tolist()
        out_dict["xmm_flux"] = xmm_flux.tolist()
        out_dict["xmm_det_ml"] = xmm_det_ml.tolist()
        out_dict["vlass_match"] = vlass_match.tolist()
        out_dict["vlass_flux"] = vlass_flux.tolist()
        out_dict["spitzer_match"] = spitzer_match.tolist()
        out_dict["spitzer_ch1"] = spitzer_ch1.tolist()
        out_dict["spitzer_ch2"] = spitzer_ch2.tolist()
        out_dict["erosita_match"] = erosita_match.tolist()
        out_dict["erosita_flux"] = erosita_flux.tolist()

        # --- Local catalog match flags (dynamic) ---
        for cat_def in local_catalogs:
            name = cat_def["name"]
            match_arr, z_arr, _ = local_results[name]
            out_dict[f"{name}_match"] = match_arr.tolist()
            if z_arr is not None:
                out_dict[f"{name}_z"] = z_arr.tolist()

        # --- Validation ---
        out_dict["n_bands_covered"] = val["n_bands_covered"].tolist()
        out_dict["at_edge"] = val["at_edge"].tolist()
        out_dict["near_bright_star"] = bright_star.tolist()
        out_dict["bright_star_dist"] = bright_dist.tolist()
        out_dict["is_artifact_morph"] = is_artifact_morph.tolist()
        out_dict["is_mosaic_edge"] = is_mosaic_edge.tolist()
        out_dict["nan_frac"] = nan_frac.tolist()

        # --- Aggregated ---
        out_dict["n_catalogs_matched"] = n_catalogs_matched.tolist()
        out_dict["best_spec_z"] = best_spec_z.tolist()
        out_dict["best_photo_z"] = best_photo_z.tolist()
        out_dict["mu_best"] = mu_best.tolist()
        out_dict["novel"] = novel.tolist()

        xmatch_cat = pl.DataFrame(out_dict)

        # Write
        self.progress(0.93, "Writing crossmatch catalog...")
        xmatch_path = cat_dir / "crossmatch_catalog.parquet"
        write_catalog(xmatch_cat, xmatch_path)
        logger.info(
            "Wrote crossmatch catalog: %s (%d sources, %d columns)",
            xmatch_path.name, xmatch_cat.height, xmatch_cat.width,
        )

        self.progress(1.0, f"Cross-matched {n_sources} sources: {n_known} known, {n_novel} novel")

        # Build return dict
        result = {
            "sources_crossmatched": n_sources,
            "ned_matches": match_stats.get("ned_matches", 0),
            "sdss_matches": match_stats.get("sdss_matches", 0),
            "gaia_matches": match_stats.get("gaia_matches", 0),
            "simbad_matches": match_stats.get("simbad_matches", 0),
            "hsc_matches": match_stats.get("hsc_matches", 0),
            "hcv_matches": match_stats.get("hcv_matches", 0),
            "ps1_matches": match_stats.get("ps1_matches", 0),
            "chandra_matches": match_stats.get("chandra_matches", 0),
            "wise_matches": match_stats.get("wise_matches", 0),
            "xmm_matches": match_stats.get("xmm_matches", 0),
            "vlass_matches": match_stats.get("vlass_matches", 0),
            "spitzer_matches": match_stats.get("spitzer_matches", 0),
            "erosita_matches": match_stats.get("erosita_matches", 0),
            "novel_sources": n_novel,
            "edge_sources": int(np.sum(val["at_edge"])),
            "near_bright_star": int(np.sum(bright_star)),
            "artifact_morph": int(np.sum(is_artifact_morph)),
            "mosaic_edge_artifacts": n_mosaic_edge,
            "artifact_excluded": match_stats.get("artifact_excluded", 0),
            "catalog_path": str(xmatch_path),
        }
        # Add per-local-catalog match counts
        for cat_def in local_catalogs:
            result[f"{cat_def['name']}_matches"] = match_stats.get(
                f"{cat_def['name']}_matches", 0)

        return result

    # ------------------------------------------------------------------ #
    #  API catalog query helpers                                           #
    # ------------------------------------------------------------------ #
    def _query_ned(self, field_center, field_radius_deg, our_coords, xmatch_sep, n):
        ned_match = np.zeros(n, dtype=bool)
        ned_name = np.full(n, "", dtype=object)
        ned_type = np.full(n, "", dtype=object)
        stats = {}
        try:
            from astroquery.ipac.ned import Ned
            Ned.TIMEOUT = 300
            field_radius_arcmin = field_radius_deg * 60.0
            ned_result = Ned.query_region(
                field_center, radius=field_radius_arcmin * u.arcmin,
            )
            n_ned = len(ned_result)
            logger.info("NED returned %d objects", n_ned)
            if n_ned > 0:
                ned_ra = np.array(ned_result["RA"], dtype=np.float64)
                ned_dec = np.array(ned_result["DEC"], dtype=np.float64)
                ned_coords = SkyCoord(ra=ned_ra * u.deg, dec=ned_dec * u.deg)
                idx_our, idx_ned, sep2d, _ = search_around_sky(
                    our_coords, ned_coords, xmatch_sep,
                )
                for i, j in zip(idx_our, idx_ned, strict=True):
                    ned_match[i] = True
                    ned_name[i] = str(ned_result["Object Name"][j])
                    ned_type[i] = str(ned_result["Type"][j])
                stats["ned_matches"] = int(np.sum(ned_match))
            else:
                stats["ned_matches"] = 0
        except Exception as e:
            logger.warning("NED query failed: %s", e)
            stats["ned_matches"] = 0
            stats["ned_error"] = str(e)
        return ned_match, ned_name, ned_type, stats

    def _query_sdss(self, field_center, field_radius_deg, our_coords, xmatch_sep, n):
        sdss_match = np.zeros(n, dtype=bool)
        sdss_specz = np.full(n, np.nan)
        stats = {}
        try:
            from astroquery.sdss import SDSS
            sdss_radius = min(field_radius_deg * 60.0, 2.9) * u.arcmin
            sdss_result = SDSS.query_region(
                field_center, radius=sdss_radius, spectro=True, data_release=18,
            )
            if sdss_result is not None and len(sdss_result) > 0:
                n_sdss = len(sdss_result)
                logger.info("SDSS returned %d spectroscopic objects", n_sdss)
                sdss_coords = SkyCoord(
                    ra=sdss_result["ra"] * u.deg, dec=sdss_result["dec"] * u.deg,
                )
                idx_our, idx_sdss, sep2d, _ = search_around_sky(
                    our_coords, sdss_coords, xmatch_sep,
                )
                for i, j in zip(idx_our, idx_sdss, strict=True):
                    sdss_match[i] = True
                    z = sdss_result["z"][j]
                    if np.isfinite(z):
                        sdss_specz[i] = float(z)
                stats["sdss_matches"] = int(np.sum(sdss_match))
            else:
                stats["sdss_matches"] = 0
        except Exception as e:
            logger.warning("SDSS query failed: %s", e)
            stats["sdss_matches"] = 0
            stats["sdss_error"] = str(e)
        return sdss_match, sdss_specz, stats

    def _query_gaia(self, field_center, field_radius, our_coords, xmatch_sep, n):
        gaia_match = np.zeros(n, dtype=bool)
        gaia_pmra = np.full(n, np.nan)
        gaia_result = None
        stats = {}
        try:
            from astroquery.gaia import Gaia
            Gaia.ROW_LIMIT = 50000
            job = Gaia.cone_search_async(field_center, radius=field_radius)
            gaia_result = job.get_results()
            if gaia_result is not None and len(gaia_result) > 0:
                n_gaia = len(gaia_result)
                logger.info("Gaia returned %d objects", n_gaia)
                gaia_ra = np.array(gaia_result["ra"], dtype=np.float64)
                gaia_dec = np.array(gaia_result["dec"], dtype=np.float64)
                gaia_coords = SkyCoord(ra=gaia_ra * u.deg, dec=gaia_dec * u.deg)
                idx_our, idx_gaia, sep2d, _ = search_around_sky(
                    our_coords, gaia_coords, xmatch_sep,
                )
                for i, j in zip(idx_our, idx_gaia, strict=True):
                    gaia_match[i] = True
                    pm = gaia_result["pmra"][j]
                    if np.isfinite(pm):
                        gaia_pmra[i] = float(pm)
                stats["gaia_matches"] = int(np.sum(gaia_match))
            else:
                stats["gaia_matches"] = 0
        except Exception as e:
            logger.warning("Gaia query failed: %s", e)
            stats["gaia_matches"] = 0
            stats["gaia_error"] = str(e)
        return gaia_match, gaia_pmra, gaia_result, stats

    def _query_simbad(self, field_center, field_radius, our_coords, xmatch_sep, n):
        simbad_match = np.zeros(n, dtype=bool)
        simbad_otype = np.full(n, "", dtype=object)
        stats = {}
        try:
            from astroquery.simbad import Simbad
            custom = Simbad()
            custom.TIMEOUT = 120
            custom.ROW_LIMIT = 50000
            custom.add_votable_fields("otype")
            result = custom.query_region(field_center, radius=field_radius)
            if result is not None and len(result) > 0:
                n_sim = len(result)
                logger.info("SIMBAD returned %d objects", n_sim)
                sim_ra = np.array(result["ra"], dtype=np.float64)
                sim_dec = np.array(result["dec"], dtype=np.float64)
                sim_coords = SkyCoord(ra=sim_ra * u.deg, dec=sim_dec * u.deg)
                idx_our, idx_sim, sep2d, _ = search_around_sky(
                    our_coords, sim_coords, xmatch_sep,
                )
                otype_col = "otype" if "otype" in result.colnames else "OTYPE"
                for i, j in zip(idx_our, idx_sim, strict=True):
                    simbad_match[i] = True
                    if otype_col in result.colnames:
                        simbad_otype[i] = str(result[otype_col][j])
                stats["simbad_matches"] = int(np.sum(simbad_match))
            else:
                stats["simbad_matches"] = 0
        except Exception as e:
            logger.warning("SIMBAD query failed: %s", e)
            stats["simbad_matches"] = 0
            stats["simbad_error"] = str(e)
        return simbad_match, simbad_otype, stats

    def _query_hsc(self, ra_center, dec_center, field_radius_deg, our_coords, xmatch_sep, n):
        """Query the Hubble Source Catalog v3 via REST API."""
        hsc_match = np.zeros(n, dtype=bool)
        hsc_nfilters = np.zeros(n, dtype=np.int32)
        stats = {}
        try:
            import requests
            radius_deg = min(field_radius_deg, 0.5)  # HSC max 0.5 deg
            url = "https://catalogs.mast.stsci.edu/api/v0.1/hsc/v3/summary/magaper2.csv"
            params = {
                "ra": ra_center,
                "dec": dec_center,
                "sr": radius_deg,
                "columns": "[MatchID,MatchRA,MatchDec,NumFilters,NumImages]",
            }
            resp = requests.get(url, params=params, timeout=300)
            resp.raise_for_status()

            lines = [ln.strip() for ln in resp.text.strip().split("\n")]
            if len(lines) <= 1:
                stats["hsc_matches"] = 0
                return hsc_match, hsc_nfilters, stats

            # Parse CSV (first line = header)
            header = lines[0].split(",")
            ra_idx = header.index("MatchRA")
            dec_idx = header.index("MatchDec")
            nfilt_idx = header.index("NumFilters")

            hsc_ra_list = []
            hsc_dec_list = []
            hsc_nf_list = []
            for line in lines[1:]:
                cols = line.split(",")
                if len(cols) <= max(ra_idx, dec_idx, nfilt_idx):
                    continue
                try:
                    hsc_ra_list.append(float(cols[ra_idx]))
                    hsc_dec_list.append(float(cols[dec_idx]))
                    hsc_nf_list.append(int(cols[nfilt_idx]))
                except (ValueError, IndexError):
                    continue

            n_hsc = len(hsc_ra_list)
            logger.info("HSC returned %d sources", n_hsc)
            if n_hsc > 0:
                hsc_ra = np.array(hsc_ra_list, dtype=np.float64)
                hsc_dec = np.array(hsc_dec_list, dtype=np.float64)
                hsc_nf = np.array(hsc_nf_list, dtype=np.int32)
                hsc_coords = SkyCoord(ra=hsc_ra * u.deg, dec=hsc_dec * u.deg)
                idx_our, idx_hsc, sep2d, _ = search_around_sky(
                    our_coords, hsc_coords, xmatch_sep,
                )
                for i, j in zip(idx_our, idx_hsc, strict=True):
                    hsc_match[i] = True
                    hsc_nfilters[i] = hsc_nf[j]
                stats["hsc_matches"] = int(np.sum(hsc_match))
            else:
                stats["hsc_matches"] = 0
        except Exception as e:
            logger.warning("HSC query failed: %s", e)
            stats["hsc_matches"] = 0
            stats["hsc_error"] = str(e)
        return hsc_match, hsc_nfilters, stats

    def _query_hcv(self, ra_center, dec_center, field_radius_deg, our_coords, xmatch_sep, n):
        """Query the Hubble Catalog of Variables via REST API."""
        hcv_match = np.zeros(n, dtype=bool)
        hcv_autoclass = np.zeros(n, dtype=np.int32)
        stats = {}
        try:
            import requests
            radius_deg = min(field_radius_deg, 0.5)
            url = "https://catalogs.mast.stsci.edu/api/v0.1/hsc/v3/hcvsummary.csv"
            params = {
                "ra": ra_center,
                "dec": dec_center,
                "sr": radius_deg,
                "columns": "[MatchID,RA,Dec,AutoClass,ExpertClass,MAD]",
            }
            resp = requests.get(url, params=params, timeout=300)
            resp.raise_for_status()

            lines = [ln.strip() for ln in resp.text.strip().split("\n")]
            if len(lines) <= 1:
                stats["hcv_matches"] = 0
                return hcv_match, hcv_autoclass, stats

            header = lines[0].split(",")
            ra_idx = header.index("RA")
            dec_idx = header.index("Dec")
            ac_idx = header.index("AutoClass")

            # HCV returns one row per filter per source — deduplicate by MatchID
            mid_idx = header.index("MatchID")
            seen: dict[str, tuple[float, float, int]] = {}  # MatchID -> (ra, dec, max_autoclass)
            for line in lines[1:]:
                cols = line.split(",")
                if len(cols) <= max(ra_idx, dec_idx, ac_idx, mid_idx):
                    continue
                try:
                    mid = cols[mid_idx]
                    r = float(cols[ra_idx])
                    d = float(cols[dec_idx])
                    ac = int(cols[ac_idx])
                    if mid in seen:
                        seen[mid] = (r, d, max(seen[mid][2], ac))
                    else:
                        seen[mid] = (r, d, ac)
                except (ValueError, IndexError):
                    continue

            hcv_ra_list = [v[0] for v in seen.values()]
            hcv_dec_list = [v[1] for v in seen.values()]
            hcv_ac_list = [v[2] for v in seen.values()]

            n_hcv = len(hcv_ra_list)
            logger.info("HCV returned %d variable candidates", n_hcv)
            if n_hcv > 0:
                hcv_ra = np.array(hcv_ra_list, dtype=np.float64)
                hcv_dec = np.array(hcv_dec_list, dtype=np.float64)
                hcv_ac = np.array(hcv_ac_list, dtype=np.int32)
                hcv_coords = SkyCoord(ra=hcv_ra * u.deg, dec=hcv_dec * u.deg)
                idx_our, idx_hcv, sep2d, _ = search_around_sky(
                    our_coords, hcv_coords, xmatch_sep,
                )
                for i, j in zip(idx_our, idx_hcv, strict=True):
                    hcv_match[i] = True
                    hcv_autoclass[i] = hcv_ac[j]
                stats["hcv_matches"] = int(np.sum(hcv_match))
            else:
                stats["hcv_matches"] = 0
        except Exception as e:
            logger.warning("HCV query failed: %s", e)
            stats["hcv_matches"] = 0
            stats["hcv_error"] = str(e)
        return hcv_match, hcv_autoclass, stats

    def _query_panstarrs(self, ra_center, dec_center, field_radius_deg, our_coords, xmatch_sep, n):
        """Query PanSTARRS DR2 via REST CSV API (Dec > -30 only)."""
        ps1_match = np.zeros(n, dtype=bool)
        ps1_gmag = np.full(n, np.nan)
        stats = {}

        if dec_center < -30.0:
            logger.info("PanSTARRS skipped: Dec=%.2f < -30 (outside PS1 coverage)", dec_center)
            stats["ps1_matches"] = 0
            stats["ps1_skipped"] = True
            return ps1_match, ps1_gmag, stats

        try:
            import requests
            radius_deg = min(field_radius_deg, 0.5)
            url = "https://catalogs.mast.stsci.edu/api/v0.1/panstarrs/dr2/mean.csv"
            params = {
                "ra": ra_center,
                "dec": dec_center,
                "radius": radius_deg,
                "nDetections.gte": 2,
                "columns": "[objID,raMean,decMean,gMeanPSFMag]",
                "pagesize": 500001,
            }
            resp = requests.get(url, params=params, timeout=300)
            resp.raise_for_status()

            lines = [ln.strip() for ln in resp.text.strip().split("\n")]
            if len(lines) <= 1:
                stats["ps1_matches"] = 0
                return ps1_match, ps1_gmag, stats

            header = lines[0].split(",")
            ra_idx = header.index("raMean")
            dec_idx = header.index("decMean")
            gmag_idx = header.index("gMeanPSFMag")

            ps1_ra_list = []
            ps1_dec_list = []
            ps1_g_list = []
            for line in lines[1:]:
                cols = line.split(",")
                if len(cols) <= max(ra_idx, dec_idx, gmag_idx):
                    continue
                try:
                    ps1_ra_list.append(float(cols[ra_idx]))
                    ps1_dec_list.append(float(cols[dec_idx]))
                    g = float(cols[gmag_idx])
                    # PS1 uses -999 as sentinel for missing magnitudes
                    ps1_g_list.append(g if g > -99 else np.nan)
                except (ValueError, IndexError):
                    continue

            n_ps1 = len(ps1_ra_list)
            logger.info("PanSTARRS returned %d sources", n_ps1)

            if n_ps1 > 0:
                ps1_ra = np.array(ps1_ra_list, dtype=np.float64)
                ps1_dec = np.array(ps1_dec_list, dtype=np.float64)
                ps1_g = np.array(ps1_g_list, dtype=np.float64)
                ps1_coords = SkyCoord(ra=ps1_ra * u.deg, dec=ps1_dec * u.deg)

                idx_our, idx_ps1, sep2d, _ = search_around_sky(
                    our_coords, ps1_coords, xmatch_sep,
                )
                for i, j in zip(idx_our, idx_ps1, strict=True):
                    ps1_match[i] = True
                    g = ps1_g[j]
                    if np.isfinite(g):
                        ps1_gmag[i] = float(g)
                stats["ps1_matches"] = int(np.sum(ps1_match))
            else:
                stats["ps1_matches"] = 0
        except Exception as e:
            logger.warning("PanSTARRS query failed: %s", e)
            stats["ps1_matches"] = 0
            stats["ps1_error"] = str(e)
        return ps1_match, ps1_gmag, stats

    # ------------------------------------------------------------------ #
    #  Multi-wavelength TAP catalog queries                                #
    # ------------------------------------------------------------------ #
    def _tap_cone_search(self, tap_url, table, ra_col, dec_col, extra_cols,
                         ra_center, dec_center, radius_deg,
                         timeout=300, max_rows=500000):
        """Run an ADQL cone search via pyvo TAP. Returns result table or None."""
        try:
            import pyvo
            svc = pyvo.dal.TAPService(tap_url)
            adql = (
                f"SELECT TOP {max_rows} {ra_col}, {dec_col}, {extra_cols} "
                f"FROM {table} "
                f"WHERE 1=CONTAINS(POINT('ICRS', {ra_col}, {dec_col}), "
                f"CIRCLE('ICRS', {ra_center}, {dec_center}, {radius_deg}))"
            )
            logger.debug("TAP query: %s → %s", tap_url, adql)
            result = svc.search(adql, maxrec=max_rows)
            return result
        except Exception as e:
            logger.warning("TAP query to %s failed: %s", tap_url, e)
            return None

    def _query_chandra(self, ra_center, dec_center, field_radius_deg, our_coords, n):
        """Query Chandra Source Catalog 2.1 via Simple Cone Search."""
        chandra_match = np.zeros(n, dtype=bool)
        chandra_flux = np.full(n, np.nan)
        chandra_hardness = np.full(n, np.nan)
        stats = {}
        try:
            import pyvo
            svc = pyvo.dal.SCSService("http://cda.cfa.harvard.edu/csc21scs/coneSearch")
            result = svc.search(pos=(ra_center, dec_center),
                                radius=min(field_radius_deg, 0.5))
            if result is None or len(result) == 0:
                stats["chandra_matches"] = 0
                return chandra_match, chandra_flux, chandra_hardness, stats

            tbl = result.to_table()
            n_res = len(tbl)
            logger.info("Chandra CSC 2.1: %d sources", n_res)
            c_ra = np.array(tbl["ra"], dtype=np.float64)
            c_dec = np.array(tbl["dec"], dtype=np.float64)
            c_coords = SkyCoord(ra=c_ra * u.deg, dec=c_dec * u.deg)

            xm_sep = 2.0 * u.arcsec  # Chandra ~0.5" on-axis
            idx_our, idx_c, sep2d, _ = search_around_sky(our_coords, c_coords, xm_sep)
            for i, j in zip(idx_our, idx_c, strict=True):
                chandra_match[i] = True
                try:
                    fb = float(tbl["flux_aper_b"][j])
                    if np.isfinite(fb):
                        chandra_flux[i] = fb
                except (ValueError, TypeError):
                    pass
                try:
                    hm = float(tbl["hard_hm"][j])
                    if np.isfinite(hm):
                        chandra_hardness[i] = hm
                except (ValueError, TypeError):
                    pass
            stats["chandra_matches"] = int(np.sum(chandra_match))
        except Exception as e:
            logger.warning("Chandra query failed: %s", e)
            stats["chandra_matches"] = 0
            stats["chandra_error"] = str(e)
        return chandra_match, chandra_flux, chandra_hardness, stats

    def _query_catwise(self, ra_center, dec_center, field_radius_deg, our_coords, n):
        """Query CatWISE2020 via IRSA TAP (all-sky mid-IR W1/W2)."""
        wise_match = np.zeros(n, dtype=bool)
        wise_w1 = np.full(n, np.nan)
        wise_w2 = np.full(n, np.nan)
        wise_w1w2 = np.full(n, np.nan)
        stats = {}
        try:
            result = self._tap_cone_search(
                tap_url="https://irsa.ipac.caltech.edu/TAP",
                table="catwise_2020",
                ra_col="ra", dec_col="dec",
                extra_cols="w1mpro, w2mpro",
                ra_center=ra_center, dec_center=dec_center,
                radius_deg=min(field_radius_deg, 0.25),
            )
            if result is None or len(result) == 0:
                stats["wise_matches"] = 0
                return wise_match, wise_w1, wise_w2, wise_w1w2, stats

            n_res = len(result)
            logger.info("CatWISE2020: %d sources", n_res)
            tbl = result.to_table()
            w_ra = np.array(tbl["ra"], dtype=np.float64)
            w_dec = np.array(tbl["dec"], dtype=np.float64)
            w_coords = SkyCoord(ra=w_ra * u.deg, dec=w_dec * u.deg)

            xm_sep = 4.0 * u.arcsec  # WISE 6" PSF
            idx_our, idx_w, sep2d, _ = search_around_sky(our_coords, w_coords, xm_sep)
            for i, j in zip(idx_our, idx_w, strict=True):
                wise_match[i] = True
                w1 = float(tbl["w1mpro"][j])
                w2 = float(tbl["w2mpro"][j])
                if np.isfinite(w1):
                    wise_w1[i] = w1
                if np.isfinite(w2):
                    wise_w2[i] = w2
                if np.isfinite(w1) and np.isfinite(w2):
                    wise_w1w2[i] = w1 - w2
            stats["wise_matches"] = int(np.sum(wise_match))
        except Exception as e:
            logger.warning("CatWISE query failed: %s", e)
            stats["wise_matches"] = 0
            stats["wise_error"] = str(e)
        return wise_match, wise_w1, wise_w2, wise_w1w2, stats

    def _query_xmm(self, ra_center, dec_center, field_radius_deg, our_coords, n):
        """Query 4XMM-DR14 via HEASARC TAP."""
        xmm_match = np.zeros(n, dtype=bool)
        xmm_flux = np.full(n, np.nan)
        xmm_det_ml = np.full(n, np.nan)
        stats = {}
        try:
            result = self._tap_cone_search(
                tap_url="https://heasarc.gsfc.nasa.gov/xamin/vo/tap",
                table="xmmssc",
                ra_col="sc_ra", dec_col="sc_dec",
                extra_cols="ep_8_flux, sc_det_ml",
                ra_center=ra_center, dec_center=dec_center,
                radius_deg=min(field_radius_deg, 0.5),
            )
            if result is None or len(result) == 0:
                stats["xmm_matches"] = 0
                return xmm_match, xmm_flux, xmm_det_ml, stats

            n_res = len(result)
            logger.info("4XMM-DR14: %d sources", n_res)
            tbl = result.to_table()
            x_ra = np.array(tbl["sc_ra"], dtype=np.float64)
            x_dec = np.array(tbl["sc_dec"], dtype=np.float64)
            x_coords = SkyCoord(ra=x_ra * u.deg, dec=x_dec * u.deg)

            xm_sep = 5.0 * u.arcsec  # XMM position uncertainty ~2-4"
            idx_our, idx_x, sep2d, _ = search_around_sky(our_coords, x_coords, xm_sep)
            for i, j in zip(idx_our, idx_x, strict=True):
                xmm_match[i] = True
                fx = float(tbl["ep_8_flux"][j])
                if np.isfinite(fx):
                    xmm_flux[i] = fx
                ml = float(tbl["sc_det_ml"][j])
                if np.isfinite(ml):
                    xmm_det_ml[i] = ml
            stats["xmm_matches"] = int(np.sum(xmm_match))
        except Exception as e:
            logger.warning("XMM query failed: %s", e)
            stats["xmm_matches"] = 0
            stats["xmm_error"] = str(e)
        return xmm_match, xmm_flux, xmm_det_ml, stats

    def _query_vlass(self, ra_center, dec_center, field_radius_deg, our_coords, n):
        """Query VLASS Quick Look catalog via VizieR TAP (Dec > -40 only)."""
        vlass_match = np.zeros(n, dtype=bool)
        vlass_flux = np.full(n, np.nan)
        stats = {}

        if dec_center < -40.0:
            logger.info("VLASS skipped: Dec=%.2f < -40 (outside coverage)", dec_center)
            stats["vlass_matches"] = 0
            stats["vlass_skipped"] = True
            return vlass_match, vlass_flux, stats

        try:
            result = self._tap_cone_search(
                tap_url="https://tapvizier.cds.unistra.fr/TAPVizieR/tap",
                table='"J/ApJS/255/30/comp"',
                ra_col='"RAJ2000"', dec_col='"DEJ2000"',
                extra_cols='"Ftot", "Fpeak"',
                ra_center=ra_center, dec_center=dec_center,
                radius_deg=min(field_radius_deg, 0.5),
            )
            if result is None or len(result) == 0:
                stats["vlass_matches"] = 0
                return vlass_match, vlass_flux, stats

            n_res = len(result)
            logger.info("VLASS: %d radio sources", n_res)
            tbl = result.to_table()
            v_ra = np.array(tbl["RAJ2000"], dtype=np.float64)
            v_dec = np.array(tbl["DEJ2000"], dtype=np.float64)
            v_coords = SkyCoord(ra=v_ra * u.deg, dec=v_dec * u.deg)

            xm_sep = 2.0 * u.arcsec  # VLASS 2.5" resolution
            idx_our, idx_v, sep2d, _ = search_around_sky(our_coords, v_coords, xm_sep)
            for i, j in zip(idx_our, idx_v, strict=True):
                vlass_match[i] = True
                ft = float(tbl["Ftot"][j])  # mJy
                if np.isfinite(ft):
                    vlass_flux[i] = ft
            stats["vlass_matches"] = int(np.sum(vlass_match))
        except Exception as e:
            logger.warning("VLASS query failed: %s", e)
            stats["vlass_matches"] = 0
            stats["vlass_error"] = str(e)
        return vlass_match, vlass_flux, stats

    def _query_spitzer(self, ra_center, dec_center, field_radius_deg, our_coords, n):
        """Query Spitzer Legacy Photometry DR4 (SEIP) via IRSA TAP."""
        spitzer_match = np.zeros(n, dtype=bool)
        spitzer_ch1 = np.full(n, np.nan)
        spitzer_ch2 = np.full(n, np.nan)
        stats = {}
        try:
            result = self._tap_cone_search(
                tap_url="https://irsa.ipac.caltech.edu/TAP",
                table="slphotdr4",
                ra_col="ra", dec_col="dec",
                extra_cols="i1_f_ap1, i2_f_ap1",
                ra_center=ra_center, dec_center=dec_center,
                radius_deg=min(field_radius_deg, 0.25),
            )
            if result is None or len(result) == 0:
                stats["spitzer_matches"] = 0
                return spitzer_match, spitzer_ch1, spitzer_ch2, stats

            n_res = len(result)
            logger.info("Spitzer SEIP: %d sources", n_res)
            tbl = result.to_table()
            s_ra = np.array(tbl["ra"], dtype=np.float64)
            s_dec = np.array(tbl["dec"], dtype=np.float64)
            s_coords = SkyCoord(ra=s_ra * u.deg, dec=s_dec * u.deg)

            xm_sep = 3.0 * u.arcsec  # IRAC ~2" resolution
            idx_our, idx_s, sep2d, _ = search_around_sky(our_coords, s_coords, xm_sep)
            for i, j in zip(idx_our, idx_s, strict=True):
                spitzer_match[i] = True
                try:
                    ch1 = float(tbl["i1_f_ap1"][j])
                    if np.isfinite(ch1):
                        spitzer_ch1[i] = ch1
                except (ValueError, TypeError):
                    pass
                try:
                    ch2 = float(tbl["i2_f_ap1"][j])
                    if np.isfinite(ch2):
                        spitzer_ch2[i] = ch2
                except (ValueError, TypeError):
                    pass
            stats["spitzer_matches"] = int(np.sum(spitzer_match))
        except Exception as e:
            logger.warning("Spitzer SEIP query failed: %s", e)
            stats["spitzer_matches"] = 0
            stats["spitzer_error"] = str(e)
        return spitzer_match, spitzer_ch1, spitzer_ch2, stats

    def _query_erosita(self, ra_center, dec_center, field_radius_deg, our_coords, n):
        """Query eROSITA eRASS1 via HEASARC TAP (western galactic hemisphere only)."""
        erosita_match = np.zeros(n, dtype=bool)
        erosita_flux = np.full(n, np.nan)
        stats = {}

        gal_l = _galactic_longitude(ra_center, dec_center)
        if gal_l < 180.0:
            logger.info("eROSITA skipped: galactic l=%.1f < 180 (eastern hemisphere)", gal_l)
            stats["erosita_matches"] = 0
            stats["erosita_skipped"] = True
            return erosita_match, erosita_flux, stats

        try:
            result = self._tap_cone_search(
                tap_url="https://heasarc.gsfc.nasa.gov/xamin/vo/tap",
                table="erass1main",
                ra_col="ra", dec_col="dec",
                extra_cols="b1_flux, b0_detect_likelihood",
                ra_center=ra_center, dec_center=dec_center,
                radius_deg=min(field_radius_deg, 0.5),
            )
            if result is None or len(result) == 0:
                stats["erosita_matches"] = 0
                return erosita_match, erosita_flux, stats

            n_res = len(result)
            logger.info("eROSITA eRASS1: %d sources", n_res)
            tbl = result.to_table()
            e_ra = np.array(tbl["ra"], dtype=np.float64)
            e_dec = np.array(tbl["dec"], dtype=np.float64)
            e_coords = SkyCoord(ra=e_ra * u.deg, dec=e_dec * u.deg)

            xm_sep = 5.0 * u.arcsec  # eROSITA ~5" position uncertainty
            idx_our, idx_e, sep2d, _ = search_around_sky(our_coords, e_coords, xm_sep)
            for i, j in zip(idx_our, idx_e, strict=True):
                erosita_match[i] = True
                fx = float(tbl["b1_flux"][j])
                if np.isfinite(fx):
                    erosita_flux[i] = fx
            stats["erosita_matches"] = int(np.sum(erosita_match))
        except Exception as e:
            logger.warning("eROSITA query failed: %s", e)
            stats["erosita_matches"] = 0
            stats["erosita_error"] = str(e)
        return erosita_match, erosita_flux, stats

    # ------------------------------------------------------------------ #
    #  Local catalog matching (data-driven)                                #
    # ------------------------------------------------------------------ #
    def _query_local_catalog(self, cat_def, our_coords, xmatch_sep, n):
        """Match against a locally-cached parquet catalog.

        Returns (match_array, z_array_or_None, mu_array_or_None).
        """
        match = np.zeros(n, dtype=bool)
        z_arr = None
        mu_arr = None

        ref_dir = get_ref_catalogs_dir()
        local_path = ref_dir / f"{cat_def['name']}.parquet"

        if not local_path.exists():
            logger.warning("Local catalog %s not found at %s (run download_reference_catalogs.py)",
                           cat_def["name"], local_path)
            return match, z_arr, mu_arr

        try:
            tbl = pl.read_parquet(local_path)
            n_cat = tbl.height
            logger.info("Local catalog %s: %d sources", cat_def["label"], n_cat)

            if n_cat == 0:
                return match, z_arr, mu_arr

            ra_col = cat_def.get("ra_col", "RAJ2000")
            dec_col = cat_def.get("dec_col", "DEJ2000")
            cat_ra = tbl[ra_col].to_numpy().astype(np.float64)
            cat_dec = tbl[dec_col].to_numpy().astype(np.float64)
            cat_coords = SkyCoord(ra=cat_ra * u.deg, dec=cat_dec * u.deg)

            # Pre-extract z column if available
            z_col_arr = None
            z_col_name = cat_def.get("z_col", "z")
            if z_col_name and z_col_name in tbl.columns:
                z_col_arr = tbl[z_col_name].to_numpy().astype(np.float64)
                z_arr = np.full(n, np.nan)
            elif "z" in tbl.columns:
                z_col_arr = tbl["z"].to_numpy().astype(np.float64)
                z_arr = np.full(n, np.nan)

            # Pre-extract magnification column if available
            mu_col_arr = None
            if "mu" in tbl.columns:
                mu_col_arr = tbl["mu"].to_numpy().astype(np.float64)
                mu_arr = np.full(n, np.nan)

            idx_our, idx_cat, sep2d, _ = search_around_sky(
                our_coords, cat_coords, xmatch_sep,
            )
            for i, j in zip(idx_our, idx_cat, strict=True):
                match[int(i)] = True
                if z_col_arr is not None and z_arr is not None:
                    val = z_col_arr[int(j)]
                    if np.isfinite(val):
                        z_arr[int(i)] = val
                if mu_col_arr is not None and mu_arr is not None:
                    val = mu_col_arr[int(j)]
                    if np.isfinite(val) and val > 0:
                        mu_arr[int(i)] = val

        except Exception as e:
            logger.warning("Local catalog %s failed: %s", cat_def["name"], e)

        return match, z_arr, mu_arr

    # ------------------------------------------------------------------ #
    #  Artifact / validation helpers                                       #
    # ------------------------------------------------------------------ #
    def _validate_sources(self, cat, n_sources):
        """Check band coverage at each source position (vectorized).

        Also computes NaN fraction in a neighborhood around each source on
        the detection image to identify mosaic-edge artifacts.
        """
        proc_dir = self.workspace / "processed"
        x = cat["x"].to_numpy().astype(np.float64)
        y = cat["y"].to_numpy().astype(np.float64)

        n_bands_covered = np.zeros(n_sources, dtype=np.int32)
        total_bands = 0

        # Prefer reproj files; fall back to bksub if none exist
        band_files = sorted(proc_dir.glob("*_reproj.fits"))
        if not band_files:
            band_files = sorted(proc_dir.glob("*_bksub.fits"))

        for fp in band_files:
            total_bands += 1
            try:
                with fits.open(fp, memmap=True) as hdul:
                    data = hdul[0].data
                    ny, nx = data.shape
                    ix = np.clip(np.round(x).astype(int), 0, nx - 1)
                    iy = np.clip(np.round(y).astype(int), 0, ny - 1)
                    in_bounds = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
                    pixel_vals = data[iy, ix]
                    valid = in_bounds & np.isfinite(pixel_vals)
                    n_bands_covered += valid.astype(np.int32)
            except Exception as e:
                logger.warning("Coverage check failed for %s: %s", fp.name, e)

        coverage_frac = float(self.config.get("min_band_coverage_fraction", 0.5))
        at_edge = n_bands_covered < max(1, int(total_bands * coverage_frac))

        logger.info(
            "Validation: %d bands checked (%s), %d edge sources (<%d%% coverage)",
            total_bands,
            "reproj" if any("reproj" in fp.name for fp in band_files) else "bksub",
            int(np.sum(at_edge)), int(coverage_frac * 100),
        )

        # --- NaN neighborhood check on detection image ---
        nan_frac = np.zeros(n_sources, dtype=np.float64)
        det_path = proc_dir / "detection.fits"
        if det_path.exists():
            try:
                with fits.open(det_path, memmap=True) as hdul:
                    det_data = hdul[0].data
                    det_ny, det_nx = det_data.shape
                    box_half = 10  # 21x21 pixel neighborhood
                    ix = np.round(x).astype(int)
                    iy = np.round(y).astype(int)
                    for s in range(n_sources):
                        sx, sy = ix[s], iy[s]
                        x0 = max(0, sx - box_half)
                        x1 = min(det_nx, sx + box_half + 1)
                        y0 = max(0, sy - box_half)
                        y1 = min(det_ny, sy + box_half + 1)
                        if x1 <= x0 or y1 <= y0:
                            nan_frac[s] = 1.0
                            continue
                        patch = det_data[y0:y1, x0:x1]
                        n_pixels = patch.size
                        if n_pixels == 0:
                            nan_frac[s] = 1.0
                        else:
                            nan_frac[s] = float(np.sum(~np.isfinite(patch))) / n_pixels
                logger.info("NaN neighborhood: median=%.3f, max=%.3f, >25%%=%d sources",
                            float(np.median(nan_frac)), float(np.max(nan_frac)),
                            int(np.sum(nan_frac > 0.25)))
            except Exception as e:
                logger.warning("NaN neighborhood check failed: %s", e)

        return {
            "n_bands_covered": n_bands_covered,
            "at_edge": at_edge,
            "total_bands": total_bands,
            "nan_frac": nan_frac,
        }

    def _check_bright_stars(self, our_coords, gaia_result, n_sources):
        """Flag sources near bright Gaia stars (diffraction spike risk)."""
        near_bright = np.zeros(n_sources, dtype=bool)
        bright_dist = np.full(n_sources, np.nan)

        if gaia_result is None or len(gaia_result) == 0:
            return near_bright, bright_dist

        try:
            mag_limit = float(self.config.get("bright_star_mag_limit", 18.0))
            prox_arcsec = float(self.config.get("bright_star_proximity_arcsec", 5.0))

            gmag = np.array(gaia_result["phot_g_mean_mag"], dtype=np.float64)
            bright_mask = np.isfinite(gmag) & (gmag < mag_limit)
            if not np.any(bright_mask):
                return near_bright, bright_dist

            bright_ra = np.array(gaia_result["ra"], dtype=np.float64)[bright_mask]
            bright_dec = np.array(gaia_result["dec"], dtype=np.float64)[bright_mask]
            bright_coords = SkyCoord(ra=bright_ra * u.deg, dec=bright_dec * u.deg)

            idx_our, idx_bright, sep2d, _ = search_around_sky(
                our_coords, bright_coords, prox_arcsec * u.arcsec,
            )
            for i, sep in zip(idx_our, sep2d, strict=True):
                near_bright[i] = True
                d = sep.arcsec
                if np.isnan(bright_dist[i]) or d < bright_dist[i]:
                    bright_dist[i] = d

            logger.info(
                "Bright star check: %d sources within %.1f arcsec of G<%.1f star",
                int(np.sum(near_bright)), prox_arcsec, mag_limit,
            )
        except Exception as e:
            logger.warning("Bright star check failed: %s", e)

        return near_bright, bright_dist
