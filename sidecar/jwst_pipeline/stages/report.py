"""Stage 8: Generate final report with cutouts, SED plots, and candidate JSON.

Produces visual products for the top anomaly candidates: multi-band cutout
thumbnails, SED flux plots, and a summary JSON file.
"""
import json
import logging
import math
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from jwst_pipeline.stages.base import PipelineStage
from jwst_pipeline.utils.catalog import read_catalog


def _sanitize_for_json(obj):
    """Recursively convert NaN/Inf/numpy types to JSON-safe values."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return [_sanitize_for_json(x) for x in obj.tolist()]
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    return obj


logger = logging.getLogger(__name__)

# JWST NIRCam pivot wavelengths in microns (all broad + medium bands)
PIVOT_WAVELENGTH_UM: dict[str, float] = {
    "F070W": 0.704, "F090W": 0.901, "F115W": 1.154, "F140M": 1.405,
    "F150W": 1.501, "F162M": 1.627, "F182M": 1.845, "F200W": 1.989,
    "F210M": 2.093, "F250M": 2.503, "F277W": 2.762, "F300M": 2.996,
    "F335M": 3.365, "F356W": 3.568, "F360M": 3.621, "F410M": 4.092,
    "F430M": 4.280, "F444W": 4.421, "F460M": 4.624, "F480M": 4.834,
}

# Multi-wavelength pivot wavelengths in microns (for extended SED plots)
MW_WAVELENGTH_UM: dict[str, float] = {
    "spitzer_ch1": 3.550,   # IRAC channel 1
    "spitzer_ch2": 4.493,   # IRAC channel 2
    "wise_w1": 3.368,       # CatWISE W1
    "wise_w2": 4.618,       # CatWISE W2
}

# WISE zero-point flux density in Jy (Vega system, Wright+ 2010)
_WISE_F0_JY = {"w1": 309.540, "w2": 171.787}


def _wise_vega_to_ujy(mag_vega: float, band: str) -> float:
    """Convert WISE Vega magnitude to microJy."""
    f0 = _WISE_F0_JY[band]
    return f0 * 10 ** (-0.4 * mag_vega) * 1e6


class ReportStage(PipelineStage):
    name = "report"

    def run(self) -> dict:
        cat_dir = self.workspace / "catalogs"
        proc_dir = self.workspace / "processed"
        report_dir = self.workspace / "report"
        cutout_dir = report_dir / "cutouts"
        sed_dir = report_dir / "seds"
        cutout_dir.mkdir(parents=True, exist_ok=True)
        sed_dir.mkdir(parents=True, exist_ok=True)

        # ----- Step 1: Load final catalog -----
        self.progress(0.0, "Loading final catalog...")
        self.check_cancel()

        final_path = cat_dir / "final_catalog.parquet"
        if not final_path.exists():
            raise FileNotFoundError(
                f"Final catalog not found: {final_path}. "
                "Run the anomaly stage first."
            )

        cat = read_catalog(final_path)
        n_sources = cat.height
        logger.info("Loaded final catalog: %d sources", n_sources)

        # Get top candidates + all novel sources
        top_n = int(self.config.get("top_candidates", 50))
        ranks = cat["anomaly_rank"].to_numpy()
        top_mask = (ranks > 0) & (ranks <= top_n)
        top_indices_set = set(np.where(top_mask)[0])

        # Always include novel sources even if they aren't top anomalies
        novel_indices_set = set()
        total_novel_count = 0
        if "novel" in cat.columns:
            novel_arr = cat["novel"].to_numpy()
            novel_indices_set = set(np.where(novel_arr)[0])

            # Identify high-z candidates BEFORE quality gate so they're protected
            highz_indices_set = set()
            min_highz_redshift = float(self.config.get("report_highz_min_z", 6.0))
            if "z_phot_eazy" in cat.columns:
                z_arr = cat["z_phot_eazy"].to_numpy().astype(np.float64)
                zq_arr = cat["z_phot_quality"].to_list() if "z_phot_quality" in cat.columns else [""] * n_sources
                for idx in novel_indices_set:
                    if (np.isfinite(z_arr[idx]) and z_arr[idx] > min_highz_redshift
                            and zq_arr[idx] in ("good", "uncertain")):
                        highz_indices_set.add(idx)
                if highz_indices_set:
                    logger.info(
                        "Protected %d novel high-z candidates (z > %.1f) from gates/caps",
                        len(highz_indices_set), min_highz_redshift,
                    )

            # S/N quality gate: novel sources need S/N >= 5 in at least N bands
            # (high-z candidates are exempt)
            if "n_bands_sn5" in cat.columns:
                sn5_arr = cat["n_bands_sn5"].to_numpy().astype(np.int64)
                min_sn5_bands = int(self.config.get("novel_min_sn5_bands", 3))
                pre_gate = len(novel_indices_set)
                novel_indices_set = {
                    idx for idx in novel_indices_set
                    if sn5_arr[idx] >= min_sn5_bands or idx in highz_indices_set
                }
                n_gated = pre_gate - len(novel_indices_set)
                if n_gated > 0:
                    logger.info(
                        "S/N quality gate: removed %d novel sources with < %d bands at S/N >= 5",
                        n_gated, min_sn5_bands,
                    )

            # Safety cap: prevent OOM when thousands of novel sources survive
            max_novel = int(self.config.get("max_novel_in_report", 500))
            total_novel_count = len(novel_indices_set)
            if len(novel_indices_set) > max_novel:
                # Separate high-z from the rest before capping
                non_highz_set = novel_indices_set - highz_indices_set
                remaining_slots = max(0, max_novel - len(highz_indices_set))
                if "max_sn" in cat.columns:
                    max_sn_arr = cat["max_sn"].to_numpy().astype(np.float64)
                    non_highz_list = sorted(
                        non_highz_set,
                        key=lambda i: max_sn_arr[i] if np.isfinite(max_sn_arr[i]) else -1.0,
                        reverse=True,
                    )
                else:
                    non_highz_list = sorted(non_highz_set)
                novel_indices_set = highz_indices_set | set(non_highz_list[:remaining_slots])
                logger.info("Capped novel in report: %d -> %d (%d high-z protected + %d by S/N)",
                            total_novel_count, len(novel_indices_set),
                            len(highz_indices_set), min(remaining_slots, len(non_highz_list)))

            n_novel_added = len(novel_indices_set - top_indices_set)
            if n_novel_added > 0:
                logger.info("Adding %d novel sources to report (not in top %d anomalies)",
                            n_novel_added, top_n)

        # Sort: top anomalies first (by rank), then novel sources (by ID)
        top_sorted = sorted(top_indices_set, key=lambda i: ranks[i])
        novel_only = sorted(novel_indices_set - top_indices_set)
        top_indices = np.array(top_sorted + novel_only, dtype=int)
        n_top = len(top_indices)
        logger.info("Generating report for %d candidates (%d anomaly + %d novel-only)",
                     n_top, len(top_sorted), len(novel_only))

        # ----- Step 2: Discover filter images -----
        self.progress(0.05, "Scanning filter images...")

        # Prefer _reproj.fits (same WCS as detection image) over _bksub.fits
        filter_images: dict[str, Path] = {}
        use_reproj = False
        for fp in sorted(proc_dir.glob("*_reproj.fits")):
            name = fp.stem.replace("_reproj", "")
            filter_images[name.upper()] = fp
            use_reproj = True

        if not filter_images:
            for fp in sorted(proc_dir.glob("*_bksub.fits")):
                name = fp.stem.replace("_bksub", "")
                filter_images[name.upper()] = fp

        filters_sorted = sorted(filter_images.keys(), key=lambda f: PIVOT_WAVELENGTH_UM.get(f, 99.0))
        logger.info("Available filters for cutouts (%s): %s",
                     "reproj" if use_reproj else "bksub", filters_sorted)

        # Identify flux columns available (include all filters with valid wavelengths)
        flux_filters = []
        for col in cat.columns:
            if col.startswith("flux_F") and not col.startswith("fluxerr"):
                filt = col[5:]
                if filt in PIVOT_WAVELENGTH_UM:
                    flux_filters.append(filt)
        flux_filters.sort(key=lambda f: PIVOT_WAVELENGTH_UM.get(f, 99.0))

        # ----- Step 3: Generate cutouts -----
        self.progress(0.10, "Generating cutouts...")
        self.check_cancel()

        cutout_size = 51  # pixels (odd for centering)
        half = cutout_size // 2
        cutouts_generated = 0

        # Pre-load detection image for RGB composite
        det_path = proc_dir / "detection.fits"
        det_data = None
        det_wcs = None
        if det_path.exists():
            with fits.open(det_path) as hdul:
                det_data = hdul[0].data.copy()
                det_wcs = WCS(hdul[0].header)

        frac_per_source = 0.5 / max(n_top, 1)

        # Pre-extract arrays (Polars 0.19 doesn't allow numpy int64 indexing)
        all_ids = cat["id"].to_numpy()
        all_x = cat["x"].to_numpy()
        all_y = cat["y"].to_numpy()
        all_ra = cat["ra"].to_numpy()
        all_dec = cat["dec"].to_numpy()

        for rank_idx, src_idx in enumerate(top_indices):
            self.check_cancel()
            src_idx = int(src_idx)
            frac = 0.10 + rank_idx * frac_per_source
            if rank_idx % 10 == 0:
                self.progress(frac, f"Cutouts: {rank_idx+1}/{n_top}...")

            src_id = int(all_ids[src_idx])
            src_x = float(all_x[src_idx])
            src_y = float(all_y[src_idx])

            # Refine position using the detection image peak, but only
            # accept the refinement if the peak is close to the catalog
            # centroid.  For faint/novel sources the SEP centroid is more
            # reliable than the brightest pixel in a 10-px box (which
            # often snaps to a nearby brighter object).
            if det_data is not None:
                search_r = 5  # reduced from 10 to avoid snapping to neighbors
                ny_d, nx_d = det_data.shape
                rx0 = max(0, int(src_x) - search_r)
                rx1 = min(nx_d, int(src_x) + search_r + 1)
                ry0 = max(0, int(src_y) - search_r)
                ry1 = min(ny_d, int(src_y) + search_r + 1)
                patch = det_data[ry0:ry1, rx0:rx1]
                pvalid = np.isfinite(patch)
                if np.any(pvalid):
                    pk = np.unravel_index(np.nanargmax(patch), patch.shape)
                    pk_x = float(pk[1] + rx0)
                    pk_y = float(pk[0] + ry0)
                    pk_dist = np.sqrt((pk_x - src_x)**2 + (pk_y - src_y)**2)
                    # Only accept refinement if peak is within 3 pixels
                    # of the catalog centroid — prevents snapping to
                    # nearby brighter sources
                    if pk_dist <= 3.0:
                        src_x = pk_x
                        src_y = pk_y

            # Find other cataloged sources within the cutout area
            # so we can mark them with red crosshairs.
            dists = np.sqrt((all_x - src_x)**2 + (all_y - src_y)**2)
            neighbor_mask = (dists > 2.0) & (dists < half)
            neighbor_xy = list(zip(all_x[neighbor_mask], all_y[neighbor_mask], strict=True))

            # Compute reference normalization from detection image so all
            # per-filter cutouts share the same stretch.  This ensures
            # that bands where the source is faint appear appropriately
            # dark rather than having noise stretched to fill the range.
            ref_vmin, ref_vmax = None, None
            if det_data is not None:
                ref_vmin, ref_vmax = self._cutout_norm_params(
                    det_data, src_x, src_y, half,
                )

            # Detection image cutout — uses its own normalization
            if det_data is not None:
                self._save_cutout(
                    det_data, src_x, src_y, half,
                    cutout_dir / f"src{src_id:06d}_detection.png",
                    neighbor_xy=neighbor_xy,
                )
                cutouts_generated += 1

            # Per-filter cutouts — linked normalization from detection image
            for filt in filters_sorted:
                filt_path = filter_images.get(filt)
                if filt_path is None:
                    continue
                try:
                    with fits.open(filt_path) as hdul:
                        filt_data = hdul[0].data

                    if use_reproj:
                        # Reproj files share same WCS as detection image,
                        # so detection x,y map directly — no WCS transform
                        fx, fy = src_x, src_y
                    else:
                        filt_wcs = WCS(fits.getheader(str(filt_path)))
                        fx, fy = filt_wcs.world_to_pixel_values(
                            float(all_ra[src_idx]), float(all_dec[src_idx]),
                        )
                        fx, fy = float(fx), float(fy)

                    # Skip if center pixel has no data (NaN region)
                    ny, nx = filt_data.shape
                    ix, iy = int(fx), int(fy)
                    if not (0 <= ix < nx and 0 <= iy < ny):
                        continue
                    if not np.isfinite(filt_data[iy, ix]):
                        continue

                    # Compute neighbor positions in this filter's pixel frame
                    if use_reproj:
                        filt_neighbors = neighbor_xy
                    else:
                        filt_neighbors = []
                        for nx_w, ny_w in neighbor_xy if det_wcs is not None else []:
                            try:
                                # neighbor_xy is in detection coords; convert via the detection WCS
                                nra, ndec = det_wcs.pixel_to_world_values(nx_w, ny_w)
                                fnx, fny = filt_wcs.world_to_pixel_values(nra, ndec)
                                filt_neighbors.append((float(fnx), float(fny)))
                            except Exception:
                                pass

                    self._save_cutout(
                        filt_data, fx, fy, half,
                        cutout_dir / f"src{src_id:06d}_{filt}.png",
                        ref_vmin=ref_vmin, ref_vmax=ref_vmax,
                        neighbor_xy=filt_neighbors,
                    )
                    cutouts_generated += 1
                except Exception as e:
                    logger.debug("Cutout failed for src %d %s: %s", src_id, filt, e)

            # RGB composite cutout
            if len(filters_sorted) >= 3:
                try:
                    self._save_rgb_cutout(
                        filter_images, filters_sorted,
                        src_x, src_y, half,
                        cutout_dir / f"src{src_id:06d}_rgb.png",
                        use_reproj, float(all_ra[src_idx]), float(all_dec[src_idx]),
                    )
                    cutouts_generated += 1
                except Exception as e:
                    logger.debug("RGB cutout failed for src %d: %s", src_id, e)

        logger.info("Generated %d cutout images", cutouts_generated)

        # ----- Step 3b: Fetch Zcut + HLA archive cutouts -----
        enable_zcut = self.config.get("enable_zcut_cutouts", True)
        enable_hla = self.config.get("enable_hla_cutouts", True)
        archive_cutouts = 0

        if (enable_zcut or enable_hla) and n_top > 0:
            self.progress(0.52, "Fetching archive cutouts...")
            max_archive = min(n_top, int(self.config.get("top_candidates", 50)))
            archive_indices = top_indices[:max_archive]
            frac_per = 0.08 / max(len(archive_indices), 1)

            for rank_idx, src_idx in enumerate(archive_indices):
                self.check_cancel()
                src_idx = int(src_idx)
                src_id = int(all_ids[src_idx])
                src_ra = float(all_ra[src_idx])
                src_dec = float(all_dec[src_idx])
                frac = 0.52 + rank_idx * frac_per

                if rank_idx % 10 == 0:
                    self.progress(frac, f"Archive cutouts: {rank_idx+1}/{len(archive_indices)}...")

                if enable_zcut:
                    n_z = self._fetch_zcut_cutouts(
                        src_ra, src_dec, src_id, cutout_dir,
                    )
                    archive_cutouts += n_z

                if enable_hla:
                    ok = self._fetch_hla_cutout(
                        src_ra, src_dec, src_id, cutout_dir,
                    )
                    if ok:
                        archive_cutouts += 1

            logger.info("Generated %d archive cutout images (Zcut + HLA)", archive_cutouts)

        # ----- Step 4: Generate SED plots -----
        self.progress(0.60, "Generating SED plots...")
        self.check_cancel()

        sed_plots_generated = 0
        frac_per_sed = 0.25 / max(n_top, 1)

        # Pre-extract flux/error/S/N arrays for SED plots
        flux_arrays: dict[str, np.ndarray] = {}
        err_arrays: dict[str, np.ndarray] = {}
        sn_plot_arrays: dict[str, np.ndarray] = {}
        for filt in flux_filters:
            fcol = f"flux_{filt}"
            ecol = f"fluxerr_{filt}"
            sncol = f"sn_{filt}"
            if fcol in cat.columns:
                flux_arrays[filt] = cat[fcol].to_numpy().astype(np.float64)
            if ecol in cat.columns:
                err_arrays[filt] = cat[ecol].to_numpy().astype(np.float64)
            if sncol in cat.columns:
                sn_plot_arrays[filt] = cat[sncol].to_numpy().astype(np.float64)
        anomaly_scores = cat["anomaly_score"].to_numpy().astype(np.float64)

        # Pre-extract MW photometry columns for SED overlay
        col_arrays: dict[str, np.ndarray] = {}
        for mw_col in ["spitzer_ch1", "spitzer_ch2", "wise_w1", "wise_w2"]:
            if mw_col in cat.columns:
                col_arrays[mw_col] = cat[mw_col].to_numpy().astype(np.float64)

        for rank_idx, src_idx in enumerate(top_indices):
            self.check_cancel()
            src_idx = int(src_idx)
            frac = 0.60 + rank_idx * frac_per_sed
            if rank_idx % 10 == 0:
                self.progress(frac, f"SED plots: {rank_idx+1}/{n_top}...")

            src_id = int(all_ids[src_idx])

            # Collect fluxes, errors, and S/N per filter
            wavelengths = []
            fluxes = []
            flux_errors = []
            filt_names = []
            sn_vals = []

            for filt in flux_filters:
                wave = PIVOT_WAVELENGTH_UM.get(filt, 0)
                f_val = float(flux_arrays[filt][src_idx]) if filt in flux_arrays else np.nan
                e_val = float(err_arrays[filt][src_idx]) if filt in err_arrays else np.nan
                sn_val = float(sn_plot_arrays[filt][src_idx]) if filt in sn_plot_arrays else np.nan

                # Skip filters with no coverage (both flux and error are NaN)
                if not np.isfinite(f_val) and not np.isfinite(e_val):
                    continue

                wavelengths.append(wave)
                fluxes.append(f_val)
                flux_errors.append(e_val)
                filt_names.append(filt)
                sn_vals.append(sn_val if np.isfinite(sn_val) else None)

            # Collect multi-wavelength SED points (Spitzer/WISE)
            mw_wl, mw_fl, mw_nm, mw_inst = [], [], [], []
            for mw_col, mw_name, mw_w, mw_i in [
                ("spitzer_ch1", "IRAC 3.6", 3.550, "spitzer"),
                ("spitzer_ch2", "IRAC 4.5", 4.493, "spitzer"),
            ]:
                if mw_col in col_arrays:
                    val = float(col_arrays[mw_col][src_idx])
                    if np.isfinite(val) and val > 0:
                        mw_wl.append(mw_w)
                        mw_fl.append(val)
                        mw_nm.append(mw_name)
                        mw_inst.append(mw_i)
            for mw_col, mw_name, mw_w, mw_band in [
                ("wise_w1", "W1", 3.368, "w1"),
                ("wise_w2", "W2", 4.618, "w2"),
            ]:
                if mw_col in col_arrays:
                    mag = float(col_arrays[mw_col][src_idx])
                    if np.isfinite(mag):
                        mw_wl.append(mw_w)
                        mw_fl.append(_wise_vega_to_ujy(mag, mw_band))
                        mw_nm.append(mw_name)
                        mw_inst.append("wise")

            try:
                self._save_sed_plot(
                    wavelengths, fluxes, flux_errors, filt_names,
                    sn_vals,
                    src_id, float(anomaly_scores[src_idx]),
                    sed_dir / f"src{src_id:06d}_sed.png",
                    mw_wavelengths=mw_wl or None,
                    mw_fluxes=mw_fl or None,
                    mw_names=mw_nm or None,
                    mw_instruments=mw_inst or None,
                )
                sed_plots_generated += 1
            except Exception as e:
                logger.debug("SED plot failed for src %d: %s", src_id, e)

        logger.info("Generated %d SED plots", sed_plots_generated)

        # ----- Step 4b: Optional CIGALE SED fitting -----
        cigale_results: dict[int, dict] = {}
        if self.config.get("enable_cigale", False):
            self.progress(0.85, "Running CIGALE SED fitting...")
            self.check_cancel()
            cigale_results = self._run_cigale(cat, top_indices, flux_filters, report_dir)
            if cigale_results:
                logger.info("CIGALE: fitted %d candidates", len(cigale_results))

        # ----- Step 5: Build candidates JSON -----
        self.progress(0.88, "Building candidates summary...")
        self.check_cancel()

        # Pre-extract all needed columns as numpy/list for JSON building
        anomaly_ranks = cat["anomaly_rank"].to_numpy()
        col_arrays: dict[str, np.ndarray | list] = {}
        for col in cat.columns:
            try:
                col_arrays[col] = cat[col].to_numpy()
            except Exception:
                col_arrays[col] = cat[col].to_list()

        candidates = []
        for src_idx in top_indices:
            src_idx = int(src_idx)
            src_id = int(all_ids[src_idx])
            is_novel_only = src_idx in novel_indices_set and src_idx not in top_indices_set
            rank_val = int(anomaly_ranks[src_idx])
            entry = {
                "id": src_id,
                "rank": rank_val if rank_val > 0 else None,
                "anomaly_score": float(anomaly_scores[src_idx]),
                "ra": float(all_ra[src_idx]),
                "dec": float(all_dec[src_idx]),
                "x": float(all_x[src_idx]),
                "y": float(all_y[src_idx]),
                "novel_only": is_novel_only,
            }

            # Add photometry (mag, flux, fluxerr, S/N per band)
            for filt in flux_filters:
                for prefix in ("mag_", "flux_", "fluxerr_", "sn_"):
                    col = f"{prefix}{filt}"
                    if col in col_arrays:
                        val = float(col_arrays[col][src_idx])
                        entry[col] = val if np.isfinite(val) else None

            # Add characterization
            for col in ["ellipticity", "r_half_pix", "stellarity", "dropout_flag",
                         "red_excess", "is_agn_candidate", "object_class",
                         "z_phot_eazy", "z_phot_chi2", "z_phot_type",
                         "z_phot_quality"]:
                if col in col_arrays:
                    val = col_arrays[col][src_idx]
                    if isinstance(val, (float, np.floating)):
                        entry[col] = float(val) if np.isfinite(float(val)) else None
                    elif isinstance(val, (bool, np.bool_)):
                        entry[col] = bool(val)
                    else:
                        entry[col] = str(val) if val else None

            # Add crossmatch info (dynamic — picks up all *_match, *_z columns)
            xmatch_cols = (
                [c for c in cat.columns if c.endswith("_match")] +
                [c for c in cat.columns if c.endswith("_z") and c.startswith(("best_", "sdss_")) or
                 (c.endswith("_z") and c not in ("best_spec_z", "best_photo_z"))] +
                ["ned_name", "ned_type", "simbad_otype",
                 "novel", "n_catalogs_matched",
                 "best_spec_z", "best_photo_z",
                 # Multi-wavelength photometry values
                 "chandra_flux", "chandra_hardness",
                 "wise_w1", "wise_w2", "wise_w1w2",
                 "xmm_flux", "xmm_det_ml",
                 "vlass_flux",
                 "spitzer_ch1", "spitzer_ch2",
                 "erosita_flux",
                 # MW science columns from anomaly stage
                 "agn_confidence", "mw_class",
                 "z_agree", "z_source", "z_catastrophic",
                 # Lensing magnification
                 "mu_best",
                 # Pipeline quality flags
                 "is_cluster_member", "is_mw_contaminated", "is_fragment"]
            )
            for col in xmatch_cols:
                if col in col_arrays:
                    val = col_arrays[col][src_idx]
                    if isinstance(val, (bool, np.bool_)):
                        entry[col] = bool(val)
                    elif isinstance(val, (float, np.floating)):
                        entry[col] = float(val) if np.isfinite(float(val)) else None
                    elif isinstance(val, (int, np.integer)):
                        entry[col] = int(val)
                    else:
                        entry[col] = str(val) if val else None

            # Add validation info
            for col in ["n_bands_covered", "at_edge", "near_bright_star",
                         "bright_star_dist", "is_artifact_morph",
                         "is_mosaic_edge", "is_spike_artifact", "spike_filter",
                         "nan_frac",
                         "confidence_tier", "tier_label",
                         "max_sn", "median_sn", "n_bands_sn5"]:
                if col in col_arrays:
                    val = col_arrays[col][src_idx]
                    if isinstance(val, (bool, np.bool_)):
                        entry[col] = bool(val)
                    elif isinstance(val, (float, np.floating)):
                        entry[col] = float(val) if np.isfinite(float(val)) else None
                    elif isinstance(val, (int, np.integer)):
                        entry[col] = int(val)
                    else:
                        entry[col] = str(val) if val else None

            # Add file paths
            entry["cutout_detection"] = f"cutouts/src{src_id:06d}_detection.png"
            entry["cutout_rgb"] = f"cutouts/src{src_id:06d}_rgb.png"
            entry["sed_plot"] = f"seds/src{src_id:06d}_sed.png"

            # HLA cutout path
            hla_path = cutout_dir / f"src{src_id:06d}_hla.jpg"
            if hla_path.exists():
                entry["cutout_hla"] = f"cutouts/src{src_id:06d}_hla.jpg"

            # Zcut survey cutout paths (dynamic per-survey)
            zcut_paths = sorted(cutout_dir.glob(f"src{src_id:06d}_zcut_*.png"))
            if zcut_paths:
                entry["cutout_zcut"] = [
                    f"cutouts/{p.name}" for p in zcut_paths
                ]

            # CIGALE results
            if src_id in cigale_results:
                cig = cigale_results[src_id]
                for key in ("stellar_mass", "sfr", "agn_frac", "dust_av", "reduced_chi2"):
                    val = cig.get(key)
                    if val is not None and np.isfinite(val):
                        entry[f"cigale_{key}"] = float(val)

            # Intrinsic (de-lensed) magnitudes when magnification available
            if "mu_best" in col_arrays:
                mu_val = float(col_arrays["mu_best"][src_idx])
                if np.isfinite(mu_val) and mu_val > 1.0:
                    intrinsic_mag = {}
                    for mag_col in cat.columns:
                        if mag_col.startswith("mag_intrinsic_"):
                            filt = mag_col[len("mag_intrinsic_"):]
                            if mag_col in col_arrays:
                                m = float(col_arrays[mag_col][src_idx])
                                if np.isfinite(m):
                                    intrinsic_mag[filt] = round(m, 3)
                    if intrinsic_mag:
                        entry["intrinsic_mag"] = intrinsic_mag

            # Multi-wavelength photometry for interactive SED overlay
            mw_phot = []
            for mw_col, mw_name, mw_wl, mw_inst in [
                ("spitzer_ch1", "IRAC 3.6", 3.550, "spitzer"),
                ("spitzer_ch2", "IRAC 4.5", 4.493, "spitzer"),
            ]:
                if mw_col in col_arrays:
                    val = float(col_arrays[mw_col][src_idx])
                    if np.isfinite(val) and val > 0:
                        mw_phot.append({
                            "name": mw_name, "wavelength_um": mw_wl,
                            "flux_ujy": val, "instrument": mw_inst,
                        })
            for mw_col, mw_name, mw_wl, mw_band in [
                ("wise_w1", "W1", 3.368, "w1"),
                ("wise_w2", "W2", 4.618, "w2"),
            ]:
                if mw_col in col_arrays:
                    mag = float(col_arrays[mw_col][src_idx])
                    if np.isfinite(mag):
                        mw_phot.append({
                            "name": mw_name, "wavelength_um": mw_wl,
                            "flux_ujy": _wise_vega_to_ujy(mag, mw_band),
                            "instrument": "wise", "mag_vega": round(mag, 3),
                        })
            if mw_phot:
                entry["mw_photometry"] = mw_phot

            candidates.append(entry)

        # Compute tier summary
        tier_summary = {"tier_1_gold": 0, "tier_2_silver": 0, "tier_3_bronze": 0}
        for c in candidates:
            t = c.get("confidence_tier", 0)
            if t == 1:
                tier_summary["tier_1_gold"] += 1
            elif t == 2:
                tier_summary["tier_2_silver"] += 1
            elif t == 3:
                tier_summary["tier_3_bronze"] += 1

        # Compute classification breakdown (across ALL sources, not just candidates)
        classification_summary: dict[str, int] = {}
        if "object_class" in col_arrays:
            for i in range(n_sources):
                cls = str(col_arrays["object_class"][i]) if col_arrays["object_class"][i] else "galaxy"
                classification_summary[cls] = classification_summary.get(cls, 0) + 1
        # Also count for novel-only sources
        novel_classification: dict[str, int] = {}
        if "object_class" in col_arrays and "novel" in col_arrays:
            for i in range(n_sources):
                if col_arrays["novel"][i]:
                    cls = str(col_arrays["object_class"][i]) if col_arrays["object_class"][i] else "galaxy"
                    novel_classification[cls] = novel_classification.get(cls, 0) + 1

        # Write JSON
        report_path = report_dir / "candidates.json"
        report_data = {
            "n_candidates": n_top,
            "n_total_sources": n_sources,
            "n_total_novel": total_novel_count,
            "filters": flux_filters,
            "tier_summary": tier_summary,
            "candidates": candidates,
        }
        if classification_summary:
            report_data["classification"] = classification_summary
        if novel_classification:
            report_data["novel_classification"] = novel_classification

        # Multi-wavelength summary
        mw_summary: dict = {}
        if "agn_confidence" in col_arrays:
            agn_arr = np.array([float(v) if v is not None else 0.0 for v in col_arrays["agn_confidence"]])
            mw_summary["n_agn_candidates"] = int(np.sum(agn_arr >= 0.3))
            mw_summary["n_strong_agn"] = int(np.sum(agn_arr >= 0.6))
        if "mw_class" in col_arrays:
            mw_cls_counts: dict[str, int] = {}
            for v in col_arrays["mw_class"]:
                cls = str(v) if v else ""
                if cls:
                    mw_cls_counts[cls] = mw_cls_counts.get(cls, 0) + 1
            if mw_cls_counts:
                mw_summary["mw_class_counts"] = mw_cls_counts
        for survey in ["chandra", "wise", "xmm", "vlass", "spitzer", "erosita"]:
            col = f"{survey}_match"
            if col in col_arrays:
                mw_summary[f"n_{survey}"] = int(sum(1 for v in col_arrays[col] if v))
        if mw_summary:
            report_data["mw_summary"] = mw_summary

        # Completeness estimate from magnitude histogram turnover
        completeness = self._estimate_completeness(cat, flux_filters)
        if completeness:
            report_data["completeness"] = completeness

        with open(report_path, "w") as f:
            json.dump(_sanitize_for_json(report_data), f, indent=2)

        logger.info("Wrote candidates report: %s", report_path.name)

        # ----- Step 6: Build full sources summary JSON -----
        self.progress(0.90, "Building full sources summary...")
        self.check_cancel()

        summary_cols = ["id", "ra", "dec", "x", "y", "anomaly_score", "anomaly_rank"]
        # Add mag columns
        for filt in flux_filters:
            mag_col = f"mag_{filt}"
            if mag_col in cat.columns:
                summary_cols.append(mag_col)
        # Add per-band S/N columns
        for filt in flux_filters:
            sn_col = f"sn_{filt}"
            if sn_col in cat.columns:
                summary_cols.append(sn_col)
        # Add characterization columns
        for col in ["ellipticity", "r_half_pix", "stellarity", "compactness",
                     "dropout_flag", "red_excess", "is_agn_candidate", "object_class",
                     "z_phot_eazy", "z_phot_chi2", "z_phot_type", "z_phot_quality"]:
            if col in cat.columns:
                summary_cols.append(col)
        # Add crossmatch columns dynamically (all *_match, *_z, aggregates)
        for col in cat.columns:
            if col.endswith("_match") or col in (
                "ned_name", "ned_type", "simbad_otype",
                "novel", "n_catalogs_matched", "best_spec_z", "best_photo_z",
                "sdss_specz", "gaia_pmra",
            ):
                if col not in summary_cols:
                    summary_cols.append(col)
            elif col.endswith("_z") and col not in summary_cols:
                summary_cols.append(col)
        # Add validation columns (including S/N aggregates)
        for col in ["n_bands_covered", "at_edge", "near_bright_star",
                     "is_artifact_morph", "is_mosaic_edge",
                     "is_spike_artifact", "spike_filter", "nan_frac",
                     "confidence_tier", "tier_label",
                     "max_sn", "median_sn", "n_bands_sn5"]:
            if col in cat.columns and col not in summary_cols:
                summary_cols.append(col)
        # Add multi-wavelength photometry + science columns
        for col in ["chandra_flux", "chandra_hardness",
                     "wise_w1", "wise_w2", "wise_w1w2",
                     "xmm_flux", "xmm_det_ml", "vlass_flux",
                     "spitzer_ch1", "spitzer_ch2", "erosita_flux",
                     "agn_confidence", "mw_class",
                     "z_agree", "z_source", "z_catastrophic"]:
            if col in cat.columns and col not in summary_cols:
                summary_cols.append(col)
        # Add color columns
        for col in cat.columns:
            if col.startswith("color_"):
                summary_cols.append(col)

        sources_list = []
        for i in range(n_sources):
            row = {}
            for col in summary_cols:
                val = col_arrays[col][i] if col in col_arrays else None
                if val is None:
                    row[col] = None
                elif isinstance(val, (float, np.floating)):
                    fv = float(val)
                    row[col] = fv if np.isfinite(fv) else None
                elif isinstance(val, (bool, np.bool_)):
                    row[col] = bool(val)
                elif isinstance(val, (int, np.integer)):
                    row[col] = int(val)
                else:
                    row[col] = str(val) if val else None
            sources_list.append(row)

        summary_path = report_dir / "sources_summary.json"
        with open(summary_path, "w") as f:
            json.dump(_sanitize_for_json({
                "n_sources": n_sources,
                "filters": flux_filters,
                "columns": summary_cols,
                "sources": sources_list,
            }), f)

        logger.info("Wrote sources summary: %s (%d sources)", summary_path.name, n_sources)

        self.progress(
            1.0, f"Report complete: {n_top} candidates, {cutouts_generated} cutouts, {sed_plots_generated} SEDs",
        )

        return {
            "cutouts_generated": cutouts_generated,
            "sed_plots_generated": sed_plots_generated,
            "n_candidates": n_top,
            "report_path": str(report_path),
        }

    def _estimate_completeness(self, cat, flux_filters):
        """Estimate 50% completeness magnitude per filter from histogram turnover."""
        completeness = {}
        for filt in flux_filters:
            mag_col = f"mag_{filt}"
            if mag_col not in cat.columns:
                continue
            mags = cat[mag_col].to_numpy().astype(np.float64)
            valid = np.isfinite(mags) & (mags > 15) & (mags < 35)
            if np.sum(valid) < 100:
                continue

            bins = np.arange(18, 32, 0.5)
            counts, edges = np.histogram(mags[valid], bins=bins)
            centers = 0.5 * (edges[:-1] + edges[1:])

            peak_idx = int(np.argmax(counts))
            if peak_idx < 2:
                continue

            peak_count = counts[peak_idx]
            m50 = None
            for i in range(peak_idx, len(counts)):
                if counts[i] < 0.5 * peak_count:
                    m50 = float(centers[i])
                    break

            completeness[filt] = {
                "mag_50pct": m50,
                "mag_turnover": float(centers[peak_idx]),
                "n_valid": int(np.sum(valid)),
            }
        return completeness

    def _cutout_norm_params(
        self, data: np.ndarray, cx: float, cy: float, half: int,
    ) -> tuple[float | None, float | None]:
        """Compute vmin/vmax for a cutout region (used as reference for all bands)."""
        ny, nx = data.shape
        x0 = max(0, int(cx) - half)
        x1 = min(nx, int(cx) + half + 1)
        y0 = max(0, int(cy) - half)
        y1 = min(ny, int(cy) + half + 1)

        if x1 <= x0 or y1 <= y0:
            return None, None

        cutout = data[y0:y1, x0:x1].astype(np.float64)
        valid = np.isfinite(cutout)
        if not np.any(valid):
            return None, None

        vmin = float(np.nanpercentile(cutout[valid], 1))
        vmax = float(np.nanpercentile(cutout[valid], 99.5))
        if vmax <= vmin:
            vmax = vmin + 1e-10
        return vmin, vmax

    def _save_cutout(
        self, data: np.ndarray, cx: float, cy: float, half: int, out_path: Path,
        ref_vmin: float | None = None, ref_vmax: float | None = None,
        neighbor_xy: list | None = None,
    ):
        """Extract and save a cutout thumbnail as PNG.

        If *ref_vmin*/*ref_vmax* are provided (from the detection image), use
        them so that all per-filter cutouts share the same stretch.

        *neighbor_xy*: optional list of (x, y) for nearby sources to mark
        with small red ``+`` markers.
        """
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from astropy.visualization import AsinhStretch, ImageNormalize

        ny, nx = data.shape
        x0 = max(0, int(cx) - half)
        x1 = min(nx, int(cx) + half + 1)
        y0 = max(0, int(cy) - half)
        y1 = min(ny, int(cy) + half + 1)

        if x1 <= x0 or y1 <= y0:
            return

        cutout = data[y0:y1, x0:x1].astype(np.float64)

        # Handle NaN
        valid = np.isfinite(cutout)
        if not np.any(valid):
            return

        # Require at least 30% valid pixels for a useful cutout
        if np.sum(valid) < 0.3 * cutout.size:
            return

        # Normalization: use reference values (linked across bands) or
        # compute per-cutout (detection image, standalone use).
        if ref_vmin is not None and ref_vmax is not None:
            vmin, vmax = ref_vmin, ref_vmax
        else:
            vmin = np.nanpercentile(cutout[valid], 1)
            vmax = np.nanpercentile(cutout[valid], 99.5)
            if vmax <= vmin:
                vmax = vmin + 1e-10

        norm = ImageNormalize(
            cutout, vmin=vmin, vmax=vmax, stretch=AsinhStretch(a=0.1),
        )

        fig, ax = plt.subplots(1, 1, figsize=(2, 2), dpi=72)
        ax.imshow(
            cutout, origin="lower", cmap="gray_r",
            norm=norm, interpolation="nearest",
        )
        # Mark center of this source
        local_cx = cx - x0
        local_cy = cy - y0
        ax.plot(local_cx, local_cy, "+", color="cyan", markersize=8, markeredgewidth=1)
        # Mark other cataloged sources with red crosshairs
        if neighbor_xy:
            for nx_pos, ny_pos in neighbor_xy:
                lx = nx_pos - x0
                ly = ny_pos - y0
                if 0 <= lx < (x1 - x0) and 0 <= ly < (y1 - y0):
                    ax.plot(lx, ly, "+", color="red", markersize=6, markeredgewidth=0.8)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_frame_on(False)
        fig.savefig(out_path, bbox_inches="tight", pad_inches=0.02, dpi=72)
        plt.close(fig)

    def _fetch_zcut_cutouts(self, ra: float, dec: float, src_id: int, cutout_dir: Path) -> int:
        """Fetch survey cutouts via astroquery Zcut for a single source.

        Returns the number of cutout images saved.
        """
        import signal
        count = 0

        def _timeout_handler(signum, frame):
            raise TimeoutError("Zcut query timed out")

        try:
            import astropy.units as u
            from astropy.coordinates import SkyCoord
            from astroquery.mast import Zcut

            coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg)

            # Set 30s timeout for survey query
            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(30)
            try:
                surveys = Zcut.get_surveys(coordinates=coord)
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)

            if not surveys or len(surveys) == 0:
                return 0

            survey_names = [str(s) for s in surveys][:3]  # limit to 3 surveys
            for survey_name in survey_names:
                try:
                    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
                    signal.alarm(30)
                    try:
                        hdulists = Zcut.get_cutouts(
                            coordinates=coord, size=5 * u.arcsec, survey=survey_name,
                        )
                    finally:
                        signal.alarm(0)
                        signal.signal(signal.SIGALRM, old_handler)
                    if hdulists and len(hdulists) > 0:
                        data = hdulists[0][0].data
                        if data is not None and data.size > 0:
                            safe_name = survey_name.replace("/", "-").replace(" ", "_").lower()
                            out_path = cutout_dir / f"src{src_id:06d}_zcut_{safe_name}.png"
                            self._save_cutout_from_array(data, out_path)
                            count += 1
                except Exception as e:
                    logger.debug("Zcut cutout failed for src %d survey %s: %s", src_id, survey_name, e)
        except Exception as e:
            logger.debug("Zcut survey query failed for src %d: %s", src_id, e)
        return count

    def _save_cutout_from_array(self, data: np.ndarray, out_path: Path):
        """Render a 2D array to a PNG cutout image."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from astropy.visualization import AsinhStretch, ImageNormalize

        arr = data.astype(np.float64)
        valid = np.isfinite(arr)
        if not np.any(valid):
            return

        vmin = float(np.nanpercentile(arr[valid], 1))
        vmax = float(np.nanpercentile(arr[valid], 99.5))
        if vmax <= vmin:
            vmax = vmin + 1e-10

        norm = ImageNormalize(arr, vmin=vmin, vmax=vmax, stretch=AsinhStretch(a=0.1))

        fig, ax = plt.subplots(1, 1, figsize=(2, 2), dpi=72)
        ax.imshow(arr, origin="lower", cmap="gray_r", norm=norm, interpolation="nearest")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_frame_on(False)
        fig.savefig(out_path, bbox_inches="tight", pad_inches=0.02, dpi=72)
        plt.close(fig)

    def _fetch_hla_cutout(self, ra: float, dec: float, src_id: int, cutout_dir: Path) -> bool:
        """Fetch an HLA Fitscut JPEG cutout for a single source.

        Returns True if a cutout was saved.
        """
        try:
            from urllib.request import Request, urlopen

            url = (
                f"https://hla.stsci.edu/cgi-bin/fitscut.cgi?"
                f"ra={ra}&dec={dec}&size=100&format=jpeg&autoscale=99.5"
            )
            req = Request(url)
            req.add_header("User-Agent", "jwst-discovery-pipeline/0.1")

            with urlopen(req, timeout=30) as resp:
                data = resp.read()

            # Validate JPEG magic bytes
            if len(data) < 4 or data[:2] != b'\xff\xd8':
                return False

            out_path = cutout_dir / f"src{src_id:06d}_hla.jpg"
            out_path.write_bytes(data)
            return True
        except Exception as e:
            logger.debug("HLA cutout failed for src %d: %s", src_id, e)
            return False

    def _save_sed_plot(
        self,
        wavelengths: list[float],
        fluxes: list[float],
        flux_errors: list[float],
        filt_names: list[str],
        sn_values: list[float | None],
        src_id: int,
        score: float,
        out_path: Path,
        mw_wavelengths: list[float] | None = None,
        mw_fluxes: list[float] | None = None,
        mw_names: list[str] | None = None,
        mw_instruments: list[str] | None = None,
    ):
        """Save a flux vs wavelength SED plot.

        Uses S/N-based categorisation following standard astronomical practice:
        - **Solid detections** (S/N >= 5): filled circles connected by a solid line
        - **Marginal** (3 <= S/N < 5): open circles, *not* connected
        - **Non-detections** (S/N < 3 or flux NaN): downward arrows at 2σ upper limit

        Optional multi-wavelength overlay (Spitzer green squares, WISE orange diamonds).
        """
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        SN_SOLID = 5.0   # minimum S/N for a solid detection
        SN_MARGINAL = 3.0  # minimum S/N for a marginal detection

        waves = np.array(wavelengths)
        flux = np.array(fluxes)
        errs = np.array(flux_errors)
        sn = np.array([v if v is not None else np.nan for v in sn_values])

        has_flux = np.isfinite(flux) & (flux > 0)

        # Classify each filter
        solid = has_flux & (sn >= SN_SOLID)
        marginal = has_flux & (sn >= SN_MARGINAL) & (sn < SN_SOLID)
        nondet = ~has_flux & np.isfinite(errs) & (errs > 0)

        if np.sum(solid) + np.sum(marginal) < 1:
            # Nothing to plot
            return

        fig, ax = plt.subplots(1, 1, figsize=(5, 3), dpi=100)

        # --- Solid detections: filled circles connected by line ---
        if np.any(solid):
            solid_err = errs[solid] if np.any(np.isfinite(errs[solid])) else None
            ax.errorbar(
                waves[solid], flux[solid],
                yerr=solid_err,
                fmt="o-", color="#2563eb", markersize=5, linewidth=1.5,
                capsize=3, ecolor="#94a3b8", label=f"S/N \u2265 {SN_SOLID:.0f}",
                zorder=3,
            )

        # --- Marginal detections: open circles, no connecting line ---
        if np.any(marginal):
            marginal_err = errs[marginal] if np.any(np.isfinite(errs[marginal])) else None
            ax.errorbar(
                waves[marginal], flux[marginal],
                yerr=marginal_err,
                fmt="o", color="#2563eb", markersize=5,
                markerfacecolor="none", markeredgewidth=1.2,
                capsize=3, ecolor="#94a3b8",
                label=f"{SN_MARGINAL:.0f} \u2264 S/N < {SN_SOLID:.0f}",
                zorder=2,
            )

        # --- Non-detections: downward arrows at 2σ upper limit ---
        if np.any(nondet):
            ax.plot(
                waves[nondet], 2 * errs[nondet],
                "v", color="#ef4444", markersize=6,
                label="2\u03c3 upper limit", zorder=1,
            )

        # Labels (filter name + S/N)
        for i, name in enumerate(filt_names):
            if solid[i] or marginal[i]:
                sn_text = f"\nS/N={sn[i]:.1f}" if np.isfinite(sn[i]) else ""
                ax.annotate(
                    f"{name}{sn_text}", (waves[i], flux[i]),
                    textcoords="offset points", xytext=(0, 8),
                    fontsize=5.5, ha="center", color="#64748b",
                )
            elif nondet[i]:
                ax.annotate(
                    name, (waves[i], 2 * errs[i]),
                    textcoords="offset points", xytext=(0, -10),
                    fontsize=5, ha="center", color="#ef444488",
                )

        # --- Multi-wavelength overlay (Spitzer / WISE) ---
        if mw_wavelengths and mw_fluxes:
            mw_style = {
                "spitzer": {"marker": "s", "color": "#16a34a", "label": "Spitzer IRAC"},
                "wise": {"marker": "D", "color": "#ea580c", "label": "CatWISE"},
            }
            plotted_instruments: set[str] = set()
            for wl, fl, nm, inst in zip(
                mw_wavelengths, mw_fluxes, mw_names or mw_wavelengths, mw_instruments or [""] * len(mw_wavelengths),
                strict=False,
            ):
                if fl <= 0:
                    continue
                # Convert µJy to Jy to match JWST SED axis
                fl_jy = fl * 1e-6
                style = mw_style.get(inst, {"marker": "^", "color": "#a855f7", "label": inst})
                label = style["label"] if inst not in plotted_instruments else None
                ax.plot(
                    wl, fl_jy,
                    marker=style["marker"], color=style["color"],
                    markersize=7, markeredgewidth=1.2, markeredgecolor="white",
                    linestyle="none", label=label, zorder=4,
                )
                ax.annotate(
                    nm, (wl, fl_jy),
                    textcoords="offset points", xytext=(0, 8),
                    fontsize=5.5, ha="center", color=style["color"],
                )
                plotted_instruments.add(inst)

        ax.set_xlabel("Wavelength (\u03bcm)", fontsize=9)
        ax.set_ylabel("Flux (Jy)", fontsize=9)
        ax.set_title(f"Source {src_id}  (anomaly score: {score:.3f})", fontsize=10)
        ax.set_yscale("log")
        ax.tick_params(labelsize=8)
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=6.5, loc="upper right")

        fig.tight_layout()
        fig.savefig(out_path, dpi=100, bbox_inches="tight")
        plt.close(fig)

    def _save_rgb_cutout(
        self,
        filter_images: dict[str, Path],
        filters_sorted: list[str],
        cx: float,
        cy: float,
        half: int,
        out_path: Path,
        use_reproj: bool,
        src_ra: float,
        src_dec: float,
    ):
        """Create an RGB composite cutout from filter groups sorted by wavelength."""
        from astropy.visualization import make_lupton_rgb

        n = len(filters_sorted)
        if n < 3:
            return

        # Assign filters to RGB channels by wavelength thirds
        blue_filts = filters_sorted[:max(1, n // 3)]
        green_filts = filters_sorted[max(1, n // 3):max(2, 2 * n // 3)]
        red_filts = filters_sorted[max(2, 2 * n // 3):]

        def _stack_channel(filts):
            arrays = []
            for filt in filts:
                filt_path = filter_images.get(filt)
                if filt_path is None:
                    continue
                try:
                    with fits.open(filt_path) as hdul:
                        data = hdul[0].data
                        if use_reproj:
                            fx, fy = cx, cy
                        else:
                            filt_wcs = WCS(hdul[0].header)
                            fx, fy = filt_wcs.world_to_pixel_values(src_ra, src_dec)
                            fx, fy = float(fx), float(fy)
                        ny_f, nx_f = data.shape
                        x0 = max(0, int(fx) - half)
                        x1 = min(nx_f, int(fx) + half + 1)
                        y0 = max(0, int(fy) - half)
                        y1 = min(ny_f, int(fy) + half + 1)
                        if x1 > x0 and y1 > y0:
                            cut = data[y0:y1, x0:x1].astype(np.float64)
                            arrays.append(cut)
                except Exception:
                    pass
            if not arrays:
                return None
            target_shape = arrays[0].shape
            same_shape = [a for a in arrays if a.shape == target_shape]
            if not same_shape:
                return None
            return np.nanmean(same_shape, axis=0)

        r_img = _stack_channel(red_filts)
        g_img = _stack_channel(green_filts)
        b_img = _stack_channel(blue_filts)

        if r_img is None or g_img is None or b_img is None:
            return

        for arr in (r_img, g_img, b_img):
            arr[~np.isfinite(arr)] = 0.0

        rgb = make_lupton_rgb(r_img, g_img, b_img, Q=10, stretch=0.5)

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(1, 1, figsize=(2, 2), dpi=72)
        ax.imshow(rgb, origin="lower", interpolation="nearest")
        local_cx = cx - max(0, int(cx) - half)
        local_cy = cy - max(0, int(cy) - half)
        ax.plot(local_cx, local_cy, "+", color="cyan", markersize=8, markeredgewidth=1)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_frame_on(False)
        fig.savefig(out_path, bbox_inches="tight", pad_inches=0.02, dpi=72)
        plt.close(fig)

    def _run_cigale(self, cat, top_indices, flux_filters, report_dir):
        """Run CIGALE SED fitting on top candidates (optional).

        Returns dict of {src_id: {stellar_mass, sfr, agn_frac, ...}}.
        """
        try:
            import pcigale  # noqa: F401
        except ImportError:
            logger.info("pcigale not installed — skipping CIGALE fitting")
            return {}

        import csv
        import shutil
        import subprocess
        import tempfile

        n_cands = len(top_indices)
        if n_cands == 0:
            return {}

        all_ids = cat["id"].to_numpy()

        # Use EAZY photo-z if available, otherwise default to z=1
        has_z = "z_phot_eazy" in cat.columns
        z_arr = cat["z_phot_eazy"].to_numpy().astype(np.float64) if has_z else None

        tmpdir = tempfile.mkdtemp(prefix="cigale_")
        results = {}
        try:
            # Build input catalog CSV
            cat_file = Path(tmpdir) / "input.txt"
            header = ["id", "redshift"]
            for filt in flux_filters:
                header.extend([filt, f"{filt}_err"])

            rows = []
            for src_idx in top_indices:
                src_idx = int(src_idx)
                src_id = int(all_ids[src_idx])
                z_val = float(z_arr[src_idx]) if z_arr is not None and np.isfinite(z_arr[src_idx]) else 1.0
                if z_val <= 0:
                    z_val = 1.0

                row = [str(src_id), f"{z_val:.4f}"]
                for filt in flux_filters:
                    fcol = f"flux_{filt}"
                    ecol = f"fluxerr_{filt}"
                    flux_val = float(cat[fcol].to_numpy()[src_idx]) * 1e3 if fcol in cat.columns else -99.0  # mJy
                    err_val = float(cat[ecol].to_numpy()[src_idx]) * 1e3 if ecol in cat.columns else -99.0
                    if not np.isfinite(flux_val) or flux_val <= 0:
                        flux_val = -99.0
                        err_val = -99.0
                    elif not np.isfinite(err_val) or err_val <= 0:
                        err_val = abs(flux_val) * 0.1  # 10% floor
                    row.extend([f"{flux_val:.6e}", f"{err_val:.6e}"])
                rows.append(row)

            with open(cat_file, "w", newline="") as f:
                writer = csv.writer(f, delimiter=" ")
                writer.writerow(header)
                writer.writerows(rows)

            # Write pcigale.ini
            ini_path = Path(tmpdir) / "pcigale.ini"
            bands_str = " ".join(flux_filters)
            ini_content = f"""[data]
data_file = input.txt
[sed_modules]
sfhdelayed = sfh.sfhdelayed
bc03 = ssp.bc03
nebular = nebular
dustatt_modified_starburst = attenuation.dustatt_modified_starburst
dale2014 = dust_emission.dale2014
redshifting = redshifting
[analysis]
variables = stellar.m_star sfh.sfr dust.attenuation
bands = {bands_str}
save_best_sed = False
save_chi2 = False
"""
            ini_path.write_text(ini_content)

            # Run CIGALE
            proc = subprocess.run(
                ["pcigale", "run"],
                cwd=tmpdir, capture_output=True, text=True, timeout=600,
            )
            if proc.returncode != 0:
                logger.warning("CIGALE failed: %s", proc.stderr[:500])
                return {}

            # Parse results
            results_file = Path(tmpdir) / "out" / "results.txt"
            if results_file.exists():
                import csv as csv2
                with open(results_file) as f:
                    reader = csv2.DictReader(f, delimiter=" ")
                    for row in reader:
                        sid = int(row.get("id", 0))
                        results[sid] = {
                            "stellar_mass": float(row.get("bayes.stellar.m_star", "nan")),
                            "sfr": float(row.get("bayes.sfh.sfr", "nan")),
                            "reduced_chi2": float(row.get("best.reduced_chi_square", "nan")),
                        }
        except Exception as e:
            logger.warning("CIGALE execution failed: %s", e)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        return results
