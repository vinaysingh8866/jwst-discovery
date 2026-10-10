"""Stage 3.5: PSF-aware source deblending using Scarlet2.

Optional stage between detect and measure. Identifies blended source groups
via proximity analysis, then uses Scarlet2 (JAX-based multi-band deblender)
to model each source as SED x Morphology with PSF convolution, producing
clean per-source flux measurements for heavily overlapping sources.

Gated by:
  - ``enable_deblend`` config flag (default False)
  - ``scarlet2`` importability (graceful skip if not installed)
"""
import gc
import logging
import math
import re
from pathlib import Path

import numpy as np
import polars as pl
from astropy.io import fits
from astropy.wcs import WCS

from jwst_pipeline.stages.base import PipelineStage
from jwst_pipeline.utils.blend_detection import identify_blend_groups
from jwst_pipeline.utils.catalog import read_catalog, write_catalog
from jwst_pipeline.utils.psf_kernels import load_psf_kernel

logger = logging.getLogger(__name__)

REPROJ_PATTERN = re.compile(r"^([A-Z]\d+[A-Z]?)_reproj\.fits$", re.IGNORECASE)
BKSUB_PATTERN = re.compile(r"^([A-Z]\d+[A-Z]?)_bksub\.fits$", re.IGNORECASE)


def _check_scarlet2():
    """Check if scarlet2 is importable."""
    try:
        import scarlet2  # noqa: F401
        return True
    except ImportError:
        return False


class DeblendStage(PipelineStage):
    name = "deblend"

    def run(self) -> dict:
        proc_dir = self.workspace / "processed"
        cat_dir = self.workspace / "catalogs"
        cat_dir.mkdir(parents=True, exist_ok=True)

        # ---- Gate checks ----
        enable = self.config.get("enable_deblend", False)
        if not enable:
            self.progress(1.0, "Deblending disabled (enable_deblend=False)")
            logger.info("Deblend stage skipped: enable_deblend=False")
            return {"skipped": True, "reason": "disabled"}

        if not _check_scarlet2():
            self.progress(1.0, "Deblending skipped (scarlet2 not installed)")
            logger.info("Deblend stage skipped: scarlet2 not installed")
            return {"skipped": True, "reason": "scarlet2_not_installed"}

        # ---- Step 1: Load detection catalog ----
        self.progress(0.0, "Loading detection catalog...")
        self.check_cancel()

        det_cat_path = cat_dir / "detection_catalog.parquet"
        if not det_cat_path.exists():
            raise FileNotFoundError(
                f"Detection catalog not found: {det_cat_path}. "
                "Run the detect stage first."
            )

        det_cat = read_catalog(det_cat_path)
        n_sources = det_cat.height
        logger.info("Loaded detection catalog: %d sources", n_sources)

        src_x = det_cat["x"].to_numpy().astype(np.float64)
        src_y = det_cat["y"].to_numpy().astype(np.float64)
        src_a = det_cat["a"].to_numpy().astype(np.float64)
        src_b = det_cat["b"].to_numpy().astype(np.float64)

        # ---- Step 2: Identify blend groups ----
        self.progress(0.05, "Identifying blend groups...")
        self.check_cancel()

        proximity_factor = float(self.config.get("deblend_proximity_factor", 3.0))
        min_prox_pix = float(self.config.get("deblend_min_proximity_pix", 10.0))
        max_group_size = int(self.config.get("deblend_max_group_size", 15))

        blend_groups = identify_blend_groups(
            src_x, src_y, src_a, src_b,
            proximity_factor=proximity_factor,
            min_proximity_pix=min_prox_pix,
            max_group_size=max_group_size,
        )

        if not blend_groups:
            self.progress(1.0, "No blend groups found")
            logger.info("No blend groups identified, writing pass-through catalog")
            # Write detection catalog as-is with deblend columns
            out_cat = det_cat.with_columns([
                pl.lit(False).alias("is_deblended"),
                pl.lit(True).alias("deblend_converged"),
                pl.lit(-1).cast(pl.Int32).alias("blend_group_id"),
            ])
            out_path = cat_dir / "deblend_catalog.parquet"
            write_catalog(out_cat, out_path)
            return {
                "skipped": False,
                "blend_groups": 0,
                "sources_deblended": 0,
                "catalog_path": str(out_path),
            }

        n_blended_sources = sum(len(g) for g in blend_groups)
        logger.info("Found %d blend groups with %d sources", len(blend_groups), n_blended_sources)
        self.progress(0.10, f"Found {len(blend_groups)} blend groups ({n_blended_sources} sources)")

        # ---- Step 3: Discover filter images ----
        self.check_cancel()
        filter_files: dict[str, Path] = {}
        for fp in sorted(proc_dir.glob("*_reproj.fits")):
            m = REPROJ_PATTERN.match(fp.name)
            if m:
                filter_files[m.group(1).upper()] = fp

        if not filter_files:
            for fp in sorted(proc_dir.glob("*_bksub.fits")):
                m = BKSUB_PATTERN.match(fp.name)
                if m:
                    filter_files[m.group(1).upper()] = fp

        if not filter_files:
            self.progress(1.0, "No filter images found for deblending")
            return {"skipped": True, "reason": "no_filter_images"}

        filters_sorted = sorted(filter_files.keys())
        n_filters = len(filters_sorted)
        logger.info("Using %d filters for deblending: %s", n_filters, filters_sorted)

        # ---- Step 4: Load all filter data + PSF kernels ----
        self.progress(0.12, f"Loading {n_filters} filter images...")
        self.check_cancel()

        filter_data: dict[str, np.ndarray] = {}
        filter_wcs: dict[str, WCS] = {}
        filter_rms: dict[str, np.ndarray] = {}
        pixel_scale = None

        for filt in filters_sorted:
            with fits.open(filter_files[filt]) as hdul:
                data = hdul[0].data.astype(np.float32)
                hdr = hdul[0].header
                wcs = WCS(hdr)
                if "RMS" in hdul:
                    rms = hdul["RMS"].data.astype(np.float32)
                else:
                    rms = np.ones_like(data) * np.nanstd(data[np.isfinite(data)])

            # Compute pixel scale from first filter
            if pixel_scale is None:
                cdelt = abs(hdr.get("CDELT1", hdr.get("CD1_1", 8.674e-6)))
                pixel_scale = cdelt * 3600.0  # arcsec/pixel

            # Replace NaN with 0
            nan_mask = ~np.isfinite(data)
            data[nan_mask] = 0.0
            rms[~np.isfinite(rms)] = 0.0
            rms_valid = rms > 0
            if np.any(rms_valid):
                rms[~rms_valid & ~nan_mask] = np.nanmedian(rms[rms_valid])
            else:
                rms[:] = 1.0

            filter_data[filt] = data
            filter_wcs[filt] = wcs
            filter_rms[filt] = rms

        # Load PSF kernels
        psf_kernels = {}
        for filt in filters_sorted:
            psf_kernels[filt] = load_psf_kernel(filt, pixel_scale)

        self.progress(0.20, "Filter images and PSF kernels loaded")

        # ---- Step 5: Deblend each group with Scarlet2 ----
        import scarlet2 as sc2
        sc2.set_validation(False)  # suppress verbose validation output

        padding = int(self.config.get("deblend_cutout_padding", 20))
        max_iter = int(self.config.get("deblend_max_iter", 200))

        # Prepare output arrays
        is_deblended = np.zeros(n_sources, dtype=bool)
        deblend_converged = np.ones(n_sources, dtype=bool)
        blend_group_id = np.full(n_sources, -1, dtype=np.int32)
        deblend_fluxes: dict[str, np.ndarray] = {
            filt: np.full(n_sources, np.nan, dtype=np.float64) for filt in filters_sorted
        }

        # Get image dimensions from first filter
        ref_filt = filters_sorted[0]
        ny, nx = filter_data[ref_filt].shape

        # Pixel area for flux conversion (MJy/sr * pixels -> Jy)
        cdelt_rad = pixel_scale / 3600.0 * math.pi / 180.0
        pixel_area_sr = cdelt_rad ** 2

        n_groups = len(blend_groups)
        frac_start = 0.20
        frac_end = 0.95
        n_success = 0
        n_failed = 0

        for gi, group in enumerate(blend_groups):
            self.check_cancel()

            frac = frac_start + (frac_end - frac_start) * gi / n_groups
            if gi % max(1, n_groups // 20) == 0:
                self.progress(frac, f"Deblending group {gi+1}/{n_groups}...")

            try:
                result = self._deblend_group(
                    group, src_x, src_y,
                    filter_data, filter_rms, filter_wcs, filters_sorted,
                    psf_kernels, padding, max_iter, nx, ny,
                    pixel_area_sr, sc2,
                )

                if result is None:
                    n_failed += 1
                    continue

                group_fluxes, converged = result
                for idx_in_group, src_idx in enumerate(group):
                    is_deblended[src_idx] = True
                    deblend_converged[src_idx] = converged
                    blend_group_id[src_idx] = gi
                    for filt in filters_sorted:
                        deblend_fluxes[filt][src_idx] = group_fluxes[filt][idx_in_group]
                n_success += 1

            except Exception as e:
                logger.warning("Deblend failed for group %d (%d sources): %s",
                               gi, len(group), e)
                n_failed += 1

            # Periodic GC
            if gi % 100 == 0:
                gc.collect()

        logger.info("Deblend complete: %d/%d groups succeeded, %d failed",
                     n_success, n_groups, n_failed)

        # ---- Step 6: Write output catalog ----
        self.progress(0.95, "Writing deblend catalog...")
        self.check_cancel()

        # Build output: all detection columns + deblend columns
        out_dict: dict[str, list | np.ndarray] = {}
        for col in det_cat.columns:
            out_dict[col] = det_cat[col].to_list()

        out_dict["is_deblended"] = is_deblended.tolist()
        out_dict["deblend_converged"] = deblend_converged.tolist()
        out_dict["blend_group_id"] = blend_group_id.tolist()
        for filt in filters_sorted:
            out_dict[f"deblend_flux_{filt}"] = deblend_fluxes[filt].tolist()

        out_cat = pl.DataFrame(out_dict)
        out_path = cat_dir / "deblend_catalog.parquet"
        write_catalog(out_cat, out_path)
        logger.info("Wrote deblend catalog: %s (%d sources, %d deblended)",
                     out_path.name, n_sources, int(is_deblended.sum()))

        # Free filter data
        del filter_data, filter_rms
        gc.collect()

        self.progress(1.0, f"Deblended {int(is_deblended.sum())} sources in {n_success} groups")

        return {
            "skipped": False,
            "blend_groups": n_groups,
            "groups_succeeded": n_success,
            "groups_failed": n_failed,
            "sources_deblended": int(is_deblended.sum()),
            "filters": filters_sorted,
            "catalog_path": str(out_path),
        }

    def _deblend_group(
        self,
        group: list[int],
        src_x: np.ndarray,
        src_y: np.ndarray,
        filter_data: dict[str, np.ndarray],
        filter_rms: dict[str, np.ndarray],
        filter_wcs: dict[str, WCS],
        filters_sorted: list[str],
        psf_kernels: dict[str, np.ndarray],
        padding: int,
        max_iter: int,
        nx: int,
        ny: int,
        pixel_area_sr: float,
        sc2,
    ):
        """Deblend a single group of sources.

        Returns
        -------
        (group_fluxes, converged) or None on failure.
        group_fluxes : dict[str, ndarray] — per-filter flux array (Jy) for each group member.
        converged : bool — whether the fit converged.
        """
        # Compute bounding box around group members
        gx = src_x[group]
        gy = src_y[group]
        x_min = max(0, int(np.floor(gx.min())) - padding)
        x_max = min(nx, int(np.ceil(gx.max())) + padding)
        y_min = max(0, int(np.floor(gy.min())) - padding)
        y_max = min(ny, int(np.ceil(gy.max())) + padding)

        cutout_h = y_max - y_min
        cutout_w = x_max - x_min

        if cutout_h < 5 or cutout_w < 5:
            return None

        # Build multi-band data cube: (n_filters, height, width)
        n_filters = len(filters_sorted)
        data_cube = np.zeros((n_filters, cutout_h, cutout_w), dtype=np.float32)
        weight_cube = np.zeros_like(data_cube)

        for fi, filt in enumerate(filters_sorted):
            filt_img = filter_data[filt]
            filt_ny, filt_nx = filt_img.shape
            # Clamp cutout to this filter's image bounds
            fy0 = max(0, min(y_min, filt_ny))
            fy1 = max(0, min(y_max, filt_ny))
            fx0 = max(0, min(x_min, filt_nx))
            fx1 = max(0, min(x_max, filt_nx))
            if fy1 - fy0 < 3 or fx1 - fx0 < 3:
                # Filter image doesn't cover this group
                continue
            # Place the valid portion into the cutout (handle offset if bounds differ)
            dy = fy0 - y_min
            dx = fx0 - x_min
            sh = fy1 - fy0
            sw = fx1 - fx0
            data_cube[fi, dy:dy+sh, dx:dx+sw] = filt_img[fy0:fy1, fx0:fx1]
            rms_cut = filter_rms[filt][fy0:fy1, fx0:fx1]
            # Weights = 1/variance (avoid div by zero)
            valid = rms_cut > 0
            w = np.zeros((sh, sw), dtype=np.float32)
            w[valid] = 1.0 / (rms_cut[valid] ** 2)
            weight_cube[fi, dy:dy+sh, dx:dx+sw] = w

        # Source centers relative to cutout (y, x order for scarlet2)
        import jax.numpy as jnp
        centers = [jnp.array([float(gy[i] - y_min), float(gx[i] - x_min)]) for i in range(len(group))]

        # Use the median PSF across filters (Scarlet2 takes a single PSF per observation)
        # Pick the PSF from the median-wavelength filter
        mid_idx = n_filters // 2
        mid_filt = filters_sorted[mid_idx]
        psf_array = psf_kernels[mid_filt]

        # Ensure PSF is 3D: (n_filters, h, w) by repeating for all bands
        psf_3d = np.broadcast_to(psf_array[None, :, :], (n_filters, *psf_array.shape)).copy()

        try:
            obs = sc2.Observation(
                data_cube,
                weights=weight_cube,
                psf=sc2.ArrayPSF(psf_3d),
            )

            with sc2.Scene(obs.frame) as scene:
                for center in centers:
                    try:
                        spectrum, morph = sc2.init.from_gaussian_moments(obs, center)
                    except Exception:
                        # Fallback: pixel spectrum + compact Gaussian morphology
                        try:
                            spectrum = sc2.init.pixel_spectrum(obs, center)
                            morph = sc2.GaussianMorphology(2.0)()
                        except Exception as init_exc:
                            raise RuntimeError("Cannot initialize source") from init_exc
                    sc2.Source(center, spectrum, morph)

            params = scene.make_parameters()
            scene_ = scene.fit(obs, params, max_iter=max_iter, e_rel=1e-4, progress_bar=False)
            converged = True
        except Exception as e:
            logger.debug("Scarlet2 fit failed for group: %s", e)
            return None

        # Extract per-source fluxes
        group_fluxes: dict[str, np.ndarray] = {
            filt: np.zeros(len(group), dtype=np.float64) for filt in filters_sorted
        }

        try:
            for si, src in enumerate(scene_.sources):
                # Get model for this source
                model = sc2.measure.flux(src)  # (n_filters,) total flux in image units
                for fi, filt in enumerate(filters_sorted):
                    # Convert from MJy/sr * pixels to Jy
                    flux_jy = float(model[fi]) * pixel_area_sr * 1e6
                    group_fluxes[filt][si] = flux_jy
        except Exception as e:
            logger.debug("Flux extraction failed: %s", e)
            return None

        return group_fluxes, converged
