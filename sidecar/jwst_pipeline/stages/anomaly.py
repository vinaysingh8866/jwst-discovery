"""Stage 7: Anomaly detection using Isolation Forest.

Builds a feature matrix from colors, morphology, and photometry, then runs
Isolation Forest to score every source. Outputs a ranked catalog with
anomaly scores.
"""
import logging

import numpy as np
import polars as pl
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from jwst_pipeline.stages.base import PipelineStage
from jwst_pipeline.utils.catalog import read_catalog, write_catalog

logger = logging.getLogger(__name__)


class AnomalyStage(PipelineStage):
    name = "anomaly"

    def run(self) -> dict:
        cat_dir = self.workspace / "catalogs"

        # ----- Step 1: Load crossmatch catalog -----
        self.progress(0.0, "Loading crossmatch catalog...")
        self.check_cancel()

        xm_path = cat_dir / "crossmatch_catalog.parquet"
        if not xm_path.exists():
            raise FileNotFoundError(
                f"Crossmatch catalog not found: {xm_path}. "
                "Run the crossmatch stage first."
            )

        cat = read_catalog(xm_path)
        n_sources = cat.height
        logger.info("Loaded crossmatch catalog: %d sources, %d columns", n_sources, cat.width)

        # ----- Step 1b: De-lens fluxes if magnification available -----
        if "mu_best" in cat.columns:
            mu = cat["mu_best"].to_numpy().astype(np.float64)
            lensed = np.isfinite(mu) & (mu > 1.0)
            n_lensed = int(np.sum(lensed))
            if n_lensed > 0:
                self.progress(0.03, f"De-lensing {n_lensed} sources (median mu={np.nanmedian(mu[lensed]):.2f})...")
                # De-lens fluxes for feature matrix (colors/ratios)
                for col in cat.columns:
                    if (col.startswith("flux_F") and not col.startswith("fluxerr")):
                        vals = cat[col].to_numpy().astype(np.float64)
                        intrinsic = vals.copy()
                        intrinsic[lensed] = vals[lensed] / mu[lensed]
                        cat = cat.with_columns(pl.Series(col, intrinsic))

                # Compute intrinsic mags from observed mags: mag_int = mag_obs + 2.5*log10(mu)
                mag_correction = np.zeros(n_sources)
                mag_correction[lensed] = 2.5 * np.log10(mu[lensed])
                for col in cat.columns:
                    if col.startswith("mag_") and not col.startswith("mag_intrinsic"):
                        obs_mag = cat[col].to_numpy().astype(np.float64)
                        int_mag = obs_mag + mag_correction
                        int_mag[~np.isfinite(obs_mag)] = np.nan
                        cat = cat.with_columns(pl.Series(f"mag_intrinsic_{col[4:]}", int_mag))
                logger.info("De-lensed %d sources for anomaly feature matrix", n_lensed)

        # ----- Step 2: Build feature matrix -----
        self.progress(0.05, "Building feature matrix...")
        self.check_cancel()

        # ----- Count valid bands per source -----
        flux_cols = sorted(
            c for c in cat.columns
            if c.startswith("flux_F") and not c.startswith("fluxerr") and not c.startswith("mag_intrinsic")
        )
        n_total_bands = len(flux_cols)

        n_valid_bands = np.zeros(n_sources, dtype=np.int64)
        for fc in flux_cols:
            arr = cat[fc].to_numpy().astype(np.float64)
            n_valid_bands += (np.isfinite(arr) & (arr > 0)).astype(np.int64)

        cat = cat.with_columns(pl.Series("n_valid_bands", n_valid_bands))
        logger.info(
            "Band coverage: median=%d, min=%d, max=%d (of %d total)",
            int(np.median(n_valid_bands)), int(np.min(n_valid_bands)),
            int(np.max(n_valid_bands)), n_total_bands,
        )

        # Require at least 50% of bands to have valid photometry for anomaly scoring.
        # Sources with fewer bands produce extreme colors/ratios from missing data,
        # not genuinely anomalous SEDs.
        min_bands = max(3, n_total_bands // 2)
        has_enough_bands = n_valid_bands >= min_bands
        logger.info(
            "Sources with >= %d valid bands: %d / %d",
            min_bands, int(np.sum(has_enough_bands)), n_sources,
        )

        # Select feature columns: colors, morphology, fluxes
        feature_cols = []

        # Color indices
        for col in cat.columns:
            if col.startswith("color_"):
                feature_cols.append(col)

        # Morphology
        for col in ["ellipticity", "r_half_pix", "compactness", "stellarity"]:
            if col in cat.columns:
                feature_cols.append(col)

        # S/N features
        for col in ["max_sn", "median_sn"]:
            if col in cat.columns:
                feature_cols.append(col)

        # Flux ratios between non-adjacent filters (captures SED shape).
        # Only use filter pairs where BOTH filters have >70% coverage among
        # sources with enough bands, to avoid coverage-dependent bias.
        if len(flux_cols) >= 2:
            pairs = [
                ("flux_F090W", "flux_F277W"),
                ("flux_F115W", "flux_F277W"),
                ("flux_F115W", "flux_F444W"),
                ("flux_F150W", "flux_F444W"),
                ("flux_F277W", "flux_F444W"),
            ]
            for f1, f2 in pairs:
                if f1 in cat.columns and f2 in cat.columns:
                    col_name = f"logratio_{f1[5:]}_{f2[5:]}"
                    v1 = cat[f1].to_numpy().astype(np.float64)
                    v2 = cat[f2].to_numpy().astype(np.float64)
                    ratio = np.full(n_sources, np.nan)
                    valid = (v1 > 0) & (v2 > 0) & np.isfinite(v1) & np.isfinite(v2)
                    ratio[valid] = np.log10(v1[valid] / v2[valid])
                    cat = cat.with_columns(pl.Series(col_name, ratio))
                    feature_cols.append(col_name)

        # ----- Enhanced features -----

        # SED power-law slope: fit log(flux) vs log(wavelength)
        try:
            from jwst_pipeline.stages.characterize import PIVOT_WAVELENGTH_UM
            waves = []
            slope_flux_arrays = []
            for fc in flux_cols:
                filt = fc[5:]  # strip "flux_"
                if filt in PIVOT_WAVELENGTH_UM:
                    waves.append(PIVOT_WAVELENGTH_UM[filt])
                    slope_flux_arrays.append(cat[fc].to_numpy().astype(np.float64))

            if len(waves) >= 3:
                log_waves = np.log10(np.array(waves))
                sed_slope = np.full(n_sources, np.nan)
                uv_slope = np.full(n_sources, np.nan)

                # UV filters: wavelength < 2.5 µm
                uv_mask = np.array(waves) < 2.5
                log_uv_waves = log_waves[uv_mask] if np.sum(uv_mask) >= 2 else None

                for k in range(n_sources):
                    fluxes_k = np.array([fa[k] for fa in slope_flux_arrays])
                    valid = np.isfinite(fluxes_k) & (fluxes_k > 0)
                    if np.sum(valid) >= 3:
                        log_f = np.log10(fluxes_k[valid])
                        coeffs = np.polyfit(log_waves[valid], log_f, 1)
                        sed_slope[k] = coeffs[0]

                    # UV slope (beta)
                    if log_uv_waves is not None:
                        uv_valid = valid & uv_mask
                        if np.sum(uv_valid) >= 2:
                            log_f_uv = np.log10(fluxes_k[uv_valid])
                            uv_coeffs = np.polyfit(log_waves[uv_valid], log_f_uv, 1)
                            uv_slope[k] = uv_coeffs[0]

                cat = cat.with_columns(pl.Series("sed_slope", sed_slope))
                feature_cols.append("sed_slope")
                if log_uv_waves is not None and np.any(np.isfinite(uv_slope)):
                    cat = cat.with_columns(pl.Series("uv_slope", uv_slope))
                    feature_cols.append("uv_slope")

                n_slope = int(np.sum(np.isfinite(sed_slope)))
                logger.info("SED slope: %d valid, median=%.2f", n_slope, np.nanmedian(sed_slope))
        except Exception as e:
            logger.debug("SED slope computation failed: %s", e)

        # Color deviation from population median (robust z-score)
        color_cols_for_dev = [c for c in cat.columns if c.startswith("color_")]
        for col in color_cols_for_dev:
            arr = cat[col].to_numpy().astype(np.float64)
            med = np.nanmedian(arr)
            mad = np.nanmedian(np.abs(arr - med))
            if np.isfinite(mad) and mad > 0:
                dev = (arr - med) / (1.4826 * mad)
                col_name = f"{col}_dev"
                cat = cat.with_columns(pl.Series(col_name, dev))
                feature_cols.append(col_name)

        # Non-parametric morphology features (from characterize stage)
        for col in ["gini", "m20", "concentration", "asymmetry", "smoothness"]:
            if col in cat.columns:
                feature_cols.append(col)

        # Kron-to-fixed aperture flux ratio (measures how extended a source is)
        for filt in ["F200W", "F277W", "F444W"]:
            kron_col = f"flux_kron_{filt}"
            fixed_col = f"flux_{filt}"
            if kron_col in cat.columns and fixed_col in cat.columns:
                kron_f = cat[kron_col].to_numpy().astype(np.float64)
                fixed_f = cat[fixed_col].to_numpy().astype(np.float64)
                ratio = np.full(n_sources, np.nan)
                valid = (
                    (kron_f > 0) & (fixed_f > 0)
                    & np.isfinite(kron_f) & np.isfinite(fixed_f)
                )
                ratio[valid] = np.log10(kron_f[valid] / fixed_f[valid])
                col_name = f"kron_ratio_{filt}"
                cat = cat.with_columns(pl.Series(col_name, ratio))
                feature_cols.append(col_name)

        # Multi-wavelength features (Chandra, WISE, XMM, VLASS, eROSITA, Spitzer)
        # are too sparse for the Isolation Forest — typically <35% coverage, so the
        # 70% column-coverage filter below always drops them.  Instead we use a
        # post-IF "multi-wavelength anomaly boost" (see Step 5b) and dedicated
        # AGN confidence scoring (see _compute_agn_confidence).

        logger.info("Total feature columns (%d): %s", len(feature_cols), feature_cols)

        # Extract feature matrix
        feature_arrays = []
        for col in feature_cols:
            arr = cat[col].to_numpy().astype(np.float64)
            feature_arrays.append(arr)

        X = np.column_stack(feature_arrays)
        logger.info("Raw feature matrix: %d x %d", X.shape[0], X.shape[1])

        # ----- Step 3: Handle missing values -----
        self.progress(0.15, "Handling missing values...")
        self.check_cancel()

        # First pass: identify usable sources (enough bands)
        n_valid_per_source = np.sum(np.isfinite(X), axis=1)
        min_features = max(3, len(feature_cols) // 3)
        usable = (n_valid_per_source >= min_features) & has_enough_bands
        n_usable = int(np.sum(usable))

        # Drop feature columns where <70% of usable sources have valid data.
        # This prevents coverage-pattern differences from driving anomaly scores.
        # E.g. if F200W only covers 50% of sources, colors involving F200W
        # would be NaN for half the population, and median imputation would
        # make the other half look systematically different.
        min_col_coverage = 0.70
        keep_cols = []
        kept_names = []
        for j, col in enumerate(feature_cols):
            col_data = X[usable, j]
            frac_valid = np.sum(np.isfinite(col_data)) / max(n_usable, 1)
            if frac_valid >= min_col_coverage:
                keep_cols.append(j)
                kept_names.append(col)
            else:
                logger.info(
                    "Dropping feature '%s': only %.0f%% valid among usable sources",
                    col, 100 * frac_valid,
                )

        if len(keep_cols) < 3:
            logger.warning("Too few usable feature columns (%d)", len(keep_cols))
            self.progress(1.0, "Too few features for anomaly detection")
            cat = cat.with_columns([
                pl.Series("anomaly_score", np.full(n_sources, np.nan)),
                pl.Series("anomaly_rank", np.full(n_sources, 0, dtype=np.int64)),
            ])
            out_path = cat_dir / "final_catalog.parquet"
            write_catalog(cat, out_path)
            return {
                "anomalies_found": 0,
                "top_candidates": 0,
                "catalog_path": str(out_path),
            }

        X = X[:, keep_cols]
        feature_cols = kept_names
        logger.info("Kept %d / %d feature columns after coverage filter", len(keep_cols), len(feature_arrays))
        logger.info("Final features: %s", feature_cols)

        # Recompute usable after column filtering
        n_valid_per_source = np.sum(np.isfinite(X), axis=1)
        usable = (n_valid_per_source >= min_features) & has_enough_bands
        n_usable = int(np.sum(usable))
        n_excluded_bands = int(np.sum(~has_enough_bands & (n_valid_per_source >= min_features)))
        logger.info(
            "Usable sources: %d / %d (min %d features, min %d bands; %d excluded for low coverage)",
            n_usable, n_sources, min_features, min_bands, n_excluded_bands,
        )

        if n_usable < 50:
            logger.warning("Too few usable sources for anomaly detection")
            self.progress(1.0, "Too few sources for anomaly detection")
            cat = cat.with_columns([
                pl.Series("anomaly_score", np.full(n_sources, np.nan)),
                pl.Series("anomaly_rank", np.full(n_sources, 0, dtype=np.int64)),
            ])
            out_path = cat_dir / "final_catalog.parquet"
            write_catalog(cat, out_path)
            return {
                "anomalies_found": 0,
                "top_candidates": 0,
                "catalog_path": str(out_path),
            }

        # Impute remaining NaN with column median for usable sources
        X_usable = X[usable].copy()
        for j in range(X_usable.shape[1]):
            col_data = X_usable[:, j]
            mask = ~np.isfinite(col_data)
            if np.any(mask):
                med = np.nanmedian(col_data)
                col_data[mask] = med if np.isfinite(med) else 0.0
                X_usable[:, j] = col_data

        self.progress(0.25, f"Feature matrix: {n_usable} sources x {X_usable.shape[1]} features")

        # ----- Step 4: Scale features -----
        self.progress(0.25, "Scaling features...")
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X_usable)

        # Clip extreme outliers to prevent single features dominating
        X_scaled = np.clip(X_scaled, -10, 10)

        # ----- Step 5: Run Isolation Forest -----
        self.progress(0.30, "Running Isolation Forest...")
        self.check_cancel()

        contamination = float(self.config.get("anomaly_contamination", 0.05))
        n_estimators = int(self.config.get("anomaly_n_estimators", 200))

        clf = IsolationForest(
            n_estimators=n_estimators,
            contamination=contamination,
            random_state=42,
            n_jobs=-1,
        )

        self.progress(0.35, f"Fitting {n_estimators} trees on {n_usable} sources...")
        clf.fit(X_scaled)

        self.progress(0.70, "Computing anomaly scores...")
        self.check_cancel()

        # score_samples returns negative values; more negative = more anomalous
        raw_scores = clf.score_samples(X_scaled)

        # Convert to 0-1 scale: 1 = most anomalous, 0 = most normal
        # raw_scores are typically in [-1, 0] range
        score_min = raw_scores.min()
        score_max = raw_scores.max()
        if score_max > score_min:
            normalized = 1.0 - (raw_scores - score_min) / (score_max - score_min)
        else:
            normalized = np.zeros(len(raw_scores))

        # Map back to full catalog
        anomaly_scores = np.full(n_sources, np.nan)
        anomaly_scores[usable] = normalized

        # ----- Step 5b: Context-aware score adjustment -----
        # The raw Isolation Forest score measures photometric unusualness,
        # but many "unusual" sources are just cluster members, known objects,
        # or have contaminated photometry.  We apply multiplicative and
        # additive adjustments so that genuinely interesting unknowns float
        # to the top.
        self.progress(0.72, "Applying context-aware score adjustments...")
        has_score = np.isfinite(anomaly_scores)

        # --- 5b-i: Cluster-member suppression ---
        # Detect the dominant redshift peak from spectroscopic redshifts.
        # Sources at that redshift are cluster members with 4000A breaks
        # that mimic Lyman breaks — they are NOT genuinely anomalous.
        is_cluster_member = np.zeros(n_sources, dtype=bool)
        cluster_z = None
        if "best_spec_z" in cat.columns:
            spec_z = cat["best_spec_z"].to_numpy().astype(np.float64)
            valid_sz = spec_z[np.isfinite(spec_z) & (spec_z > 0.01) & (spec_z < 5.0)]
            if len(valid_sz) >= 20:
                # Histogram to find the dominant peak
                bins = np.arange(0.0, 5.0, 0.02)
                counts, edges = np.histogram(valid_sz, bins=bins)
                peak_bin = np.argmax(counts)
                if counts[peak_bin] >= 10:
                    cluster_z = (edges[peak_bin] + edges[peak_bin + 1]) / 2.0
                    logger.info("Detected cluster redshift z=%.3f (%d spec-z sources in peak)",
                                cluster_z, counts[peak_bin])

                    # Mark cluster members: spec-z within ±0.03 of peak
                    sz_match = np.isfinite(spec_z) & (np.abs(spec_z - cluster_z) < 0.03)
                    is_cluster_member[sz_match] = True

                    # Also mark photo-z matches if no spec-z available
                    if "best_photo_z" in cat.columns:
                        photo_z = cat["best_photo_z"].to_numpy().astype(np.float64)
                        pz_match = (
                            ~np.isfinite(spec_z)
                            & np.isfinite(photo_z)
                            & (np.abs(photo_z - cluster_z) < 0.05)
                        )
                        is_cluster_member[pz_match] = True

                    n_cluster = int(np.sum(is_cluster_member))
                    logger.info("Cluster members identified: %d (suppressing anomaly scores)", n_cluster)

                    # Suppress: multiply score by 0.3 (they stay in catalog but rank low)
                    anomaly_scores[has_score & is_cluster_member] *= 0.3

        cat = cat.with_columns(pl.Series("is_cluster_member", is_cluster_member.tolist()))

        # --- 5b-ii: Novelty boost ---
        # Sources NOT in any reference catalog are far more likely to be
        # genuinely interesting discoveries.  Boost their scores.
        if "novel" in cat.columns:
            novel_arr = cat["novel"].to_numpy().astype(bool)
            n_novel_scored = int(np.sum(novel_arr & has_score))
            if n_novel_scored > 0:
                anomaly_scores[has_score & novel_arr] = np.minimum(
                    1.0, anomaly_scores[has_score & novel_arr] * 1.5 + 0.10
                )
                logger.info("Novelty boost applied to %d sources", n_novel_scored)

        # --- 5b-iii: Known-object dampening ---
        # NED-matched galaxies with spec-z are well-studied — dampen
        # unless they have MW evidence of being interesting (AGN, etc.)
        if "ned_match" in cat.columns:
            ned = cat["ned_match"].to_numpy().astype(bool)
            known_boring = ned & ~is_cluster_member  # cluster already suppressed
            # Don't dampen if source has X-ray or radio (could be interesting AGN)
            has_interesting_mw = np.zeros(n_sources, dtype=bool)
            for col in ["chandra_match", "xmm_match", "vlass_match"]:
                if col in cat.columns:
                    has_interesting_mw |= cat[col].to_numpy().astype(bool)
            known_boring = known_boring & ~has_interesting_mw
            n_dampened = int(np.sum(known_boring & has_score))
            if n_dampened > 0:
                anomaly_scores[has_score & known_boring] *= 0.7
                logger.info("Known-object dampening applied to %d sources", n_dampened)

        # --- 5b-iv: Multi-wavelength anomaly boost ---
        # Sparse MW data is dropped by the 70% coverage filter above,
        # so we apply a small post-IF additive boost for MW-detected sources.
        mw_boost = np.zeros(n_sources, dtype=np.float64)

        for col in ["chandra_match", "xmm_match", "erosita_match"]:
            if col in cat.columns:
                matched = cat[col].to_numpy().astype(bool)
                mw_boost[matched] += 0.03

        if "wise_w1w2" in cat.columns:
            w1w2 = cat["wise_w1w2"].to_numpy().astype(np.float64)
            wise_agn = np.isfinite(w1w2) & (w1w2 > 0.8)
            mw_boost[wise_agn] += 0.04

        if "vlass_match" in cat.columns:
            radio = cat["vlass_match"].to_numpy().astype(bool)
            mw_boost[radio] += 0.05

        anomaly_scores[has_score] = np.minimum(1.0, anomaly_scores[has_score] + mw_boost[has_score])
        n_boosted = int(np.sum((mw_boost > 0) & has_score))
        if n_boosted > 0:
            logger.info("MW anomaly boost: %d sources boosted (max=%.3f)", n_boosted, np.max(mw_boost))

        # --- 5b-v: Spitzer/CatWISE contamination flag ---
        # When low-resolution Spitzer/WISE flux vastly exceeds JWST F444W,
        # the MW match is almost certainly blended with a bright neighbor.
        is_mw_contaminated = np.zeros(n_sources, dtype=bool)
        f444_col = "flux_F444W" if "flux_F444W" in cat.columns else None
        if f444_col:
            f444 = cat[f444_col].to_numpy().astype(np.float64)
            for sp_col in ["spitzer_ch1", "wise_w1"]:
                if sp_col in cat.columns:
                    sp_flux = cat[sp_col].to_numpy().astype(np.float64)
                    contaminated = (
                        np.isfinite(sp_flux) & np.isfinite(f444)
                        & (f444 > 0) & (sp_flux > 0)
                        & (sp_flux / f444 > 50.0)  # MW flux >50x JWST
                    )
                    is_mw_contaminated |= contaminated

            n_contam = int(np.sum(is_mw_contaminated))
            if n_contam > 0:
                logger.info("MW flux contamination flagged: %d sources (Spitzer/WISE >> F444W)", n_contam)
                # Dampen contaminated sources
                anomaly_scores[has_score & is_mw_contaminated] *= 0.5

        cat = cat.with_columns(pl.Series("is_mw_contaminated", is_mw_contaminated.tolist()))

        # --- 5b-vi: Proximity deduplication ---
        # In crowded fields (especially galaxy clusters with ICL), SEP often
        # fragments extended structures or ICL knots into multiple detections
        # within a small radius.  Keep only the highest-S/N source per group
        # and suppress the rest so that fragments don't pollute novel counts.
        self.progress(0.73, "Deduplicating nearby sources...")
        ras = cat["ra"].to_numpy().astype(np.float64)
        decs = cat["dec"].to_numpy().astype(np.float64)
        max_sn_arr = cat["max_sn"].to_numpy().astype(np.float64) if "max_sn" in cat.columns else np.ones(n_sources)
        is_fragment = np.zeros(n_sources, dtype=bool)
        dedup_radius_arcsec = 0.5  # reduced from 2.0" to avoid percolation chains

        # Run dedup on ALL sources (not just scored) to catch fragments
        # that could be flagged as novel
        valid_idx = np.where(np.isfinite(ras) & np.isfinite(decs))[0]
        if len(valid_idx) > 0:
            v_ra = ras[valid_idx]
            v_dec = decs[valid_idx]
            v_sn = max_sn_arr[valid_idx]
            cos_dec = np.cos(np.radians(np.mean(v_dec)))

            # Simple O(N*k) approach: for each source, find neighbors within radius
            # Use sorted RA for fast neighbor search
            sort_order = np.argsort(v_ra)
            sorted_ra = v_ra[sort_order]
            sorted_dec = v_dec[sort_order]
            sorted_sn = v_sn[sort_order]
            sorted_orig_idx = valid_idx[sort_order]

            ra_tol = dedup_radius_arcsec / 3600.0 / cos_dec

            for i in range(len(sort_order)):
                if is_fragment[sorted_orig_idx[i]]:
                    continue
                # Search forward for neighbors
                for j in range(i + 1, len(sort_order)):
                    if sorted_ra[j] - sorted_ra[i] > ra_tol:
                        break
                    ddec = abs(sorted_dec[j] - sorted_dec[i]) * 3600.0
                    if ddec > dedup_radius_arcsec:
                        continue
                    dra = abs(sorted_ra[j] - sorted_ra[i]) * cos_dec * 3600.0
                    sep = np.sqrt(dra**2 + ddec**2)
                    if sep <= dedup_radius_arcsec:
                        # Mark the lower-S/N source as a fragment
                        if sorted_sn[j] >= sorted_sn[i]:
                            is_fragment[sorted_orig_idx[i]] = True
                            break  # i is suppressed, stop checking its neighbors
                        else:
                            is_fragment[sorted_orig_idx[j]] = True

            n_fragments = int(np.sum(is_fragment))
            if n_fragments > 0:
                anomaly_scores[has_score & is_fragment] *= 0.2
                # Remove fragments from novel flag (like spike artifacts)
                if "novel" in cat.columns:
                    novel_arr = cat["novel"].to_numpy().astype(bool)
                    n_novel_frags = int(np.sum(novel_arr & is_fragment))
                    novel_arr[is_fragment] = False
                    cat = cat.with_columns(pl.Series("novel", novel_arr.tolist()))
                    logger.info("Proximity dedup: %d fragment sources suppressed (within %.1f\"), "
                                "%d removed from novel",
                                n_fragments, dedup_radius_arcsec, n_novel_frags)
                else:
                    logger.info("Proximity dedup: %d fragment sources suppressed (within %.1f\")",
                                n_fragments, dedup_radius_arcsec)

        cat = cat.with_columns(pl.Series("is_fragment", is_fragment.tolist()))

        # ----- Step 5b-vii: Single-band spike artifact detection -----
        self.progress(0.73, "Detecting single-band spike artifacts...")
        is_spike, spike_filter = self._detect_spike_artifacts(cat, n_sources)
        n_spikes = int(np.sum(is_spike))
        if n_spikes > 0:
            anomaly_scores[has_score & is_spike] *= 0.1
            # Remove spike artifacts from novel flag
            if "novel" in cat.columns:
                novel_arr = cat["novel"].to_numpy().astype(bool)
                n_novel_spikes = int(np.sum(novel_arr & is_spike))
                novel_arr[is_spike] = False
                cat = cat.with_columns(pl.Series("novel", novel_arr.tolist()))
                logger.info("Spike artifact detection: %d sources flagged (%d removed from novel)",
                            n_spikes, n_novel_spikes)
            else:
                logger.info("Spike artifact detection: %d sources flagged", n_spikes)
        cat = cat.with_columns([
            pl.Series("is_spike_artifact", is_spike.tolist()),
            pl.Series("spike_filter", spike_filter.tolist()),
        ])

        # ----- Step 5c: AGN confidence + MW classification + photo-z -----
        self.progress(0.75, "Computing AGN confidence & MW classification...")
        agn_confidence = self._compute_agn_confidence(cat, n_sources)
        cat = cat.with_columns(pl.Series("agn_confidence", agn_confidence))
        n_agn_any = int(np.sum(agn_confidence >= 0.3))
        n_agn_strong = int(np.sum(agn_confidence >= 0.6))
        logger.info("AGN confidence: %d candidates (>=0.3), %d strong (>=0.6)", n_agn_any, n_agn_strong)

        mw_class = self._compute_mw_class(cat, agn_confidence, n_sources)
        cat = cat.with_columns(pl.Series("mw_class", mw_class.tolist()))
        mw_counts = {}
        for cls in mw_class:
            if cls:
                mw_counts[cls] = mw_counts.get(cls, 0) + 1
        if mw_counts:
            logger.info("MW classification: %s", mw_counts)

        z_agree, z_source, z_catastrophic = self._cross_validate_photoz(cat, n_sources)
        cat = cat.with_columns([
            pl.Series("z_agree", z_agree.tolist()),
            pl.Series("z_source", z_source.tolist()),
            pl.Series("z_catastrophic", z_catastrophic.tolist()),
        ])

        # Rank (1 = most anomalous) — computed after boost
        anomaly_rank = np.zeros(n_sources, dtype=np.int64)
        ranked_indices = np.argsort(-anomaly_scores[usable])  # descending
        for rank, idx in enumerate(ranked_indices):
            full_idx = np.where(usable)[0][idx]
            anomaly_rank[full_idx] = rank + 1

        self.progress(0.80, "Anomaly scoring complete")

        # ----- Step 6: Analyze top candidates -----
        self.progress(0.80, "Analyzing top candidates...")
        self.check_cancel()

        top_n = int(self.config.get("top_candidates", 50))
        top_mask = (anomaly_rank > 0) & (anomaly_rank <= top_n)
        n_top = int(np.sum(top_mask))

        # Count interesting properties among top candidates
        top_stats = {"n_candidates": n_top}
        if "novel" in cat.columns:
            novel_arr = cat["novel"].to_numpy()
            top_stats["n_novel"] = int(np.sum(novel_arr[top_mask]))
        if "dropout_flag" in cat.columns:
            dropout_arr = cat["dropout_flag"].to_list()
            top_stats["n_dropouts"] = sum(
                1 for i in range(n_sources) if top_mask[i] and dropout_arr[i] != "none"
            )
        if "red_excess" in cat.columns:
            red_arr = cat["red_excess"].to_numpy()
            top_stats["n_red_excess"] = int(np.sum(red_arr[top_mask]))

        logger.info("Top %d candidates: %s", top_n, top_stats)

        # Log top 10
        top10_idx = np.where(top_mask)[0]
        top10_sorted = top10_idx[np.argsort(anomaly_rank[top10_idx])][:10]
        ids = cat["id"].to_numpy()
        ras = cat["ra"].to_numpy()
        decs = cat["dec"].to_numpy()
        for idx in top10_sorted:
            rank = anomaly_rank[idx]
            score = anomaly_scores[idx]
            logger.info(
                "  Rank %d: id=%d, score=%.4f, RA=%.5f, Dec=%.5f",
                rank, ids[idx], score, ras[idx], decs[idx],
            )

        # ----- Step 7: Confidence tiering -----
        self.progress(0.85, "Computing confidence tiers...")
        self.check_cancel()

        conf_tier, tier_label = self._compute_confidence_tier(
            cat, anomaly_scores, anomaly_rank, top_n, n_sources,
        )
        n_t1 = int(np.sum(conf_tier == 1))
        n_t2 = int(np.sum(conf_tier == 2))
        n_t3 = int(np.sum(conf_tier == 3))
        logger.info("Confidence tiers: %d gold, %d silver, %d bronze", n_t1, n_t2, n_t3)
        top_stats["tier_1_gold"] = n_t1
        top_stats["tier_2_silver"] = n_t2
        top_stats["tier_3_bronze"] = n_t3

        # ----- Step 8: Write output -----
        self.progress(0.92, "Writing final catalog...")
        self.check_cancel()

        cat = cat.with_columns([
            pl.Series("anomaly_score", anomaly_scores),
            pl.Series("anomaly_rank", anomaly_rank),
            pl.Series("confidence_tier", conf_tier),
            pl.Series("tier_label", [str(s) for s in tier_label]),
        ])

        out_path = cat_dir / "final_catalog.parquet"
        write_catalog(cat, out_path)
        logger.info(
            "Wrote final catalog: %s (%d sources, %d columns)",
            out_path.name, cat.height, cat.width,
        )

        self.progress(1.0, f"Found {n_top} anomaly candidates from {n_usable} scored sources")

        return {
            "anomalies_found": n_top,
            "sources_scored": n_usable,
            "top_candidates": top_n,
            "top_stats": top_stats,
            "catalog_path": str(out_path),
            "score_distribution": {
                "median": float(np.nanmedian(anomaly_scores[usable])),
                "mean": float(np.nanmean(anomaly_scores[usable])),
                "std": float(np.nanstd(anomaly_scores[usable])),
                "p95": float(np.nanpercentile(anomaly_scores[usable], 95)),
                "p99": float(np.nanpercentile(anomaly_scores[usable], 99)),
            },
        }

    # ------------------------------------------------------------------ #
    #  Spike artifact detection                                            #
    # ------------------------------------------------------------------ #
    def _detect_spike_artifacts(self, cat, n_sources):
        """Detect single-band flux spike artifacts from deblend contamination.

        Failure mode: SEP deblending splits a compact object into multiple
        fragments that each measure the SAME point-source flux in one filter
        while being distinct in other filters. This produces dramatic
        single-band "spikes" that mimic emission lines.

        All 3 criteria must be met per source per filter:
        1. flux_in_filter / median(flux_in_adjacent_filters) > 10
        2. flux_psf / flux > 0.95 in the spike filter (unresolved)
        3. Neighbor within 2" with >50% matching flux in that filter

        Returns: (is_spike_artifact, spike_filter) arrays
        """
        FILTER_ORDER = ["F090W", "F115W", "F150W", "F200W",
                        "F277W", "F356W", "F410M", "F444W"]

        ras = cat["ra"].to_numpy().astype(np.float64)
        decs = cat["dec"].to_numpy().astype(np.float64)
        cos_dec = np.cos(np.radians(np.median(decs)))

        # Collect available filters with both flux and flux_psf
        avail = []
        flux = {}
        flux_psf = {}
        for filt in FILTER_ORDER:
            fcol = f"flux_{filt}"
            pcol = f"flux_psf_{filt}"
            if fcol in cat.columns and pcol in cat.columns:
                avail.append(filt)
                flux[filt] = cat[fcol].to_numpy().astype(np.float64)
                flux_psf[filt] = cat[pcol].to_numpy().astype(np.float64)
            elif fcol in cat.columns:
                avail.append(filt)
                flux[filt] = cat[fcol].to_numpy().astype(np.float64)
                flux_psf[filt] = np.full(n_sources, np.nan)

        is_spike = np.zeros(n_sources, dtype=bool)
        spike_filt_arr = np.full(n_sources, "", dtype=object)

        if len(avail) < 3:
            return is_spike, spike_filt_arr

        # Phase 1: find candidates passing criteria 1+2 (fast, per-source)
        candidates = []  # list of (source_idx, filter_name)
        for k in range(n_sources):
            for fi, filt in enumerate(avail):
                fval = flux[filt][k]
                if not (np.isfinite(fval) and fval > 0):
                    continue
                fpval = flux_psf[filt][k]

                # Adjacent filter fluxes (up to 2 on each side)
                adj = []
                for di in [-2, -1, 1, 2]:
                    ai = fi + di
                    if 0 <= ai < len(avail):
                        af = flux[avail[ai]][k]
                        if np.isfinite(af) and af > 0:
                            adj.append(af)
                if len(adj) < 2:
                    continue

                # Criterion 1: flux > 10× median of adjacent
                med_adj = np.median(adj)
                if med_adj <= 0 or fval / med_adj < 10.0:
                    continue

                # Criterion 2: PSF/aperture > 0.90 (mostly unresolved)
                if np.isfinite(fpval) and fpval > 0 and fval > 0:
                    if fpval / fval < 0.90:
                        continue
                else:
                    continue

                candidates.append((k, filt))
                break  # one spike filter is enough

        if not candidates:
            logger.info("Spike artifact detection: 0 candidates to check")
            return is_spike, spike_filt_arr

        logger.info("Spike artifact detection: %d candidates pass criteria 1+2, checking neighbors...",
                     len(candidates))

        # Phase 2: neighbor check (only for candidates)
        search_r_deg = 2.0 / 3600.0
        ra_tol = search_r_deg / max(cos_dec, 0.01)

        for src_idx, filt in candidates:
            fval = flux[filt][src_idx]
            src_ra = ras[src_idx]
            src_dec = decs[src_idx]

            # Search nearby sources
            for j in range(n_sources):
                if j == src_idx:
                    continue
                if abs(ras[j] - src_ra) > ra_tol:
                    continue
                ddec = abs(decs[j] - src_dec) * 3600.0
                if ddec > 2.0:
                    continue
                dra = abs(ras[j] - src_ra) * cos_dec * 3600.0
                sep = np.sqrt(dra**2 + ddec**2)
                if sep > 2.0:
                    continue

                # Check neighbor flux in same filter
                nf = flux[filt][j]
                if np.isfinite(nf) and nf > 0:
                    match_ratio = min(fval, nf) / max(fval, nf)
                    if match_ratio > 0.5:
                        is_spike[src_idx] = True
                        spike_filt_arr[src_idx] = filt
                        break

        return is_spike, spike_filt_arr

    # ------------------------------------------------------------------ #
    #  Multi-wavelength science methods                                    #
    # ------------------------------------------------------------------ #
    def _compute_agn_confidence(self, cat, n_sources):
        """Composite AGN confidence score (0.0–1.0).

        Combines JWST SED shape, WISE W1-W2 color, X-ray detection,
        radio detection, and morphological compactness.
        """
        score = np.zeros(n_sources, dtype=np.float64)

        # V-shaped SED from characterize stage (+0.30)
        if "is_agn_candidate" in cat.columns:
            is_agn_sed = cat["is_agn_candidate"].to_numpy().astype(bool)
            score[is_agn_sed] += 0.30

        # WISE W1-W2 > 0.8 — Stern+ 2012 AGN criterion (+0.30)
        if "wise_w1w2" in cat.columns:
            w1w2 = cat["wise_w1w2"].to_numpy().astype(np.float64)
            score[np.isfinite(w1w2) & (w1w2 > 0.8)] += 0.30

        # X-ray detection (+0.25)
        has_xray = np.zeros(n_sources, dtype=bool)
        for col in ["chandra_match", "xmm_match", "erosita_match"]:
            if col in cat.columns:
                has_xray |= cat[col].to_numpy().astype(bool)
        score[has_xray] += 0.25

        # Radio detection (+0.10)
        if "vlass_match" in cat.columns:
            score[cat["vlass_match"].to_numpy().astype(bool)] += 0.10

        # Compact morphology (+0.05)
        if "stellarity" in cat.columns:
            stell = cat["stellarity"].to_numpy().astype(np.float64)
            score[np.isfinite(stell) & (stell > 0.5)] += 0.05

        return score

    def _compute_mw_class(self, cat, agn_confidence, n_sources):
        """Assign multi-wavelength sub-classifications."""
        mw_class = np.full(n_sources, "", dtype=object)

        has_xray = np.zeros(n_sources, dtype=bool)
        for col in ["chandra_match", "xmm_match", "erosita_match"]:
            if col in cat.columns:
                has_xray |= cat[col].to_numpy().astype(bool)

        wise_agn = np.zeros(n_sources, dtype=bool)
        if "wise_w1w2" in cat.columns:
            w1w2 = cat["wise_w1w2"].to_numpy().astype(np.float64)
            wise_agn = np.isfinite(w1w2) & (w1w2 > 0.8)

        has_radio = np.zeros(n_sources, dtype=bool)
        if "vlass_match" in cat.columns:
            has_radio = cat["vlass_match"].to_numpy().astype(bool)

        has_spitzer = np.zeros(n_sources, dtype=bool)
        if "spitzer_match" in cat.columns:
            has_spitzer = cat["spitzer_match"].to_numpy().astype(bool)

        compact = np.zeros(n_sources, dtype=bool)
        if "stellarity" in cat.columns:
            stell = cat["stellarity"].to_numpy().astype(np.float64)
            compact = np.isfinite(stell) & (stell > 0.5)

        very_red = np.zeros(n_sources, dtype=bool)
        if "color_F277W_F444W" in cat.columns:
            c_red = cat["color_F277W_F444W"].to_numpy().astype(np.float64)
            very_red = np.isfinite(c_red) & (c_red > 1.5)

        # Priority-based classification (highest first)
        for k in range(n_sources):
            if has_xray[k] and wise_agn[k]:
                mw_class[k] = "xray_agn"
            elif wise_agn[k] and not has_xray[k] and compact[k]:
                mw_class[k] = "obscured_agn"
            elif has_radio[k] and wise_agn[k]:
                mw_class[k] = "radio_agn"
            elif has_radio[k] and agn_confidence[k] < 0.3:
                mw_class[k] = "radio_sfg"
            elif has_xray[k] and not wise_agn[k]:
                mw_class[k] = "xray_source"
            elif has_spitzer[k] and very_red[k]:
                mw_class[k] = "ir_excess"

        return mw_class

    def _cross_validate_photoz(self, cat, n_sources):
        """Compare EAZY photo-z with external redshifts."""
        z_agree = np.full(n_sources, False, dtype=bool)
        z_source = np.full(n_sources, "", dtype=object)
        z_catastrophic = np.full(n_sources, False, dtype=bool)

        if "z_phot_eazy" not in cat.columns:
            return z_agree, z_source, z_catastrophic

        z_eazy = cat["z_phot_eazy"].to_numpy().astype(np.float64)
        z_ref = np.full(n_sources, np.nan)

        # Priority: spec-z > catalog photo-z
        if "best_spec_z" in cat.columns:
            spec_z = cat["best_spec_z"].to_numpy().astype(np.float64)
            has_spec = np.isfinite(spec_z) & (spec_z > 0)
            z_ref[has_spec] = spec_z[has_spec]
            for k in np.where(has_spec)[0]:
                z_source[k] = "spec"

        if "best_photo_z" in cat.columns:
            photo_z = cat["best_photo_z"].to_numpy().astype(np.float64)
            needs_photo = np.isfinite(photo_z) & (photo_z > 0) & ~np.isfinite(z_ref)
            z_ref[needs_photo] = photo_z[needs_photo]
            for k in np.where(needs_photo)[0]:
                z_source[k] = "photo"

        # Sources with EAZY but no external z
        has_eazy_val = np.isfinite(z_eazy) & (z_eazy > 0)
        for k in range(n_sources):
            if has_eazy_val[k] and not z_source[k]:
                z_source[k] = "eazy"

        # Compare where both exist
        both = np.isfinite(z_ref) & has_eazy_val
        if np.any(both):
            dz = np.abs(z_eazy[both] - z_ref[both]) / (1.0 + z_ref[both])
            z_agree[both] = dz < 0.15
            z_catastrophic[both] = dz > 0.3
            n_compared = int(np.sum(both))
            n_agree = int(np.sum(z_agree))
            n_cat = int(np.sum(z_catastrophic))
            logger.info(
                "Photo-z validation: %d/%d agree (%.1f%%), %d catastrophic outliers",
                n_agree, n_compared, 100 * n_agree / max(n_compared, 1), n_cat,
            )

        return z_agree, z_source, z_catastrophic

    def _compute_confidence_tier(self, cat, anomaly_scores, anomaly_rank, top_n, n_sources):
        """Assign confidence tiers incorporating multi-wavelength evidence.

        Tier 1 (GOLD):   Novel + clean + (high AGN confidence OR dropout OR
                          high anomaly score OR multi-wavelength multi-indicator)
        Tier 2 (SILVER): Novel + clean without MW enhancement,
                          OR novel with minor caveats
        Tier 3 (BRONZE): Known in catalogs OR artifact morphology
        0 = unscored (not in top N)
        """
        tier = np.zeros(n_sources, dtype=np.int32)
        tier_label = np.full(n_sources, "", dtype=object)

        top_mask = (anomaly_rank > 0) & (anomaly_rank <= top_n)

        novel = cat["novel"].to_numpy() if "novel" in cat.columns else np.ones(n_sources, dtype=bool)
        at_edge = cat["at_edge"].to_numpy() if "at_edge" in cat.columns else np.zeros(n_sources, dtype=bool)
        near_star = (cat["near_bright_star"].to_numpy() if "near_bright_star" in cat.columns
                     else np.zeros(n_sources, dtype=bool))
        is_artifact = (cat["is_artifact_morph"].to_numpy() if "is_artifact_morph" in cat.columns
                       else np.zeros(n_sources, dtype=bool))
        if "is_spike_artifact" in cat.columns:
            is_artifact = is_artifact | cat["is_spike_artifact"].to_numpy().astype(bool)
        if "is_fragment" in cat.columns:
            is_artifact = is_artifact | cat["is_fragment"].to_numpy().astype(bool)

        if "n_bands_covered" in cat.columns:
            n_bands = cat["n_bands_covered"].to_numpy().astype(np.float64)
            n_total = max(1, int(np.nanmax(n_bands)))
            good_coverage = n_bands >= max(3, n_total * 0.6)
        else:
            good_coverage = np.ones(n_sources, dtype=bool)

        if "n_bands_sn5" in cat.columns:
            n_sn5 = cat["n_bands_sn5"].to_numpy().astype(np.int64)
            good_sn = n_sn5 >= 2
        else:
            good_sn = np.ones(n_sources, dtype=bool)

        # Multi-wavelength enhancement columns
        agn_conf = (cat["agn_confidence"].to_numpy().astype(np.float64) if "agn_confidence" in cat.columns
                    else np.zeros(n_sources))
        mw_cls = cat["mw_class"].to_list() if "mw_class" in cat.columns else [""] * n_sources

        has_dropout = np.zeros(n_sources, dtype=bool)
        if "dropout_flag" in cat.columns:
            dropout_list = cat["dropout_flag"].to_list()
            has_dropout = np.array([str(d) not in ("none", "", "None") for d in dropout_list])

        # P95 anomaly score threshold
        valid_scores = anomaly_scores[np.isfinite(anomaly_scores)]
        p95 = float(np.percentile(valid_scores, 95)) if len(valid_scores) > 0 else 1.0

        clean = ~at_edge & ~near_star & good_coverage & good_sn
        mw_multi = np.array([cls in ("xray_agn", "radio_agn") for cls in mw_cls])

        for k in range(n_sources):
            if not top_mask[k]:
                continue

            if is_artifact[k]:
                tier[k] = 3
                tier_label[k] = "artifact"
            elif not novel[k]:
                tier[k] = 3
                tier_label[k] = "known"
            elif novel[k] and clean[k] and (
                agn_conf[k] >= 0.5 or has_dropout[k]
                or anomaly_scores[k] > p95 or mw_multi[k]
            ):
                tier[k] = 1
                tier_label[k] = "gold"
            elif novel[k] and clean[k]:
                tier[k] = 2
                tier_label[k] = "silver"
            elif novel[k]:
                tier[k] = 2
                tier_label[k] = "suspicious"
            else:
                tier[k] = 3
                tier_label[k] = "other"

        return tier, tier_label
