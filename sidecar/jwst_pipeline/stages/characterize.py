"""Stage 5: Derive colors, morphology, and classification features.

Computes color indices between adjacent filters, morphological parameters,
PSF-aware star/galaxy classification, AGN/LRD detection, Lyman break dropout
flags, red-excess flags, optional EAZY photo-z, and summary object_class.
Pure computation — no network calls (except optional EAZY template fetch).
"""
import contextlib
import logging
import math
import sys

import numpy as np
import polars as pl
from astropy.io import fits

from jwst_pipeline.stages.base import PipelineStage
from jwst_pipeline.utils.catalog import read_catalog, write_catalog

logger = logging.getLogger(__name__)

# JWST NIRCam pivot wavelengths in microns (for sorting filters by wavelength)
PIVOT_WAVELENGTH_UM: dict[str, float] = {
    "F070W": 0.704,
    "F090W": 0.901,
    "F115W": 1.154,
    "F140M": 1.405,
    "F150W": 1.501,
    "F162M": 1.627,
    "F182M": 1.845,
    "F200W": 1.989,
    "F210M": 2.093,
    "F250M": 2.503,
    "F277W": 2.762,
    "F300M": 2.996,
    "F335M": 3.365,
    "F356W": 3.568,
    "F360M": 3.621,
    "F410M": 4.092,
    "F430M": 4.280,
    "F444W": 4.421,
    "F460M": 4.624,
    "F480M": 4.834,
}

# NIRCam PSF FWHM in arcseconds (from STScI NIRCam documentation)
PSF_FWHM_ARCSEC: dict[str, float] = {
    "F070W": 0.030, "F090W": 0.034, "F115W": 0.040, "F140M": 0.048,
    "F150W": 0.055, "F162M": 0.059, "F182M": 0.062, "F200W": 0.066,
    "F210M": 0.070, "F250M": 0.080, "F277W": 0.091, "F300M": 0.100,
    "F335M": 0.108, "F356W": 0.115, "F360M": 0.116, "F410M": 0.133,
    "F430M": 0.139, "F444W": 0.145, "F460M": 0.149, "F480M": 0.155,
}

# Lyman break dropout definitions: (drop_filter, detect_filter, approx_redshift_range)
DROPOUT_DEFS = [
    ("F090W", "F115W", "z~6-7"),
    ("F115W", "F150W", "z~8-9"),
    ("F150W", "F200W", "z~10-13"),
]

# Flux ratio threshold for dropout classification (drop/detect < this)
DROPOUT_FLUX_RATIO = 0.3


class CharacterizeStage(PipelineStage):
    name = "characterize"

    def run(self) -> dict:
        cat_dir = self.workspace / "catalogs"

        # ----- Step 1: Load photometry catalog -----
        self.progress(0.0, "Loading photometry catalog...")
        self.check_cancel()

        phot_path = cat_dir / "photometry_catalog.parquet"
        if not phot_path.exists():
            raise FileNotFoundError(
                f"Photometry catalog not found: {phot_path}. "
                "Run the measure stage first."
            )

        cat = read_catalog(phot_path)
        n_sources = cat.height
        logger.info("Loaded photometry catalog: %d sources, %d columns", n_sources, cat.width)

        # Identify which filters have photometry columns
        available_filters = []
        for col in cat.columns:
            if col.startswith("mag_") and not col.startswith("mag_err"):
                filt = col[4:]  # strip "mag_"
                if filt in PIVOT_WAVELENGTH_UM:
                    available_filters.append(filt)

        # Sort by wavelength
        available_filters.sort(key=lambda f: PIVOT_WAVELENGTH_UM.get(f, 99.0))
        logger.info("Available filters (by wavelength): %s", available_filters)
        self.progress(0.05, f"Characterizing {n_sources} sources across {len(available_filters)} filters")

        # ----- Step 2: Color indices -----
        self.progress(0.10, "Computing color indices...")
        self.check_cancel()

        color_columns: dict[str, list[float]] = {}
        n_colors = 0

        for j in range(len(available_filters) - 1):
            f_blue = available_filters[j]
            f_red = available_filters[j + 1]
            col_name = f"color_{f_blue}_{f_red}"

            mag_blue = cat[f"mag_{f_blue}"].to_numpy().astype(np.float64)
            mag_red = cat[f"mag_{f_red}"].to_numpy().astype(np.float64)

            # color = mag_blue - mag_red (positive = red, negative = blue)
            color = mag_blue - mag_red
            color_columns[col_name] = color.tolist()
            n_colors += 1

            valid = np.isfinite(color)
            if np.any(valid):
                logger.info(
                    "%s: median=%.3f, std=%.3f, %d valid",
                    col_name, np.nanmedian(color), np.nanstd(color), np.sum(valid),
                )

        self.progress(0.30, f"Computed {n_colors} color indices")

        # ----- Step 3: Morphology -----
        self.progress(0.30, "Computing morphological parameters...")
        self.check_cancel()

        a = cat["a"].to_numpy().astype(np.float64)
        b = cat["b"].to_numpy().astype(np.float64)
        det_flux = cat["flux"].to_numpy().astype(np.float64)

        # Ensure a >= b (semi-major >= semi-minor)
        a_safe = np.maximum(a, 0.01)
        b_safe = np.maximum(b, 0.01)

        ellipticity = 1.0 - (b_safe / a_safe)
        r_half = np.sqrt(a_safe * b_safe)  # geometric mean semi-axis (pixels)
        compactness = np.where(
            r_half > 0,
            det_flux / (math.pi * r_half ** 2),
            0.0,
        )

        morph_columns = {
            "ellipticity": ellipticity.tolist(),
            "r_half_pix": r_half.tolist(),
            "compactness": compactness.tolist(),
        }

        logger.info(
            "Morphology: median ellipticity=%.3f, median r_half=%.2f pix",
            np.nanmedian(ellipticity), np.nanmedian(r_half),
        )

        # ----- Step 3b: Non-parametric morphology (Gini, M20, CAS) -----
        self.progress(0.35, "Computing non-parametric morphology...")
        self.check_cancel()

        gini_arr, m20_arr, conc_arr, asym_arr, smooth_arr = (
            self._compute_nonparametric_morphology(cat, n_sources)
        )
        morph_columns["gini"] = gini_arr.tolist()
        morph_columns["m20"] = m20_arr.tolist()
        morph_columns["concentration"] = conc_arr.tolist()
        morph_columns["asymmetry"] = asym_arr.tolist()
        morph_columns["smoothness"] = smooth_arr.tolist()

        n_gini_valid = int(np.sum(np.isfinite(gini_arr)))
        logger.info(
            "Non-parametric morphology: %d/%d sources with valid Gini, "
            "median Gini=%.3f, median C=%.2f",
            n_gini_valid, n_sources,
            np.nanmedian(gini_arr), np.nanmedian(conc_arr),
        )
        self.progress(0.45, "Morphology computed")

        # ----- Step 4: PSF-aware star/galaxy classification -----
        self.progress(0.45, "Classifying stars vs galaxies (PSF-aware)...")
        self.check_cancel()

        # Determine pixel scale from detection image WCS
        proc_dir = self.workspace / "processed"
        det_path = proc_dir / "detection.fits"
        pixel_scale_arcsec = 0.031  # default NIRCam SW pixel scale
        if det_path.exists():
            with fits.open(det_path) as hdul:
                hdr = hdul[0].header
                cdelt = hdr.get("CDELT1", hdr.get("CD1_1"))
                if cdelt is not None:
                    pixel_scale_arcsec = abs(float(cdelt)) * 3600.0
            logger.info("Pixel scale from detection image: %.4f arcsec/pix", pixel_scale_arcsec)

        # Compute reference PSF half-light radius in pixels
        # Use median FWHM of available filters as representative PSF size
        psf_fwhms = [PSF_FWHM_ARCSEC[f] for f in available_filters if f in PSF_FWHM_ARCSEC]
        if psf_fwhms:
            ref_fwhm = float(np.median(psf_fwhms))
        else:
            ref_fwhm = 0.066  # F200W default
        # Half-light radius ≈ FWHM / 2 for a Gaussian PSF
        psf_r_half_pix = (ref_fwhm / 2.0) / pixel_scale_arcsec
        logger.info(
            "PSF ref FWHM=%.4f arcsec, r_half=%.2f pix (pixel_scale=%.4f arcsec/pix)",
            ref_fwhm, psf_r_half_pix, pixel_scale_arcsec,
        )

        # PSF-aware stellarity: based on r_half / psf_r_half ratio
        size_ratio = r_half / max(psf_r_half_pix, 0.1)
        # size_score: 1.0 if r_half <= psf_r_half, drops to 0 at 2x psf_r_half
        size_score = np.clip(1.0 - (size_ratio - 1.0) / 1.0, 0.0, 1.0)
        shape_score = np.clip(1.0 - ellipticity / 0.4, 0.0, 1.0)
        stellarity = size_score * shape_score

        # Discrete classification thresholds
        is_star = (r_half < 1.3 * psf_r_half_pix) & (ellipticity < 0.3)
        is_galaxy = r_half > 1.8 * psf_r_half_pix
        # ambiguous = everything between

        n_stars = int(np.sum(is_star))
        n_galaxies = int(np.sum(is_galaxy))
        n_ambiguous = n_sources - n_stars - n_galaxies
        logger.info(
            "Classification: %d stars, %d galaxies, %d ambiguous (PSF r_half=%.2f pix)",
            n_stars, n_galaxies, n_ambiguous, psf_r_half_pix,
        )

        morph_columns["stellarity"] = stellarity.tolist()
        self.progress(0.55, f"{n_stars} stars, {n_galaxies} galaxies, {n_ambiguous} ambiguous")

        # ----- Step 5: Lyman break dropout search -----
        self.progress(0.55, "Searching for Lyman break dropouts...")
        self.check_cancel()

        dropout_flags = ["none"] * n_sources
        dropout_counts: dict[str, int] = {}

        # Build flux, fluxerr, and mag arrays for all available filters
        flux_by_filter: dict[str, np.ndarray] = {}
        fluxerr_by_filter: dict[str, np.ndarray] = {}
        mag_by_filter: dict[str, np.ndarray] = {}
        for filt in available_filters:
            fcol = f"flux_{filt}"
            ecol = f"fluxerr_{filt}"
            mcol = f"mag_{filt}"
            if fcol in cat.columns:
                flux_by_filter[filt] = cat[fcol].to_numpy().astype(np.float64)
            if ecol in cat.columns:
                fluxerr_by_filter[filt] = cat[ecol].to_numpy().astype(np.float64)
            if mcol in cat.columns:
                mag_by_filter[filt] = cat[mcol].to_numpy().astype(np.float64)

        for drop_filt, detect_filt, z_range in DROPOUT_DEFS:
            if drop_filt not in flux_by_filter or detect_filt not in flux_by_filter:
                logger.info("Skipping %s dropout: missing flux columns", drop_filt)
                continue

            flux_drop = flux_by_filter[drop_filt]
            flux_det = flux_by_filter[detect_filt]

            # Find filters redder than the detection filter (sorted by wavelength)
            det_wave = PIVOT_WAVELENGTH_UM.get(detect_filt, 0)
            redder_filters = [
                f for f in available_filters
                if PIVOT_WAVELENGTH_UM.get(f, 0) > det_wave and f in flux_by_filter
            ]

            # Identify the next redder filter for continuum color check
            next_red_filt = redder_filters[0] if redder_filters else None

            label = f"{drop_filt}_drop"
            count = 0
            n_rejected_continuum = 0

            for k in range(n_sources):
                if dropout_flags[k] != "none":
                    continue  # already classified as higher-z dropout

                f_d = flux_drop[k]
                f_r = flux_det[k]

                # Require significant detection (S/N > 5) in the detection filter
                if not np.isfinite(f_r) or f_r <= 0:
                    continue
                fe_r = fluxerr_by_filter.get(detect_filt)
                if fe_r is not None and np.isfinite(fe_r[k]) and fe_r[k] > 0:
                    if f_r / fe_r[k] < 5.0:
                        continue  # too faint to be a reliable dropout

                # Require detection (S/N > 3) in at least 1 additional redder filter
                # This weeds out sources at the edge of one filter's footprint
                n_redder_det = 0
                for rf in redder_filters:
                    f_rf = flux_by_filter[rf][k]
                    if np.isfinite(f_rf) and f_rf > 0:
                        fe_rf = fluxerr_by_filter.get(rf)
                        if fe_rf is None or not np.isfinite(fe_rf[k]) or fe_rf[k] <= 0:
                            n_redder_det += 1
                        elif f_rf / fe_rf[k] > 3.0:
                            n_redder_det += 1
                if n_redder_det < 1:
                    continue

                # Dropout criteria: drop filter is faint relative to detection
                # NaN means no coverage (not a dropout) — only finite values count
                is_dropout = False
                if not np.isfinite(f_d):
                    # No coverage in drop filter — NOT a dropout, just missing data
                    continue
                elif f_d <= 0:
                    # Covered but not detected — true dropout candidate
                    is_dropout = True
                elif f_d / f_r < DROPOUT_FLUX_RATIO:
                    is_dropout = True

                if not is_dropout:
                    continue

                # Enhanced check 1: Break magnitude (mag_drop - mag_detect > 1.0)
                # Stronger criterion than flux ratio alone (Hainline+2020)
                if (drop_filt in mag_by_filter and detect_filt in mag_by_filter
                        and f_d > 0):  # only check if drop flux is positive (not non-detection)
                    m_drop = mag_by_filter[drop_filt][k]
                    m_det = mag_by_filter[detect_filt][k]
                    if np.isfinite(m_drop) and np.isfinite(m_det):
                        if m_drop - m_det < 1.0:
                            # Break not strong enough — likely not a real dropout
                            n_rejected_continuum += 1
                            continue

                # Enhanced check 2: Continuum color — reject dusty low-z interlopers
                # Flat continuum redward of the break: mag_detect - mag_next_red < 0.4
                if (next_red_filt is not None
                        and detect_filt in mag_by_filter
                        and next_red_filt in mag_by_filter):
                    m_det = mag_by_filter[detect_filt][k]
                    m_next = mag_by_filter[next_red_filt][k]
                    if np.isfinite(m_det) and np.isfinite(m_next):
                        if m_det - m_next > 0.4:
                            # Too red continuum — likely dusty low-z galaxy
                            n_rejected_continuum += 1
                            continue

                dropout_flags[k] = label
                count += 1

            dropout_counts[label] = count
            logger.info(
                "%s dropouts (%s): %d candidates (%d rejected by continuum checks)",
                drop_filt, z_range, count, n_rejected_continuum,
            )

        self.progress(0.75, f"Found {sum(dropout_counts.values())} dropout candidates")

        # ----- Step 6: Red excess flag -----
        self.progress(0.75, "Flagging red excess sources...")
        self.check_cancel()

        red_excess = [False] * n_sources
        n_red = 0

        # Check for F277W-F444W color > 1.5 (extremely red)
        color_col = "color_F277W_F444W"
        if color_col in color_columns:
            colors = color_columns[color_col]
            for k in range(n_sources):
                c = colors[k]
                if np.isfinite(c) and c > 1.5:
                    red_excess[k] = True
                    n_red += 1
        else:
            # Try to compute from mags directly
            if "mag_F277W" in cat.columns and "mag_F444W" in cat.columns:
                m277 = cat["mag_F277W"].to_numpy()
                m444 = cat["mag_F444W"].to_numpy()
                for k in range(n_sources):
                    c = m277[k] - m444[k]
                    if np.isfinite(c) and c > 1.5:
                        red_excess[k] = True
                        n_red += 1

        logger.info("Red excess sources (F277W-F444W > 1.5): %d", n_red)
        self.progress(0.78, f"{n_red} red excess sources flagged")

        # ----- Step 6b: AGN / "Little Red Dot" detection -----
        self.progress(0.78, "Detecting AGN / Little Red Dot candidates...")
        self.check_cancel()

        # JWST discovered compact red AGN at z>4 with V-shaped SEDs (Kocevski+2024)
        # Criteria: red rest-frame optical + blue/flat UV + compact + reliable detection
        is_agn_candidate = np.zeros(n_sources, dtype=bool)
        n_agn = 0

        # Need F277W-F444W color (red rest-optical) and F150W-F200W (UV slope)
        color_277_444 = None
        if "color_F277W_F444W" in color_columns:
            color_277_444 = np.array(color_columns["color_F277W_F444W"])
        elif "mag_F277W" in cat.columns and "mag_F444W" in cat.columns:
            color_277_444 = (
                cat["mag_F277W"].to_numpy().astype(np.float64)
                - cat["mag_F444W"].to_numpy().astype(np.float64)
            )

        color_150_200 = None
        if "color_F150W_F200W" in color_columns:
            color_150_200 = np.array(color_columns["color_F150W_F200W"])
        elif "mag_F150W" in cat.columns and "mag_F200W" in cat.columns:
            color_150_200 = (
                cat["mag_F150W"].to_numpy().astype(np.float64)
                - cat["mag_F200W"].to_numpy().astype(np.float64)
            )

        # S/N in F444W for reliability
        sn_444 = None
        if "sn_F444W" in cat.columns:
            sn_444 = cat["sn_F444W"].to_numpy().astype(np.float64)
        elif "flux_F444W" in cat.columns and "fluxerr_F444W" in cat.columns:
            f444 = cat["flux_F444W"].to_numpy().astype(np.float64)
            e444 = cat["fluxerr_F444W"].to_numpy().astype(np.float64)
            sn_444 = np.where((e444 > 0) & np.isfinite(e444), f444 / e444, 0.0)

        if color_277_444 is not None and color_150_200 is not None and sn_444 is not None:
            is_agn_candidate = (
                np.isfinite(color_277_444) & np.isfinite(color_150_200)
                & (color_277_444 > 1.0)           # Red rest-frame optical
                & (color_150_200 < 0.5)            # Blue/flat UV (V-shaped SED)
                & (r_half < 1.5 * psf_r_half_pix)  # Compact / point-like
                & (sn_444 > 10)                    # Reliable detection
            )
            n_agn = int(np.sum(is_agn_candidate))
            logger.info("AGN / Little Red Dot candidates: %d", n_agn)
        else:
            missing = []
            if color_277_444 is None:
                missing.append("F277W-F444W color")
            if color_150_200 is None:
                missing.append("F150W-F200W color")
            if sn_444 is None:
                missing.append("F444W S/N")
            logger.info("Skipping AGN detection: missing %s", ", ".join(missing))

        self.progress(0.82, f"{n_agn} AGN/LRD candidates")

        # ----- Step 7: Optional EAZY photo-z estimation -----
        self.progress(0.82, "Checking for EAZY photo-z...")
        self.check_cancel()

        z_phot_eazy = np.full(n_sources, np.nan)
        z_phot_chi2 = np.full(n_sources, np.nan)
        z_phot_type = [""] * n_sources
        has_eazy = False

        # EAZY NIRCam filter IDs (from FILTER.RES.latest)
        EAZY_FILTER_IDS = {
            "F070W": 362, "F090W": 363, "F115W": 364, "F140M": 368,
            "F150W": 365, "F162M": 369, "F182M": 370, "F200W": 366,
            "F210M": 371, "F250M": 379, "F277W": 375, "F300M": 380,
            "F335M": 381, "F356W": 376, "F360M": 382, "F410M": 383,
            "F430M": 384, "F444W": 377, "F460M": 385, "F480M": 386,
        }

        try:
            # macOS Python 3.8+ needs fork for multiprocessing in eazy
            import multiprocessing as mp
            import os
            import tempfile

            # eazy prints import diagnostics to stdout, which is the NDJSON protocol channel
            with contextlib.redirect_stdout(sys.stderr):
                import eazy  # noqa: F811
            try:
                mp.set_start_method("fork", force=False)
            except RuntimeError:
                pass  # already set

            # Check which EAZY-compatible filters we have
            eazy_filters = [f for f in available_filters if f in EAZY_FILTER_IDS]
            if not self.config.get("enable_photoz", True):
                logger.info("EAZY photo-z disabled (enable_photoz=False)")
            elif len(eazy_filters) >= 3:
                logger.info("EAZY available with %d filters: %s", len(eazy_filters), eazy_filters)

                # Ensure templates are downloaded
                try:
                    eazy.fetch_eazy_photoz()
                except Exception as e:
                    logger.warning("EAZY template fetch failed (may already exist): %s", e)

                # Build temporary catalog file and translate file
                tmpdir = tempfile.mkdtemp(prefix="eazy_")
                cat_file = os.path.join(tmpdir, "photcat.fits")
                translate_file = os.path.join(tmpdir, "zphot.translate")
                param_file = os.path.join(tmpdir, "zphot.param")
                output_dir = os.path.join(tmpdir, "OUTPUT")
                os.makedirs(output_dir, exist_ok=True)

                # Build FITS catalog with id + flux/error columns in µJy
                from astropy.table import Table

                tbl = Table()
                tbl["id"] = np.arange(1, n_sources + 1, dtype=np.int64)
                tbl["z_spec"] = np.full(n_sources, -1.0)  # no spec-z

                translate_lines = ["id  id", "z_spec  z_spec"]

                eazy_filt_ids = []
                for filt in eazy_filters:
                    fid = EAZY_FILTER_IDS[filt]
                    fcol = f"flux_{filt}"
                    ecol = f"fluxerr_{filt}"
                    if fcol not in cat.columns:
                        continue

                    # Convert Jy → µJy
                    fx = cat[fcol].to_numpy().astype(np.float64) * 1e6
                    ex = np.full(n_sources, -99.0)
                    if ecol in cat.columns:
                        ex = cat[ecol].to_numpy().astype(np.float64) * 1e6

                    # EAZY sentinel: -99 for missing
                    fx_clean = np.where(np.isfinite(fx) & (fx > 0), fx, -99.0)
                    ex_clean = np.where(np.isfinite(ex) & (ex > 0), ex, -99.0)

                    flux_col = f"f_{filt}"
                    err_col = f"e_{filt}"
                    tbl[flux_col] = fx_clean
                    tbl[err_col] = ex_clean
                    translate_lines.append(f"{flux_col}  F{fid}")
                    translate_lines.append(f"{err_col}  E{fid}")
                    eazy_filt_ids.append(fid)

                if len(eazy_filt_ids) >= 3:
                    tbl.write(cat_file, format="fits", overwrite=True)
                    logger.info("EAZY: wrote catalog with %d sources, %d filters to %s",
                                n_sources, len(eazy_filt_ids), cat_file)

                    with open(translate_file, "w") as f:
                        f.write("\n".join(translate_lines) + "\n")

                    # Write param file
                    eazy_data = os.path.dirname(eazy.__file__) + "/data/eazy-photoz"
                    with open(param_file, "w") as f:
                        f.write(f"CATALOG_FILE        {cat_file}\n")
                        f.write("MAIN_OUTPUT_FILE    photz\n")
                        f.write(f"OUTPUT_DIRECTORY    {output_dir}\n")
                        f.write(f"FILTERS_RES         {eazy_data}/filters/FILTER.RES.latest\n")
                        f.write(f"TEMPLATES_FILE      {eazy_data}/templates/fsps_full/tweak_fsps_QSF_12_v3.param\n")
                        f.write(f"TEMP_ERR_FILE       {eazy_data}/templates/TEMPLATE_ERROR.eazy_v1.0\n")
                        f.write("TEMPLATE_COMBOS     a\n")  # non-negative linear combinations
                        f.write("Z_MIN               0.01\n")
                        f.write("Z_MAX               13.0\n")
                        f.write("Z_STEP              0.01\n")
                        f.write("Z_STEP_TYPE         1\n")  # log step
                        f.write("SYS_ERR             0.05\n")  # 5% systematic floor
                        f.write("APPLY_IGM           y\n")
                        f.write("FIX_ZSPEC           n\n")
                        f.write("PRIOR_ABZP          25.0\n")
                        f.write(f"PRIOR_FILE          {eazy_data}/templates/prior_K_TAO.dat\n")
                        f.write(f"PRIOR_FILTER        {EAZY_FILTER_IDS.get('F200W', 366)}\n")
                        f.write("CATALOG_FORMAT      FITS\n")

                    self.progress(0.84, f"Running EAZY on {n_sources} sources...")

                    try:
                        ez = eazy.photoz.PhotoZ(
                            param_file=param_file,
                            translate_file=translate_file,
                            zeropoint_file=None,
                            load_prior=True,
                            n_proc=0,  # single-threaded (safe on macOS)
                        )
                        self.progress(0.85, "EAZY fitting...")
                        ez.fit_catalog(n_proc=0)

                        z_phot_eazy = ez.zbest.copy()
                        chi2_arr = ez.chi2_fit  # shape (NOBJ, NZ)
                        z_phot_chi2 = np.min(chi2_arr, axis=1)
                        z_grid = ez.zgrid.copy()

                        # --- Extract P(z) diagnostics: CI, ODDS, peaks ---
                        from scipy.signal import find_peaks as _find_peaks
                        z_lo68 = np.full(n_sources, np.nan)
                        z_hi68 = np.full(n_sources, np.nan)
                        z_odds = np.full(n_sources, np.nan)
                        z_npeaks = np.zeros(n_sources, dtype=int)

                        for _i in range(n_sources):
                            c2 = chi2_arr[_i]
                            if not np.any(np.isfinite(c2)):
                                continue
                            pz = np.exp(-0.5 * (c2 - np.nanmin(c2)))
                            pz_sum = np.nansum(pz)
                            if pz_sum <= 0:
                                continue
                            pz /= pz_sum

                            # 68% CI
                            cumsum = np.nancumsum(pz)
                            lo_idx = np.searchsorted(cumsum, 0.16)
                            hi_idx = np.searchsorted(cumsum, 0.84)
                            z_lo68[_i] = z_grid[min(lo_idx, len(z_grid) - 1)]
                            z_hi68[_i] = z_grid[min(hi_idx, len(z_grid) - 1)]

                            # ODDS: integrated P(z) within ±0.06*(1+z_best)
                            zb = z_phot_eazy[_i]
                            if np.isfinite(zb) and zb > 0:
                                dz = 0.06 * (1 + zb)
                                mask_odds = (z_grid >= zb - dz) & (z_grid <= zb + dz)
                                z_odds[_i] = float(np.nansum(pz[mask_odds]))

                            # Number of peaks
                            peaks, _ = _find_peaks(pz, height=0.01, distance=10)
                            z_npeaks[_i] = max(len(peaks), 1)

                        has_eazy = True
                        n_good_z = int(np.sum(np.isfinite(z_phot_eazy) & (z_phot_eazy > 0)))
                        logger.info("EAZY photo-z: %d/%d sources with valid z_phot", n_good_z, n_sources)
                    except Exception as e:
                        logger.warning("EAZY fitting failed: %s", e)
                    finally:
                        # Clean up temp files
                        import shutil
                        shutil.rmtree(tmpdir, ignore_errors=True)
            else:
                logger.info("EAZY: only %d compatible filters (need >= 3), skipping", len(eazy_filters))
        except ImportError:
            logger.info("EAZY not installed — skipping photometric redshifts")

        # ----- Step 7b: Photo-z quality validation -----
        z_phot_quality = ["none"] * n_sources  # default: no photo-z attempted

        if has_eazy:
            self.progress(0.87, "Validating photo-z quality...")

            # Pivot wavelengths in microns for Lyman break check
            filter_waves_um = {f: PIVOT_WAVELENGTH_UM[f] for f in available_filters
                               if f in PIVOT_WAVELENGTH_UM}

            # Pre-extract S/N arrays (may already exist from dropout section)
            sn_by_filter: dict[str, np.ndarray] = {}
            for filt in available_filters:
                sncol = f"sn_{filt}"
                if sncol in cat.columns:
                    sn_by_filter[filt] = cat[sncol].to_numpy().astype(np.float64)

            # Get n_bands_sn5 from photometry catalog
            sn5_arr = None
            if "n_bands_sn5" in cat.columns:
                sn5_arr = cat["n_bands_sn5"].to_numpy().astype(int)
            else:
                # Compute from S/N arrays
                sn5_arr = np.zeros(n_sources, dtype=int)
                for filt in available_filters:
                    if filt in sn_by_filter:
                        sn5_arr += (sn_by_filter[filt] >= 5).astype(int)

            n_good = 0
            n_uncertain = 0
            n_unreliable = 0
            n_lyman_fail = 0

            # Compute number of bands with data per source for reduced chi2
            n_bands_with_data = np.zeros(n_sources, dtype=int)
            for filt in available_filters:
                if filt in sn_by_filter:
                    n_bands_with_data += np.isfinite(sn_by_filter[filt]).astype(int)

            # z_grid max for grid-edge detection
            z_grid_max = z_grid[-1] if len(z_grid) > 0 else 13.0

            n_grid_edge = 0
            n_chi2_low = 0
            n_ci_wide = 0

            for k in range(n_sources):
                zk = z_phot_eazy[k]

                # No valid photo-z
                if not np.isfinite(zk) or zk <= 0:
                    z_phot_quality[k] = "unreliable"
                    n_unreliable += 1
                    continue

                # Chi2 = 0 means EAZY had no data to fit
                if z_phot_chi2[k] == 0:
                    z_phot_quality[k] = "unreliable"
                    n_unreliable += 1
                    continue

                # Grid-edge detection: z at 99% of grid max = hit ceiling
                if zk > 0.99 * z_grid_max:
                    z_phot_quality[k] = "unreliable"
                    n_unreliable += 1
                    n_grid_edge += 1
                    continue

                # Chi2 too low = underconstrained (noise-dominated, too few bands)
                n_bands_k = max(n_bands_with_data[k], 1)
                reduced_chi2 = z_phot_chi2[k] / max(n_bands_k - 1, 1)
                if reduced_chi2 < 0.1 and n_bands_k <= 3:
                    z_phot_quality[k] = "unreliable"
                    n_unreliable += 1
                    n_chi2_low += 1
                    continue

                # Check Lyman break consistency: no detection below Lyman-alpha
                lyman_obs_um = 0.1216 * (1 + zk)  # Lyman-alpha in observed frame
                below_break_detected = False
                for filt, wave in filter_waves_um.items():
                    if wave < lyman_obs_um and filt in sn_by_filter:
                        if np.isfinite(sn_by_filter[filt][k]) and sn_by_filter[filt][k] > 3:
                            below_break_detected = True
                            break

                if below_break_detected and zk > 6:
                    # Detected below Lyman break at high-z — inconsistent
                    z_phot_quality[k] = "unreliable"
                    n_unreliable += 1
                    n_lyman_fail += 1
                    continue

                # CI width check: very wide CI = poorly constrained
                ci_width = z_hi68[k] - z_lo68[k]
                if np.isfinite(ci_width) and ci_width > 3.0:
                    z_phot_quality[k] = "uncertain"
                    n_uncertain += 1
                    n_ci_wide += 1
                    continue

                # Low ODDS = multimodal or poorly constrained
                odds_k = z_odds[k]
                if np.isfinite(odds_k) and odds_k < 0.5:
                    z_phot_quality[k] = "uncertain"
                    n_uncertain += 1
                    continue

                if sn5_arr[k] >= 3 and reduced_chi2 < 30:
                    z_phot_quality[k] = "good"
                    n_good += 1
                else:
                    z_phot_quality[k] = "uncertain"
                    n_uncertain += 1

            logger.info(
                "Photo-z quality: %d good, %d uncertain, %d unreliable "
                "(%d Lyman-fail, %d grid-edge, %d chi2-low, %d CI-wide)",
                n_good, n_uncertain, n_unreliable,
                n_lyman_fail, n_grid_edge, n_chi2_low, n_ci_wide,
            )

        self.progress(0.88, "Photo-z done" if has_eazy else "No EAZY")

        # ----- Step 8: Summary object_class column -----
        self.progress(0.87, "Assigning object classes...")
        self.check_cancel()

        object_class = ["galaxy"] * n_sources  # default
        class_counts: dict[str, int] = {}

        for k in range(n_sources):
            if is_star[k]:
                object_class[k] = "star"
            elif is_agn_candidate[k]:
                object_class[k] = "agn_candidate"
            elif dropout_flags[k] != "none":
                object_class[k] = "high_z_candidate"
            elif red_excess[k]:
                object_class[k] = "red_galaxy"
            elif not is_star[k] and not is_galaxy[k]:
                object_class[k] = "ambiguous"
            # else: stays "galaxy"

        for cls in object_class:
            class_counts[cls] = class_counts.get(cls, 0) + 1

        logger.info("Object classes: %s", class_counts)
        self.progress(0.90, "Classification complete")

        # ----- Step 9: Build output catalog -----
        self.progress(0.90, "Building characterized catalog...")
        self.check_cancel()

        # Start with all existing columns
        out_dict: dict[str, list] = {}
        for col in cat.columns:
            out_dict[col] = cat[col].to_list()

        # Add color columns
        for col_name, values in color_columns.items():
            out_dict[col_name] = values

        # Add morphology columns
        for col_name, values in morph_columns.items():
            out_dict[col_name] = values

        # Add classification columns
        out_dict["dropout_flag"] = dropout_flags
        out_dict["red_excess"] = red_excess
        out_dict["is_agn_candidate"] = is_agn_candidate.tolist()
        out_dict["object_class"] = object_class

        # Add EAZY photo-z columns if available
        if has_eazy:
            out_dict["z_phot_eazy"] = z_phot_eazy.tolist()
            out_dict["z_phot_chi2"] = z_phot_chi2.tolist()
            out_dict["z_phot_type"] = z_phot_type
            out_dict["z_phot_quality"] = z_phot_quality
            out_dict["z_lo68"] = z_lo68.tolist()
            out_dict["z_hi68"] = z_hi68.tolist()
            out_dict["z_odds"] = z_odds.tolist()
            out_dict["z_npeaks"] = z_npeaks.tolist()

        char_cat = pl.DataFrame(out_dict)

        # Write
        self.progress(0.95, "Writing characterized catalog...")
        char_path = cat_dir / "characterized_catalog.parquet"
        write_catalog(char_cat, char_path)
        logger.info(
            "Wrote characterized catalog: %s (%d sources, %d columns)",
            char_path.name, char_cat.height, char_cat.width,
        )

        self.progress(1.0, f"Characterized {n_sources} sources")

        return {
            "sources_characterized": n_sources,
            "n_colors": n_colors,
            "n_dropouts": dropout_counts,
            "n_red_excess": n_red,
            "n_star_like": n_stars,
            "n_galaxy_like": n_galaxies,
            "n_agn_candidates": n_agn,
            "classification": class_counts,
            "has_eazy_photoz": has_eazy,
            "catalog_path": str(char_path),
        }

    def _compute_nonparametric_morphology(
        self, cat, n_sources: int,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Compute Gini, M20, Concentration, Asymmetry, Smoothness per source.

        Uses detection image + segmentation map.  Returns 5 arrays of length
        n_sources (NaN where computation was not possible).
        """
        from scipy.ndimage import uniform_filter

        proc_dir = self.workspace / "processed"
        seg_dir = self.workspace / "segmentation"

        gini = np.full(n_sources, np.nan)
        m20 = np.full(n_sources, np.nan)
        concentration = np.full(n_sources, np.nan)
        asymmetry = np.full(n_sources, np.nan)
        smoothness = np.full(n_sources, np.nan)

        det_path = proc_dir / "detection.fits"
        seg_path = seg_dir / "segmentation_map.fits"
        if not det_path.exists() or not seg_path.exists():
            logger.info("Skipping non-parametric morphology: missing detection/segmentation files")
            return gini, m20, concentration, asymmetry, smoothness

        with fits.open(det_path) as hdul:
            det_data = hdul[0].data.astype(np.float64)
        with fits.open(seg_path) as hdul:
            seg_data = hdul[0].data

        src_x = cat["x"].to_numpy().astype(np.float64)
        src_y = cat["y"].to_numpy().astype(np.float64)

        half = 30  # cutout half-size in pixels
        ny, nx = det_data.shape

        # Pre-compute distance grid for concentration (reuse for all sources)
        dist_grid = np.sqrt(
            (np.arange(2 * half + 1)[:, None] - half) ** 2
            + (np.arange(2 * half + 1)[None, :] - half) ** 2
        )
        radii = np.arange(1, half + 1, dtype=float)

        for k in range(n_sources):
            cx = int(round(src_x[k]))
            cy = int(round(src_y[k]))
            if not (half < cx < nx - half and half < cy < ny - half):
                continue

            cutout = det_data[cy - half:cy + half + 1, cx - half:cx + half + 1].copy()
            seg_cut = seg_data[cy - half:cy + half + 1, cx - half:cx + half + 1]

            # Source mask from segmentation — use segmap ID at source position
            # (photometry catalog may re-number IDs, so don't rely on cat ID)
            seg_id = seg_data[cy, cx]
            if seg_id == 0:
                continue
            mask = seg_cut == seg_id
            n_pix = int(np.sum(mask))
            if n_pix < 10:
                continue

            pixels = cutout[mask]
            valid_pix = pixels[np.isfinite(pixels)]
            if len(valid_pix) < 10:
                continue

            # Replace NaN in cutout with 0 for computation
            nan_mask = ~np.isfinite(cutout)
            cutout[nan_mask] = 0.0

            total_abs = np.sum(np.abs(cutout[mask]))
            if total_abs == 0:
                continue

            # --- Gini coefficient ---
            sorted_px = np.sort(np.abs(valid_pix))
            n_v = len(sorted_px)
            s_total = np.sum(sorted_px)
            if s_total > 0:
                gini[k] = (
                    2.0 * np.sum(np.arange(1, n_v + 1) * sorted_px) / (n_v * s_total)
                    - (n_v + 1.0) / n_v
                )

            # --- M20: second-order moment of brightest 20% of flux ---
            try:
                yy, xx = np.where(mask)
                weights = np.abs(cutout[mask])
                w_sum = np.sum(weights)
                if w_sum > 0:
                    xc = np.sum(xx * weights) / w_sum
                    yc = np.sum(yy * weights) / w_sum
                    m_total = np.sum(weights * ((xx - xc) ** 2 + (yy - yc) ** 2))

                    sorted_desc = np.sort(cutout[mask])[::-1]
                    cum_flux = np.cumsum(sorted_desc)
                    f_total = cum_flux[-1]
                    if f_total > 0 and m_total > 0:
                        i20 = np.searchsorted(cum_flux, 0.2 * f_total)
                        threshold = sorted_desc[min(i20, len(sorted_desc) - 1)]
                        bright_mask = mask & (cutout >= threshold)
                        yy20, xx20 = np.where(bright_mask)
                        if len(xx20) > 0:
                            m_20 = np.sum(
                                cutout[bright_mask]
                                * ((xx20 - xc) ** 2 + (yy20 - yc) ** 2)
                            )
                            if m_20 > 0:
                                m20[k] = np.log10(m_20 / m_total)
            except Exception:
                pass

            # --- Concentration: C = 5 * log10(r80 / r20) ---
            growth = np.array([np.sum(cutout[dist_grid <= r]) for r in radii])
            if growth[-1] > 0:
                growth_norm = growth / growth[-1]
                r20_idx = np.searchsorted(growth_norm, 0.2)
                r80_idx = np.searchsorted(growth_norm, 0.8)
                r20_val = max(radii[min(r20_idx, len(radii) - 1)], 0.5)
                r80_val = radii[min(r80_idx, len(radii) - 1)]
                if r20_val > 0 and r80_val > r20_val:
                    concentration[k] = 5.0 * np.log10(r80_val / r20_val)

            # --- Asymmetry: A = sum|I - I_180| / (2 * sum|I|) ---
            rotated = np.rot90(cutout, 2)
            diff = np.abs(cutout - rotated)
            asymmetry[k] = np.sum(diff[mask]) / (2.0 * total_abs)

            # --- Smoothness: S = sum|I - I_smooth| / sum|I| ---
            smoothed = uniform_filter(cutout, size=3)
            residual = np.abs(cutout - smoothed)
            smoothness[k] = np.sum(residual[mask]) / total_abs

        return gini, m20, concentration, asymmetry, smoothness
