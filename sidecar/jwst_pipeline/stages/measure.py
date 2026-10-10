"""Stage 4: Forced multi-band aperture photometry.

Measures flux of every detected source in every filter's background-subtracted
image at multiple aperture radii, converting MJy/sr surface brightness to
AB magnitudes.  Includes Kron adaptive apertures and optional PSF-matched
photometry for accurate colors.
"""
import gc
import logging
import math
import re
import warnings
from pathlib import Path

import numpy as np
import polars as pl
import sep
from astropy.io import fits
from astropy.wcs import WCS

from jwst_pipeline.stages.base import PipelineStage
from jwst_pipeline.utils.catalog import read_catalog, write_catalog

logger = logging.getLogger(__name__)

FILTER_PATTERN = re.compile(r"^([A-Z]\d+[A-Z]?)_bksub\.fits$", re.IGNORECASE)

# NIRCam PSF FWHM in arcseconds (from STScI NIRCam documentation)
PSF_FWHM_ARCSEC: dict[str, float] = {
    "F070W": 0.030, "F090W": 0.034, "F115W": 0.040, "F140M": 0.048,
    "F150W": 0.055, "F162M": 0.059, "F182M": 0.062, "F200W": 0.066,
    "F210M": 0.070, "F250M": 0.080, "F277W": 0.091, "F300M": 0.100,
    "F335M": 0.108, "F356W": 0.115, "F360M": 0.116, "F410M": 0.133,
    "F430M": 0.139, "F444W": 0.145, "F460M": 0.149, "F480M": 0.155,
}


def _ensure_native_f64(arr: np.ndarray) -> np.ndarray:
    """Ensure array is C-contiguous, native byte order, float64 for SEP."""
    return np.ascontiguousarray(arr, dtype=np.float64)


class MeasureStage(PipelineStage):
    name = "measure"

    def run(self) -> dict:
        proc_dir = self.workspace / "processed"
        cat_dir = self.workspace / "catalogs"
        cat_dir.mkdir(parents=True, exist_ok=True)

        # ----- Step 1: Load detection catalog (or deblend catalog) -----
        self.progress(0.0, "Loading detection catalog...")
        self.check_cancel()

        deblend_cat_path = cat_dir / "deblend_catalog.parquet"
        det_cat_path = cat_dir / "detection_catalog.parquet"

        if deblend_cat_path.exists():
            det_cat = read_catalog(deblend_cat_path)
            logger.info("Using deblend catalog: %s", deblend_cat_path.name)
        elif det_cat_path.exists():
            det_cat = read_catalog(det_cat_path)
        else:
            raise FileNotFoundError(
                f"Detection catalog not found: {det_cat_path}. "
                "Run the detect stage first."
            )

        n_sources = det_cat.height
        logger.info("Loaded detection catalog: %d sources", n_sources)

        # Check if deblend columns are present
        has_deblend = "is_deblended" in det_cat.columns

        # Source positions and shape in detection image pixel coords
        src_ra = det_cat["ra"].to_numpy().astype(np.float64)
        src_dec = det_cat["dec"].to_numpy().astype(np.float64)
        src_a = det_cat["a"].to_numpy().astype(np.float64) if "a" in det_cat.columns else np.ones(n_sources)
        src_b = det_cat["b"].to_numpy().astype(np.float64) if "b" in det_cat.columns else np.ones(n_sources)
        src_theta = (det_cat["theta"].to_numpy().astype(np.float64) if "theta" in det_cat.columns
                     else np.zeros(n_sources))

        # ----- Step 2: Discover reprojected or bksub files -----
        self.progress(0.02, "Scanning for filter images...")
        self.check_cancel()

        # Prefer _reproj.fits (reprojected onto common WCS grid) over
        # _bksub.fits (original WCS), since different observations may
        # cover different sky areas.
        REPROJ_PATTERN = re.compile(r"^([A-Z]\d+[A-Z]?)_reproj\.fits$", re.IGNORECASE)

        bksub_files: dict[str, Path] = {}
        for fp in sorted(proc_dir.glob("*_reproj.fits")):
            m = REPROJ_PATTERN.match(fp.name)
            if m:
                filt = m.group(1).upper()
                bksub_files[filt] = fp
                logger.info("Found reproj %s -> %s", filt, fp.name)

        # Fall back to bksub if no reproj files exist
        if not bksub_files:
            for fp in sorted(proc_dir.glob("*_bksub.fits")):
                m = FILTER_PATTERN.match(fp.name)
                if m:
                    filt = m.group(1).upper()
                    bksub_files[filt] = fp
                    logger.info("Found bksub %s -> %s", filt, fp.name)

        if not bksub_files:
            self.progress(1.0, "No bksub files found")
            return {
                "sources_measured": 0,
                "filters": [],
                "aperture_radii_arcsec": [],
                "catalog_path": None,
                "mag_stats": {},
            }

        filters_sorted = sorted(bksub_files.keys())
        n_filters = len(filters_sorted)
        logger.info("Found %d filters: %s", n_filters, filters_sorted)
        self.progress(0.05, f"Found {n_filters} filters for photometry")

        # Aperture radii from config
        aperture_radii_arcsec = self.config.get(
            "aperture_radii_arcsec", [0.16, 0.32, 0.5]
        )
        default_aper_idx = 1  # 0.32 arcsec = middle aperture

        # ----- Step 2b: Optional PSF-matched images -----
        enable_psf_match = self.config.get("enable_psf_matching", True)
        psf_matched_files: dict[str, Path] | None = None
        if enable_psf_match:
            self.progress(0.05, "PSF-matching filter images...")
            self.check_cancel()
            try:
                psf_matched_files = self._psf_match_images(
                    bksub_files, filters_sorted, proc_dir,
                )
                n_matched = sum(1 for f in filters_sorted
                                if psf_matched_files.get(f) != bksub_files.get(f))
                logger.info("PSF-matched %d / %d filters", n_matched, n_filters)
            except Exception as e:
                logger.warning("PSF matching failed, using original images: %s", e)
                psf_matched_files = None

        # ----- Step 3: Per-filter forced photometry -----
        phot_frac_start = 0.10
        phot_frac_end = 0.85
        phot_per_filter = (phot_frac_end - phot_frac_start) / n_filters

        # Accumulate per-filter results as dict of numpy arrays
        filter_results: dict[str, dict[str, np.ndarray]] = {}

        for i, filt in enumerate(filters_sorted):
            self.check_cancel()
            frac = phot_frac_start + i * phot_per_filter
            self.progress(frac, f"Photometry: {filt} ({i+1}/{n_filters})...")

            bksub_path = bksub_files[filt]

            try:
                result = self._measure_filter(
                    bksub_path, filt, src_ra, src_dec,
                    src_a, src_b, src_theta,
                    aperture_radii_arcsec, default_aper_idx,
                )
                # PSF-matched photometry (separate pass on convolved image)
                if psf_matched_files and psf_matched_files.get(filt) != bksub_path:
                    try:
                        psf_result = self._measure_filter(
                            psf_matched_files[filt], filt, src_ra, src_dec,
                            src_a, src_b, src_theta,
                            aperture_radii_arcsec, default_aper_idx,
                            kron=False,  # skip Kron on PSF-matched (use original)
                        )
                        result["flux_psf"] = psf_result["flux"]
                        result["mag_psf"] = psf_result["mag"]
                    except Exception as e:
                        logger.debug("PSF-matched photometry failed for %s: %s", filt, e)

                filter_results[filt] = result
                logger.info(
                    "%s: %d sources measured, median mag=%.2f",
                    filt, n_sources,
                    np.nanmedian(result["mag"]) if np.any(np.isfinite(result["mag"])) else float("nan"),
                )
            except Exception as e:
                logger.error("Photometry failed for %s: %s", filt, e)
                # Fill with NaN
                filter_results[filt] = {
                    "flux": np.full(n_sources, np.nan),
                    "fluxerr": np.full(n_sources, np.nan),
                    "mag": np.full(n_sources, np.nan),
                    "magerr": np.full(n_sources, np.nan),
                    "sn": np.full(n_sources, np.nan),
                }
                for r in aperture_radii_arcsec:
                    r_label = f"{r:.2f}".replace(".", "p")
                    filter_results[filt][f"flux_{r_label}"] = np.full(n_sources, np.nan)
                    filter_results[filt][f"fluxerr_{r_label}"] = np.full(n_sources, np.nan)

        # ----- Step 4: Replace aperture flux with deblended flux where available -----
        if has_deblend:
            is_deb = det_cat["is_deblended"].to_numpy()
            n_replaced = 0
            for filt in filters_sorted:
                deb_col = f"deblend_flux_{filt}"
                if deb_col in det_cat.columns:
                    deb_flux = det_cat[deb_col].to_numpy().astype(np.float64)
                    fr = filter_results[filt]
                    # Replace where deblended, finite, and positive
                    mask = is_deb & np.isfinite(deb_flux) & (deb_flux > 0)
                    if np.any(mask):
                        fr["flux"][mask] = deb_flux[mask]
                        # Recompute mag for replaced sources
                        fr["mag"][mask] = -2.5 * np.log10(deb_flux[mask] / 3631.0)
                        # Clear error estimates for deblended (model-based, no aperture error)
                        fr["fluxerr"][mask] = np.nan
                        fr["magerr"][mask] = np.nan
                        n_replaced += np.sum(mask)
            if n_replaced > 0:
                logger.info("Replaced aperture flux with deblend flux for %d source-filter pairs", n_replaced)

        # ----- Step 5: Merge into catalog -----
        self.progress(0.90, "Building photometry catalog...")
        self.check_cancel()

        # Start with detection catalog columns (exclude deblend_flux_* to avoid duplication)
        result_dict: dict[str, list | np.ndarray] = {}
        for col in det_cat.columns:
            if col.startswith("deblend_flux_"):
                continue  # deblend fluxes already merged into aperture flux
            result_dict[col] = det_cat[col].to_list()

        # Add per-filter photometry columns
        for filt in filters_sorted:
            fr = filter_results[filt]
            result_dict[f"flux_{filt}"] = fr["flux"].tolist()
            result_dict[f"fluxerr_{filt}"] = fr["fluxerr"].tolist()
            result_dict[f"mag_{filt}"] = fr["mag"].tolist()
            result_dict[f"magerr_{filt}"] = fr["magerr"].tolist()
            result_dict[f"sn_{filt}"] = fr["sn"].tolist()

            # Kron aperture columns
            if "flux_kron" in fr:
                result_dict[f"flux_kron_{filt}"] = fr["flux_kron"].tolist()
                result_dict[f"fluxerr_kron_{filt}"] = fr["fluxerr_kron"].tolist()
                result_dict[f"mag_kron_{filt}"] = fr["mag_kron"].tolist()
                result_dict[f"kronrad_{filt}"] = fr["kronrad"].tolist()

            # PSF-matched columns
            if "flux_psf" in fr:
                result_dict[f"flux_psf_{filt}"] = fr["flux_psf"].tolist()
                result_dict[f"mag_psf_{filt}"] = fr["mag_psf"].tolist()

        phot_cat = pl.DataFrame(result_dict)

        # Compute aggregate S/N metrics across all bands
        sn_cols = [f"sn_{filt}" for filt in filters_sorted]
        all_sn = np.column_stack([
            np.array(result_dict[col], dtype=np.float64) for col in sn_cols
        ])
        with warnings.catch_warnings():
            # Sources with no valid band are all-NaN rows; NaN is the intended result.
            warnings.simplefilter("ignore", category=RuntimeWarning)
            max_sn = np.nanmax(all_sn, axis=1)
            median_sn = np.nanmedian(all_sn, axis=1)
        max_sn[~np.isfinite(max_sn)] = np.nan
        median_sn[~np.isfinite(median_sn)] = np.nan
        n_bands_sn5 = np.nansum(all_sn >= 5.0, axis=1).astype(np.int64)

        phot_cat = phot_cat.with_columns([
            pl.Series("max_sn", max_sn),
            pl.Series("median_sn", median_sn),
            pl.Series("n_bands_sn5", n_bands_sn5),
        ])

        # ----- Step 5: Write output -----
        self.progress(0.95, "Writing photometry catalog...")
        phot_path = cat_dir / "photometry_catalog.parquet"
        write_catalog(phot_cat, phot_path)
        logger.info("Wrote photometry catalog: %s (%d sources, %d columns)",
                     phot_path.name, phot_cat.height, phot_cat.width)

        # Compute stats
        mag_stats = {}
        for filt in filters_sorted:
            mags = filter_results[filt]["mag"]
            valid = mags[np.isfinite(mags)]
            if len(valid) > 0:
                sn_arr = filter_results[filt]["sn"]
                valid_sn = sn_arr[np.isfinite(sn_arr)]
                mag_stats[filt] = {
                    "median": float(np.median(valid)),
                    "mean": float(np.mean(valid)),
                    "std": float(np.std(valid)),
                    "n_valid": int(len(valid)),
                    "median_sn": float(np.median(valid_sn)) if len(valid_sn) > 0 else 0.0,
                    "n_sn5": int(np.sum(valid_sn >= 5.0)) if len(valid_sn) > 0 else 0,
                }

        self.progress(1.0, f"Measured {n_sources} sources in {n_filters} filters")

        return {
            "sources_measured": n_sources,
            "filters": filters_sorted,
            "aperture_radii_arcsec": aperture_radii_arcsec,
            "catalog_path": str(phot_path),
            "mag_stats": mag_stats,
        }

    def _psf_match_images(
        self,
        bksub_files: dict[str, Path],
        filters_sorted: list[str],
        proc_dir: Path,
    ) -> dict[str, Path]:
        """Convolve all filter images to match the broadest PSF.

        Creates *_psfmatched.fits files in proc_dir.  Returns dict of
        filter -> path (may point to the original file if already broad enough).
        """
        from astropy.convolution import Gaussian2DKernel, convolve_fft
        try:
            from photutils.psf import create_matching_kernel
        except ImportError:
            from photutils.psf.matching import create_matching_kernel

        # Find broadest PSF among available filters
        target_fwhm = max(PSF_FWHM_ARCSEC.get(f, 0.066) for f in filters_sorted)
        logger.info("PSF matching target FWHM: %.4f arcsec", target_fwhm)

        # Skip PSF matching for very large images (>500M pixels) to avoid OOM
        sample_path = next(iter(bksub_files.values()))
        with fits.open(sample_path) as hdul:
            ny, nx = hdul[0].data.shape
        if ny * nx > 500_000_000:
            logger.warning(
                "Image too large for PSF matching (%dx%d = %dM pixels), skipping",
                nx, ny, ny * nx // 1_000_000,
            )
            return dict(bksub_files)

        matched_files: dict[str, Path] = {}
        for filt in filters_sorted:
            out_path = proc_dir / f"{filt}_psfmatched.fits"

            # Already cached from previous run
            if out_path.exists():
                matched_files[filt] = out_path
                continue

            source_fwhm = PSF_FWHM_ARCSEC.get(filt, 0.066)
            if target_fwhm - source_fwhm < 0.005:
                # Already close enough — no convolution needed
                matched_files[filt] = bksub_files[filt]
                continue

            try:
                with fits.open(bksub_files[filt]) as hdul:
                    hdr = hdul[0].header
                    cdelt = abs(hdr.get("CDELT1", hdr.get("CD1_1", 8.674e-6)))
                    pixel_scale = cdelt * 3600.0  # arcsec/pix

                    source_sigma_pix = (source_fwhm / 2.355) / pixel_scale
                    target_sigma_pix = (target_fwhm / 2.355) / pixel_scale

                    # Kernel size must be odd and large enough
                    ksize = int(8 * target_sigma_pix) | 1  # ensure odd
                    ksize = max(ksize, 11)

                    source_kernel = Gaussian2DKernel(source_sigma_pix, x_size=ksize, y_size=ksize)
                    target_kernel = Gaussian2DKernel(target_sigma_pix, x_size=ksize, y_size=ksize)
                    kernel = create_matching_kernel(source_kernel, target_kernel)

                    data = hdul[0].data.astype(np.float64)
                    nan_mask = ~np.isfinite(data)
                    data[nan_mask] = 0.0
                    matched = convolve_fft(data, kernel, allow_huge=True)
                    matched[nan_mask] = np.nan

                    out_hdu = fits.PrimaryHDU(
                        data=matched.astype(np.float32), header=hdr,
                    )
                    hdu_list = [out_hdu]
                    if "RMS" in hdul:
                        hdu_list.append(hdul["RMS"])
                    fits.HDUList(hdu_list).writeto(out_path, overwrite=True)

                matched_files[filt] = out_path
                logger.info("PSF-matched %s (%.3f -> %.3f arcsec)", filt, source_fwhm, target_fwhm)
            except Exception as e:
                logger.warning("PSF matching failed for %s: %s", filt, e)
                matched_files[filt] = bksub_files[filt]

        return matched_files

    def _measure_filter(
        self,
        bksub_path: Path,
        filt: str,
        src_ra: np.ndarray,
        src_dec: np.ndarray,
        src_a: np.ndarray,
        src_b: np.ndarray,
        src_theta: np.ndarray,
        aperture_radii_arcsec: list[float],
        default_aper_idx: int,
        kron: bool = True,
    ) -> dict[str, np.ndarray]:
        """Perform forced aperture + Kron photometry on a single filter image.

        Returns dict with flux, fluxerr, mag, magerr arrays (default aperture)
        plus per-aperture flux/fluxerr arrays and Kron aperture results.
        """
        n_sources = len(src_ra)

        with fits.open(bksub_path) as hdul:
            sci = hdul[0].data.astype(np.float64)
            hdr = hdul[0].header
            wcs = WCS(hdr)
            rms = hdul["RMS"].data.astype(np.float64)

        sci = _ensure_native_f64(sci)
        rms = _ensure_native_f64(rms)

        # Save original NaN mask BEFORE zeroing — used to check coverage
        no_coverage = ~np.isfinite(sci)

        # Replace NaN with 0 for SEP
        sci[no_coverage] = 0.0
        rms[no_coverage] = 0.0
        # Ensure RMS > 0 where valid
        rms_valid = rms > 0
        if np.any(rms_valid):
            rms[~rms_valid & ~no_coverage] = np.nanmedian(rms[rms_valid])
        else:
            rms[:] = 1.0

        # Convert sky coords to this filter's pixel coords
        x_filt, y_filt = wcs.world_to_pixel_values(src_ra, src_dec)
        x_filt = x_filt.astype(np.float64)
        y_filt = y_filt.astype(np.float64)

        # Mask sources outside the image
        ny, nx = sci.shape
        outside = (
            ~np.isfinite(x_filt) | ~np.isfinite(y_filt)
            | (x_filt < 0) | (x_filt >= nx)
            | (y_filt < 0) | (y_filt >= ny)
        )

        # Mask sources whose center pixel has no coverage (was NaN).
        # These would get near-zero flux from SEP which is a fake measurement.
        in_bounds = ~outside
        ix = np.clip(np.round(x_filt).astype(int), 0, nx - 1)
        iy = np.clip(np.round(y_filt).astype(int), 0, ny - 1)
        center_no_cov = no_coverage[iy, ix]
        no_data = outside | (in_bounds & center_no_cov)

        # Pixel scale for unit conversion
        cdelt = abs(hdr.get("CDELT1", hdr.get("CD1_1", 8.674e-6)))
        pixel_area_sr = (cdelt * math.pi / 180.0) ** 2

        # Perform aperture photometry at each radius
        results: dict[str, np.ndarray] = {}

        # Safe margin for Kron (reused below)
        margin = 6.0  # pixels
        x_safe = np.clip(x_filt, margin + 1, nx - margin - 2)
        y_safe = np.clip(y_filt, margin + 1, ny - margin - 2)

        for j, r_arcsec in enumerate(aperture_radii_arcsec):
            r_pix = r_arcsec / (cdelt * 3600.0)
            r_label = f"{r_arcsec:.2f}".replace(".", "p")

            # Clamp positions for SEP (it crashes on out-of-bounds)
            x_circ = np.clip(x_filt, r_pix + 1, nx - r_pix - 2)
            y_circ = np.clip(y_filt, r_pix + 1, ny - r_pix - 2)

            flux_sum, fluxerr_sum, flag = sep.sum_circle(
                sci, x_circ, y_circ, r_pix, err=rms, subpix=5,
            )

            # Convert MJy/sr * pixels -> Jy
            flux_jy = flux_sum * pixel_area_sr * 1e6
            fluxerr_jy = fluxerr_sum * pixel_area_sr * 1e6

            # Mark sources with no coverage as NaN (outside image OR center pixel NaN)
            flux_jy[no_data] = np.nan
            fluxerr_jy[no_data] = np.nan

            # S/N cut: measurements below S/N=2 are noise, not detections.
            min_sn = float(self.config.get("min_photometry_sn", 2.0))
            low_sn = (
                np.isfinite(flux_jy) & np.isfinite(fluxerr_jy)
                & (fluxerr_jy > 0)
                & (flux_jy / fluxerr_jy < min_sn)
            )
            flux_jy[low_sn] = np.nan
            fluxerr_jy[low_sn] = np.nan

            results[f"flux_{r_label}"] = flux_jy
            results[f"fluxerr_{r_label}"] = fluxerr_jy

            # Default aperture: store as primary flux/mag
            if j == default_aper_idx:
                # Save pre-cut copies for S/N computation (preserves marginal S/N)
                flux_precut = flux_jy.copy()
                fluxerr_precut = fluxerr_jy.copy()

                results["flux"] = flux_jy.copy()
                results["fluxerr"] = fluxerr_jy.copy()

                # AB magnitude — only for significant detections
                mag = np.full(n_sources, np.nan)
                magerr = np.full(n_sources, np.nan)
                pos = np.isfinite(flux_jy) & (flux_jy > 0)
                mag[pos] = -2.5 * np.log10(flux_jy[pos] / 3631.0)
                # mag error from flux error propagation
                valid_err = pos & np.isfinite(fluxerr_jy) & (fluxerr_jy > 0)
                magerr[valid_err] = (2.5 / math.log(10.0)) * (
                    fluxerr_jy[valid_err] / flux_jy[valid_err]
                )
                results["mag"] = mag
                results["magerr"] = magerr

                # S/N ratio from pre-cut values (preserves marginal S/N)
                sn = np.full(n_sources, np.nan)
                sn_valid = (
                    np.isfinite(flux_precut) & np.isfinite(fluxerr_precut)
                    & (fluxerr_precut > 0)
                )
                sn[sn_valid] = flux_precut[sn_valid] / fluxerr_precut[sn_valid]
                # Negative flux = noise fluctuation below sky → S/N = 0
                sn[sn_valid & (flux_precut <= 0)] = 0.0
                results["sn"] = sn

        # ----- Kron adaptive aperture photometry -----
        if kron:
            try:
                # Ensure shape params are safe for SEP
                a_safe = np.clip(np.where(np.isfinite(src_a), src_a, 1.0), 0.5, 100.0)
                b_safe = np.clip(np.where(np.isfinite(src_b), src_b, 1.0), 0.5, 100.0)
                theta_safe = np.where(np.isfinite(src_theta), src_theta, 0.0)
                # Clamp theta to [-pi/2, pi/2] for SEP
                theta_safe = np.clip(theta_safe, -math.pi / 2, math.pi / 2)

                kronrad, krflag = sep.kron_radius(
                    sci, x_safe, y_safe, a_safe, b_safe, theta_safe, 6.0,
                )
                # Clamp Kron radius to reasonable range
                kronrad = np.clip(
                    np.where(np.isfinite(kronrad), kronrad, 3.5),
                    3.5, 50.0,
                )

                # Kron flux using elliptical aperture at 2.5 * kron_radius
                flux_kron, fluxerr_kron, kflag = sep.sum_ellipse(
                    sci, x_safe, y_safe, a_safe, b_safe, theta_safe,
                    2.5 * kronrad, err=rms, subpix=5,
                )

                # Convert MJy/sr * pixels -> Jy
                flux_kron_jy = flux_kron * pixel_area_sr * 1e6
                fluxerr_kron_jy = fluxerr_kron * pixel_area_sr * 1e6
                flux_kron_jy[no_data] = np.nan
                fluxerr_kron_jy[no_data] = np.nan

                # Kron magnitude
                mag_kron = np.full(n_sources, np.nan)
                pos_k = np.isfinite(flux_kron_jy) & (flux_kron_jy > 0)
                mag_kron[pos_k] = -2.5 * np.log10(flux_kron_jy[pos_k] / 3631.0)

                results["flux_kron"] = flux_kron_jy
                results["fluxerr_kron"] = fluxerr_kron_jy
                results["mag_kron"] = mag_kron
                results["kronrad"] = kronrad
            except Exception as e:
                logger.debug("Kron photometry failed for %s: %s", filt, e)

        # Free memory
        del sci, rms
        gc.collect()

        return results
