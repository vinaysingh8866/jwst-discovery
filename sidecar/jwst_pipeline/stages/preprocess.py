"""Stage 2: Background subtraction, WCS alignment, and detection image creation.

Takes raw i2d FITS mosaics from workspace/raw/, produces per-filter
background-subtracted images and an inverse-variance weighted detection image.
"""
import gc
import logging
import re
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from astropy.wcs import WCS

from jwst_pipeline.stages.base import PipelineStage

logger = logging.getLogger(__name__)

FILTER_PATTERN = re.compile(r"clear-([a-z]\d+[a-z])", re.IGNORECASE)
# HLSP mosaics use _f200w_ naming (e.g. hlsp_jades_jwst_nircam_goods-n_f200w_v1.0_i2d.fits)
HLSP_FILTER_PATTERN = re.compile(r"_([fF]\d+[wWmM])_", re.IGNORECASE)


def _native_byteorder(arr: np.ndarray) -> np.ndarray:
    """Ensure array is native byte order (required by photutils/sep)."""
    return np.ascontiguousarray(arr, dtype=arr.dtype.newbyteorder("="))


def _extract_filter(filename: str) -> str | None:
    """Extract filter name from JWST i2d filename.

    Handles both standard Level 3 naming (clear-f200w) and HLSP naming (_f200w_).
    """
    m = FILTER_PATTERN.search(filename)
    if m:
        return m.group(1).upper()
    m2 = HLSP_FILTER_PATTERN.search(filename)
    return m2.group(1).upper() if m2 else None


class PreprocessStage(PipelineStage):
    name = "preprocess"

    def run(self) -> dict:
        raw_dir = self.workspace / "raw"
        proc_dir = self.workspace / "processed"
        proc_dir.mkdir(parents=True, exist_ok=True)

        # ----- Step 1: Discover inputs -----
        self.progress(0.0, "Scanning for FITS files...")
        self.check_cancel()

        # Decompress any .fits.gz files in raw/
        for gz_file in sorted(raw_dir.glob("*.fits.gz")):
            decompressed = gz_file.with_suffix("")  # strip .gz
            if not decompressed.exists():
                import gzip
                import shutil
                self.progress(0.0, f"Decompressing {gz_file.name}...")
                with gzip.open(gz_file, "rb") as f_in, open(decompressed, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                logger.info("Decompressed %s -> %s", gz_file.name, decompressed.name)

        # Accept standard pipeline (_i2d.fits), HLSP drizzled (_drz.fits), and HLSP science (_sci.fits)
        fits_files = (sorted(raw_dir.glob("*_i2d.fits"))
                      + sorted(raw_dir.glob("*_drz.fits"))
                      + sorted(raw_dir.glob("*_sci.fits")))
        if not fits_files:
            self.progress(1.0, "No i2d/drz FITS files found in raw/")
            return {
                "images_processed": 0,
                "filters": [],
                "detection_image": None,
                "common_shape": None,
                "bkg_failures": [],
            }

        # Map filter -> file path
        filter_files: dict[str, Path] = {}
        for fp in fits_files:
            filt = _extract_filter(fp.name)
            if filt:
                filter_files[filt] = fp
                logger.info("Found %s -> %s", filt, fp.name)

        if not filter_files:
            self.progress(1.0, "No files matched filter pattern")
            return {
                "images_processed": 0,
                "filters": [],
                "detection_image": None,
                "common_shape": None,
                "bkg_failures": [],
            }

        filters_sorted = sorted(filter_files.keys())
        n_filters = len(filters_sorted)
        logger.info("Found %d filters: %s", n_filters, filters_sorted)
        self.progress(0.05, f"Found {n_filters} filters: {', '.join(filters_sorted)}")

        # ----- Step 2: Per-filter background subtraction -----
        bkg_failures = []
        bksub_files: dict[str, Path] = {}
        wcs_list: list[WCS] = []

        bkg_frac_start = 0.05
        bkg_frac_end = 0.50
        bkg_per_filter = (bkg_frac_end - bkg_frac_start) / n_filters

        for i, filt in enumerate(filters_sorted):
            self.check_cancel()
            frac = bkg_frac_start + i * bkg_per_filter
            self.progress(frac, f"Background subtraction: {filt} ({i+1}/{n_filters})...")

            fits_path = filter_files[filt]
            bksub_path = proc_dir / f"{filt}_bksub.fits"

            try:
                sci, rms, wcs, orig_hdr = self._subtract_background(fits_path, filt)
                wcs_list.append(wcs)

                # Write bksub FITS using original header to preserve CD matrix
                hdr = orig_hdr.copy()
                hdr["FILTER"] = filt
                hdr["BUNIT"] = "MJy/sr"
                # Remove data-specific keys that conflict
                for key in ["BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "EXTEND", "SIMPLE"]:
                    hdr.pop(key, None)
                primary = fits.PrimaryHDU(data=sci.astype(np.float32), header=hdr)
                rms_hdu = fits.ImageHDU(data=rms.astype(np.float32), name="RMS")
                hdul = fits.HDUList([primary, rms_hdu])
                hdul.writeto(bksub_path, overwrite=True)
                hdul.close()

                bksub_files[filt] = bksub_path
                logger.info("Wrote %s (%d x %d)", bksub_path.name, sci.shape[1], sci.shape[0])

                # Free memory
                del sci, rms
                gc.collect()

            except Exception as e:
                logger.error("Background subtraction failed for %s: %s", filt, e)
                bkg_failures.append(filt)

        if not bksub_files:
            self.progress(1.0, "All background subtractions failed")
            return {
                "images_processed": 0,
                "filters": filters_sorted,
                "detection_image": None,
                "common_shape": None,
                "bkg_failures": bkg_failures,
            }

        # ----- Step 3: Common WCS grid -----
        self.progress(0.50, "Computing common WCS grid...")
        self.check_cancel()

        # Check if all bksub files share the same WCS and shape (e.g. HLSP mosaics).
        # If so, skip expensive reprojection entirely.
        first_filt = sorted(bksub_files.keys())[0]
        with fits.open(bksub_files[first_filt]) as hdul:
            ref_hdr = hdul[0].header
            ref_shape = hdul[0].data.shape
            ref_crval1 = ref_hdr.get("CRVAL1")
            ref_crval2 = ref_hdr.get("CRVAL2")
            ref_crpix1 = ref_hdr.get("CRPIX1")
            ref_crpix2 = ref_hdr.get("CRPIX2")

        all_same_wcs = True
        for p in bksub_files.values():
            with fits.open(p) as hdul:
                hdr = hdul[0].header
                shape = hdul[0].data.shape
                if (shape != ref_shape
                    or hdr.get("CRVAL1") != ref_crval1
                    or hdr.get("CRVAL2") != ref_crval2
                    or hdr.get("CRPIX1") != ref_crpix1
                    or hdr.get("CRPIX2") != ref_crpix2):
                    all_same_wcs = False
                    break

        if all_same_wcs:
            # All filters on the same pixel grid — use directly, no reprojection
            common_wcs = WCS(ref_hdr)
            common_shape = ref_shape
            logger.info(
                "All %d filters share identical WCS grid (%d x %d) — skipping reprojection",
                len(bksub_files), common_shape[1], common_shape[0],
            )
            self.progress(0.55, f"Same-grid mode: {common_shape[1]} x {common_shape[0]}")
        else:
            # Different WCS grids — compute union bounding box
            best_cdelt = None
            all_corners_ra = []
            all_corners_dec = []

            for p in bksub_files.values():
                with fits.open(p) as hdul:
                    hdr = hdul[0].header
                    w = WCS(hdr)
                    ny, nx = hdul[0].data.shape
                    cdelt = abs(hdr.get("CDELT1", hdr.get("CD1_1", 1e-5)))
                    if best_cdelt is None or cdelt < best_cdelt:
                        best_cdelt = cdelt
                    corners_x = [0, nx - 1, 0, nx - 1]
                    corners_y = [0, 0, ny - 1, ny - 1]
                    cra, cdec = w.pixel_to_world_values(corners_x, corners_y)
                    all_corners_ra.extend(cra.tolist())
                    all_corners_dec.extend(cdec.tolist())

            ra_min, ra_max = min(all_corners_ra), max(all_corners_ra)
            dec_min, dec_max = min(all_corners_dec), max(all_corners_dec)
            ra_center = (ra_min + ra_max) / 2.0
            dec_center = (dec_min + dec_max) / 2.0
            cos_dec = np.cos(np.radians(dec_center))

            nx_common = int(np.ceil((ra_max - ra_min) * cos_dec / best_cdelt)) + 20
            ny_common = int(np.ceil((dec_max - dec_min) / best_cdelt)) + 20

            common_wcs = WCS(naxis=2)
            common_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
            common_wcs.wcs.crval = [ra_center, dec_center]
            common_wcs.wcs.crpix = [nx_common / 2.0, ny_common / 2.0]
            common_wcs.wcs.cdelt = [-best_cdelt, best_cdelt]
            common_shape = (ny_common, nx_common)

            logger.info(
                "Union grid: %d x %d, center (%.4f, %.4f), scale %.4f\"/pix",
                nx_common, ny_common, ra_center, dec_center, best_cdelt * 3600,
            )
            self.progress(0.55, f"Common grid: {nx_common} x {ny_common}")

        # ----- Step 4: Incremental stack (reproject only if needed) -----
        numerator = np.zeros(common_shape, dtype=np.float32)
        denominator = np.zeros(common_shape, dtype=np.float32)
        n_exp = np.zeros(common_shape, dtype=np.int16)

        repr_frac_start = 0.55
        repr_frac_end = 0.95
        repr_per_filter = (repr_frac_end - repr_frac_start) / len(bksub_files)

        active_filters = sorted(bksub_files.keys())

        for i, filt in enumerate(active_filters):
            self.check_cancel()
            frac = repr_frac_start + i * repr_per_filter
            label = "Stacking" if all_same_wcs else "Reprojecting"
            self.progress(frac, f"{label} {filt} ({i+1}/{len(active_filters)})...")

            bksub_path = bksub_files[filt]

            try:
                with fits.open(bksub_path) as hdul:
                    sci_data = hdul[0].data.astype(np.float32)
                    sci_hdr = hdul[0].header
                    rms_data = hdul["RMS"].data.astype(np.float32)

                if all_same_wcs:
                    # Direct pixel-by-pixel stacking — no reprojection needed
                    reproj_sci = sci_data
                    reproj_rms = rms_data
                    del sci_data, rms_data
                else:
                    # Reproject to common grid
                    from reproject import reproject_interp

                    input_wcs = WCS(sci_hdr)
                    reproj_sci = np.empty(common_shape, dtype=np.float32)
                    footprint = np.empty(common_shape, dtype=np.float32)
                    reproject_interp(
                        (sci_data, input_wcs), common_wcs,
                        shape_out=common_shape,
                        output_array=reproj_sci,
                        output_footprint=footprint,
                    )
                    del sci_data
                    reproj_rms = np.empty(common_shape, dtype=np.float32)
                    reproject_interp(
                        (rms_data, input_wcs), common_wcs,
                        shape_out=common_shape,
                        output_array=reproj_rms,
                    )
                    del rms_data
                    gc.collect()

                # Build valid mask
                valid = (
                    np.isfinite(reproj_sci)
                    & np.isfinite(reproj_rms)
                    & (reproj_rms > 0)
                )
                if not all_same_wcs:
                    valid &= (footprint > 0)
                    del footprint

                n_valid = int(np.sum(valid))

                # Accumulate: weight = 1/rms^2
                w = np.float32(1.0) / (reproj_rms[valid] ** 2)
                numerator[valid] += reproj_sci[valid] * w
                denominator[valid] += w
                n_exp[valid] += 1
                del w

                logger.info("%s %s: %d valid pixels", label, filt, n_valid)

                # Save per-filter image for forced photometry.
                # In same-grid mode, symlink _reproj.fits -> _bksub.fits to
                # avoid doubling disk usage on large mosaics.
                reproj_path = proc_dir / f"{filt}_reproj.fits"
                if all_same_wcs:
                    if reproj_path.exists() or reproj_path.is_symlink():
                        reproj_path.unlink()
                    reproj_path.symlink_to(bksub_path.name)
                else:
                    reproj_sci[~valid] = np.nan
                    reproj_rms[~valid] = np.nan
                    repr_hdr = common_wcs.to_header()
                    repr_hdr["FILTER"] = filt
                    repr_hdr["BUNIT"] = "MJy/sr"
                    repr_primary = fits.PrimaryHDU(data=reproj_sci, header=repr_hdr)
                    repr_rms_hdu = fits.ImageHDU(data=reproj_rms, name="RMS")
                    repr_hdul = fits.HDUList([repr_primary, repr_rms_hdu])
                    repr_hdul.writeto(reproj_path, overwrite=True)
                    repr_hdul.close()

                del reproj_sci, reproj_rms, valid
                gc.collect()

            except Exception as e:
                logger.error("Stack/reproject failed for %s: %s", filt, e)
                bkg_failures.append(f"{filt}_reproject")

        # ----- Step 5: Write detection image -----
        self.progress(0.95, "Writing detection image...")
        self.check_cancel()

        # Compute final stack (in-place to save memory)
        good = denominator > 0
        numerator[good] /= denominator[good]
        numerator[~good] = 0.0
        detection_sci = numerator  # reuse array
        del numerator

        detection_rms = np.zeros(common_shape, dtype=np.float32)
        detection_rms[good] = np.float32(1.0) / np.sqrt(denominator[good])
        del denominator
        gc.collect()

        detection_path = proc_dir / "detection.fits"
        det_hdr = common_wcs.to_header()
        det_hdr["NFILTERS"] = len(active_filters)
        det_hdr["FILTERS"] = ",".join(active_filters)
        det_hdr["BUNIT"] = "MJy/sr"

        primary = fits.PrimaryHDU(data=detection_sci, header=det_hdr)
        rms_hdu = fits.ImageHDU(data=detection_rms, name="RMS")
        nexp_hdu = fits.ImageHDU(data=n_exp, name="NEXP")
        hdul = fits.HDUList([primary, rms_hdu, nexp_hdu])
        hdul.writeto(detection_path, overwrite=True)
        hdul.close()

        logger.info(
            "Detection image: %s (%d x %d, %d filters, %.0f%% coverage)",
            detection_path.name,
            common_shape[1],
            common_shape[0],
            len(active_filters),
            100.0 * np.sum(n_exp > 0) / n_exp.size,
        )

        # Verify
        self.progress(0.98, "Verifying outputs...")
        assert detection_path.exists(), "Detection image not written"
        for filt, path in bksub_files.items():
            assert path.exists(), f"Bksub file missing: {filt}"

        self.progress(1.0, f"Preprocessed {len(bksub_files)} filters -> detection image")

        return {
            "images_processed": len(bksub_files),
            "filters": list(bksub_files.keys()),
            "detection_image": str(detection_path),
            "common_shape": list(common_shape),
            "bkg_failures": bkg_failures,
        }

    def _subtract_background(
        self, fits_path: Path, filt: str
    ) -> tuple[np.ndarray, np.ndarray, WCS, fits.Header]:
        """Subtract 2D background from a single filter's i2d FITS.

        Returns (background-subtracted SCI, RMS map, WCS, original header).
        """
        with fits.open(fits_path) as hdul:
            # i2d files: SCI is in ext 'SCI' or ext 0/1
            if "SCI" in hdul:
                sci = hdul["SCI"].data.astype(np.float32)
                wcs = WCS(hdul["SCI"].header)
                orig_header = hdul["SCI"].header.copy()
            else:
                sci = hdul[0].data.astype(np.float32)
                wcs = WCS(hdul[0].header)
                orig_header = hdul[0].header.copy()

        sci = _native_byteorder(sci)

        # Mask NaN/inf pixels
        coverage_mask = ~np.isfinite(sci)
        sci[coverage_mask] = 0.0

        # Attempt Background2D
        try:
            from astropy.stats import SigmaClip
            from photutils.background import Background2D, SExtractorBackground

            sigma_clip = SigmaClip(sigma=3.0)
            bkg = Background2D(
                sci,
                box_size=128,
                filter_size=3,
                sigma_clip=sigma_clip,
                bkg_estimator=SExtractorBackground(),
                coverage_mask=coverage_mask,
            )
            bkg_image = bkg.background
            rms_image = bkg.background_rms

            logger.info(
                "%s Background2D: median=%.4f, rms_median=%.4f",
                filt,
                np.nanmedian(bkg_image),
                np.nanmedian(rms_image),
            )

        except Exception as e:
            # Fallback: scalar background from sigma-clipped stats
            logger.warning(
                "%s Background2D failed (%s), falling back to sigma_clipped_stats",
                filt, e,
            )
            mean, median, std = sigma_clipped_stats(
                sci[~coverage_mask], sigma=3.0
            )
            bkg_image = np.full_like(sci, median)
            rms_image = np.full_like(sci, std)

        subtracted = sci - bkg_image
        # Restore NaN at coverage edges
        subtracted[coverage_mask] = np.nan
        rms_image[coverage_mask] = np.nan

        return subtracted, rms_image, wcs, orig_header
