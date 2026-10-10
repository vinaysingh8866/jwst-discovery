"""Stage 3: Source detection using SEP on the detection image.

Runs SEP (SExtractor as a library) on the inverse-variance weighted detection
image, producing a Polars-based source catalog and a segmentation map.
"""
import logging

import numpy as np
import polars as pl
import sep
from astropy.io import fits
from astropy.wcs import WCS

from jwst_pipeline.stages.base import PipelineStage
from jwst_pipeline.utils.catalog import write_catalog

logger = logging.getLogger(__name__)


def _ensure_native_contiguous(arr: np.ndarray) -> np.ndarray:
    """Ensure array is C-contiguous, native byte order, float64 for SEP."""
    return np.ascontiguousarray(arr, dtype=np.float64)


class DetectStage(PipelineStage):
    name = "detect"

    def run(self) -> dict:
        proc_dir = self.workspace / "processed"
        cat_dir = self.workspace / "catalogs"
        seg_dir = self.workspace / "segmentation"
        cat_dir.mkdir(parents=True, exist_ok=True)
        seg_dir.mkdir(parents=True, exist_ok=True)

        detection_path = proc_dir / "detection.fits"

        # ----- Step 1: Read detection image -----
        self.progress(0.0, "Loading detection image...")
        self.check_cancel()

        if not detection_path.exists():
            raise FileNotFoundError(
                f"Detection image not found: {detection_path}. "
                "Run the preprocess stage first."
            )

        with fits.open(detection_path) as hdul:
            data = hdul[0].data.copy()
            wcs = WCS(hdul[0].header)
            n_filters = hdul[0].header.get("NFILTERS", 1)
            filters_str = hdul[0].header.get("FILTERS", "")

            if "RMS" in hdul:
                rms = hdul["RMS"].data.copy()
            else:
                rms = None

        logger.info(
            "Detection image: %d x %d, %d filters (%s)",
            data.shape[1], data.shape[0], n_filters, filters_str,
        )
        self.progress(0.05, f"Loaded {data.shape[1]}x{data.shape[0]} detection image")

        # ----- Step 2: Prepare data for SEP -----
        self.progress(0.05, "Preparing data for source extraction...")
        self.check_cancel()

        # Replace NaN/inf with 0
        nan_mask = ~np.isfinite(data)
        data[nan_mask] = 0.0
        if rms is not None:
            rms[nan_mask] = 0.0

        data = _ensure_native_contiguous(data)
        if rms is not None:
            rms = _ensure_native_contiguous(rms)

        # Run sep.Background for any residual global offset
        try:
            bkg = sep.Background(data)
            data_sub = data - bkg.back()
            logger.info("SEP residual background: global=%.4e, rms=%.4e", bkg.globalback, bkg.globalrms)
        except Exception as e:
            logger.warning("SEP background estimation failed (%s), using data as-is", e)
            data_sub = data

        data_sub = _ensure_native_contiguous(data_sub)
        self.progress(0.10, "Data prepared for extraction")

        # ----- Step 3: Extract sources -----
        self.progress(0.10, "Extracting sources...")
        self.check_cancel()

        cfg = self.config
        thresh = float(cfg.get("detection_threshold", 1.5))
        min_area = int(cfg.get("min_area", 5))
        deblend_nthresh = int(cfg.get("deblend_nthresh", 32))
        deblend_cont = float(cfg.get("deblend_cont", 0.005))

        # Build error array for extraction
        if rms is not None and np.any(rms > 0):
            err = rms.copy()
            err[err <= 0] = np.nanmedian(rms[rms > 0]) if np.any(rms > 0) else 1.0
        else:
            # Use global RMS from sep background
            try:
                err = bkg.rms()
            except Exception:
                err = np.ones_like(data_sub)
                logger.warning("No RMS available, using uniform error=1")

        err = _ensure_native_contiguous(err)

        # Increase pixel stack for large JWST mosaics (default 300k is too small)
        sep.set_extract_pixstack(50_000_000)

        # Increase sub-object limit for aggressive deblending in dense cluster fields
        sub_obj_limit = int(cfg.get("sub_object_limit", 32768))
        sep.set_sub_object_limit(sub_obj_limit)

        try:
            objects, segmap = sep.extract(
                data_sub,
                thresh=thresh,
                err=err,
                minarea=min_area,
                deblend_nthresh=deblend_nthresh,
                deblend_cont=deblend_cont,
                segmentation_map=True,
            )
        except Exception as e:
            logger.error("SEP extraction failed: %s", e)
            # Try with larger pixstack, higher sub-object limit, and more lenient parameters
            sep.set_extract_pixstack(100_000_000)
            sep.set_sub_object_limit(131072)
            logger.info("Retrying extraction with higher threshold, larger pixstack, and higher sub-object limit...")
            objects, segmap = sep.extract(
                data_sub,
                thresh=thresh * 2,
                err=err,
                minarea=min_area * 2,
                deblend_nthresh=16,
                deblend_cont=0.01,
                segmentation_map=True,
            )

        n_raw = len(objects)
        logger.info("SEP extracted %d raw sources", n_raw)
        self.progress(0.60, f"Extracted {n_raw} raw sources")

        # Handle zero sources
        if n_raw == 0:
            self.progress(0.75, "No sources detected — writing empty outputs")
            empty_df = pl.DataFrame({
                "id": pl.Series([], dtype=pl.Int64),
                "x": pl.Series([], dtype=pl.Float64),
                "y": pl.Series([], dtype=pl.Float64),
                "ra": pl.Series([], dtype=pl.Float64),
                "dec": pl.Series([], dtype=pl.Float64),
                "flux": pl.Series([], dtype=pl.Float64),
                "fluxerr": pl.Series([], dtype=pl.Float64),
                "a": pl.Series([], dtype=pl.Float64),
                "b": pl.Series([], dtype=pl.Float64),
                "theta": pl.Series([], dtype=pl.Float64),
                "flags": pl.Series([], dtype=pl.Int32),
                "npix": pl.Series([], dtype=pl.Int64),
                "peak": pl.Series([], dtype=pl.Float64),
            })

            cat_path = cat_dir / "detection_catalog.parquet"
            seg_path = seg_dir / "segmentation_map.fits"
            write_catalog(empty_df, cat_path)

            seg_hdu = fits.PrimaryHDU(data=segmap.astype(np.int32), header=wcs.to_header())
            seg_hdu.writeto(seg_path, overwrite=True)

            self.progress(1.0, "Detection complete — 0 sources")
            return {
                "sources_detected": 0,
                "sources_clean": 0,
                "catalog_path": str(cat_path),
                "segmentation_path": str(seg_path),
                "flux_stats": {},
            }

        # ----- Step 4: Build catalog -----
        self.progress(0.60, "Building source catalog...")
        self.check_cancel()

        # Pixel coordinates (SEP uses 0-indexed center of pixel)
        x = objects["x"].astype(np.float64)
        y = objects["y"].astype(np.float64)

        # Convert pixel to sky coordinates
        try:
            ra, dec = wcs.pixel_to_world_values(x, y)
        except Exception as e:
            logger.warning("WCS pixel_to_world failed (%s), using pixel coords", e)
            ra = x.copy()
            dec = y.copy()

        # Extract flux and shape parameters
        flux = objects["flux"].astype(np.float64)

        # Compute flux error via fixed-aperture photometry (r=3px).
        # Kron radii can be inf/NaN for edge sources; a fixed aperture is
        # robust and sufficient for the detection catalog.
        _, fluxerr, _ = sep.sum_circle(
            data_sub, x, y, 3.0, err=err, subpix=1,
        )

        a = objects["a"].astype(np.float64)
        b = objects["b"].astype(np.float64)
        theta = objects["theta"].astype(np.float64)
        flags = objects["flag"].astype(np.int32)
        npix = objects["npix"].astype(np.int64)
        peak = objects["peak"].astype(np.float64)

        # Sanitize: replace inf/NaN in morphology and flux columns
        for arr in (flux, fluxerr, a, b, theta, peak):
            arr[~np.isfinite(arr)] = 0.0

        # Create sequential IDs
        ids = np.arange(1, n_raw + 1, dtype=np.int64)

        catalog = pl.DataFrame({
            "id": ids,
            "x": x,
            "y": y,
            "ra": ra,
            "dec": dec,
            "flux": flux,
            "fluxerr": fluxerr,
            "a": a,
            "b": b,
            "theta": theta,
            "flags": flags,
            "npix": npix,
            "peak": peak,
        })

        # Count clean sources (flag == 0)
        n_clean = int(catalog.filter(pl.col("flags") == 0).height)
        logger.info("Catalog: %d total, %d clean (flag==0)", n_raw, n_clean)
        self.progress(0.72, f"Catalog built: {n_raw} sources ({n_clean} clean)")

        # ----- Step 4a: Split blended sources -----
        # Find segments with high axis-ratio that likely contain multiple
        # objects which SEP's deblender failed to separate.  Use watershed
        # on the detection image to split them into sub-sources.
        n_split_added = 0
        if cfg.get("enable_blend_splitting", True):
            self.progress(0.72, "Splitting blended sources...")
            try:
                from scipy.ndimage import maximum_filter
                from skimage.segmentation import watershed

                min_axis_ratio = float(cfg.get("blend_min_axis_ratio", 3.0))
                min_npix_blend = int(cfg.get("blend_min_npix", 100))
                peak_sep = int(cfg.get("blend_peak_separation", 7))
                min_peak_frac = float(cfg.get("blend_min_peak_fraction", 0.05))

                blend_indices = []
                for i in range(n_raw):
                    bi = b[i] if b[i] > 0.1 else 0.1
                    if a[i] / bi >= min_axis_ratio and npix[i] >= min_npix_blend:
                        blend_indices.append(i)

                logger.info("Blend splitting: %d candidates (a/b>=%.1f, npix>=%d)",
                            len(blend_indices), min_axis_ratio, min_npix_blend)

                rows_to_drop = set()
                new_rows = []  # list of dicts for new sub-sources

                err_median = float(np.nanmedian(err[err > 0])) if np.any(err > 0) else 0.001
                ny_full, nx_full = data_sub.shape

                for bi_idx in blend_indices:
                    seg_label = bi_idx + 1

                    # Use SEP bounding box to narrow search (avoids full-image scan)
                    pad = 2
                    by0 = max(0, int(objects['ymin'][bi_idx]) - pad)
                    by1 = min(ny_full, int(objects['ymax'][bi_idx]) + pad + 1)
                    bx0 = max(0, int(objects['xmin'][bi_idx]) - pad)
                    bx1 = min(nx_full, int(objects['xmax'][bi_idx]) + pad + 1)
                    local_region = segmap[by0:by1, bx0:bx1]
                    local_ys, local_xs = np.where(local_region == seg_label)
                    if len(local_ys) == 0:
                        continue
                    local_seg = segmap[by0:by1, bx0:bx1]
                    local_data = data_sub[by0:by1, bx0:bx1]
                    local_mask = local_seg == seg_label
                    local_masked = np.where(local_mask, local_data, 0.0)

                    # Find local peaks within the segment
                    local_max = maximum_filter(local_masked, size=2 * peak_sep + 1)
                    seg_peak_max = local_masked.max()
                    if seg_peak_max <= 0:
                        continue
                    peak_thresh = max(seg_peak_max * min_peak_frac, thresh * err_median)
                    peaks_mask = (local_masked == local_max) & (local_masked > peak_thresh)
                    pk_ly, pk_lx = np.where(peaks_mask)

                    if len(pk_ly) < 2:
                        continue  # single peak — nothing to split

                    # Keep only top 10 peaks to avoid noise
                    pk_vals = local_masked[pk_ly, pk_lx]
                    order = np.argsort(-pk_vals)[:10]
                    pk_ly, pk_lx = pk_ly[order], pk_lx[order]

                    # Watershed split on local bbox
                    markers = np.zeros_like(local_seg, dtype=np.int32)
                    for j in range(len(pk_lx)):
                        markers[pk_ly[j], pk_lx[j]] = j + 1

                    ws = watershed(-local_masked, markers, mask=local_mask)
                    sub_labels = sorted(set(ws[ws > 0].flat))

                    if len(sub_labels) < 2:
                        continue

                    # Replace the original source with sub-sources
                    rows_to_drop.add(bi_idx)

                    for sub_id in sub_labels:
                        sub_mask = ws == sub_id
                        sub_npix = int(np.sum(sub_mask))
                        if sub_npix < min_area:
                            continue

                        sub_data = local_data[sub_mask]
                        weights = np.maximum(sub_data, 0)
                        wsum = weights.sum()
                        sub_ly, sub_lx = np.where(sub_mask)
                        # Convert local coords to global
                        sub_gx = sub_lx.astype(np.float64) + bx0
                        sub_gy = sub_ly.astype(np.float64) + by0
                        if wsum > 0:
                            cx_new = float(np.sum(sub_gx * weights) / wsum)
                            cy_new = float(np.sum(sub_gy * weights) / wsum)
                        else:
                            cx_new = float(sub_gx.mean())
                            cy_new = float(sub_gy.mean())

                        sub_peak = float(sub_data.max())
                        sub_flux = float(sub_data.sum())

                        # Compute shape moments
                        dx = sub_gx - cx_new
                        dy = sub_gy - cy_new
                        if wsum > 0:
                            x2 = np.sum(dx**2 * weights) / wsum
                            y2 = np.sum(dy**2 * weights) / wsum
                            xy = np.sum(dx * dy * weights) / wsum
                        else:
                            x2, y2, xy = np.var(dx), np.var(dy), 0.0
                        trace = x2 + y2
                        det_val = x2 * y2 - xy**2
                        disc = max(trace**2 / 4 - det_val, 0)
                        sub_a = float(np.sqrt(max(trace / 2 + np.sqrt(disc), 0.1)))
                        sub_b = float(np.sqrt(max(trace / 2 - np.sqrt(disc), 0.1)))
                        sub_theta = float(0.5 * np.arctan2(2 * xy, x2 - y2))

                        try:
                            ra_new, dec_new = wcs.pixel_to_world_values(cx_new, cy_new)
                        except Exception:
                            ra_new, dec_new = cx_new, cy_new

                        new_rows.append({
                            "x": cx_new, "y": cy_new,
                            "ra": float(ra_new), "dec": float(dec_new),
                            "flux": sub_flux, "fluxerr": 0.0,
                            "a": sub_a, "b": sub_b, "theta": sub_theta,
                            "flags": 0, "npix": sub_npix, "peak": sub_peak,
                        })

                    # Update segmentation map with new labels (use local view)
                    new_start = n_raw + n_split_added + 1
                    local_seg_view = segmap[by0:by1, bx0:bx1]
                    for j, sub_id in enumerate(sub_labels):
                        local_seg_view[ws == sub_id] = new_start + j
                    n_split_added += len(sub_labels)

                if new_rows:
                    # Remove original blended sources from catalog
                    keep_mask = [i not in rows_to_drop for i in range(catalog.height)]
                    catalog = catalog.filter(pl.Series(keep_mask))

                    # Assign IDs to new sub-sources
                    next_id = int(catalog["id"].max()) + 1 if catalog.height > 0 else 1
                    for row in new_rows:
                        row["id"] = next_id
                        next_id += 1

                    split_cat = pl.DataFrame(new_rows).cast({
                        "id": pl.Int64, "x": pl.Float64, "y": pl.Float64,
                        "ra": pl.Float64, "dec": pl.Float64,
                        "flux": pl.Float64, "fluxerr": pl.Float64,
                        "a": pl.Float64, "b": pl.Float64, "theta": pl.Float64,
                        "flags": pl.Int32, "npix": pl.Int64, "peak": pl.Float64,
                    })
                    catalog = pl.concat([catalog, split_cat], how="diagonal")
                    n_raw = catalog.height

                    logger.info("Blend splitting: removed %d blends, added %d sub-sources",
                                len(rows_to_drop), len(new_rows))

            except ImportError:
                logger.warning("scikit-image not installed — skipping blend splitting")
            except Exception as e:
                logger.warning("Blend splitting failed: %s", e)

        # Sync n_raw with actual catalog height after any modifications
        n_raw = catalog.height
        self.progress(0.72, f"Catalog: {n_raw} sources ({n_split_added} from splits)")

        # ----- Step 4b: Low Surface Brightness detection pass -----
        n_lsb = 0
        if cfg.get("enable_lsb_detection", True):
            self.progress(0.73, "LSB detection pass...")
            self.check_cancel()
            try:
                from scipy.ndimage import gaussian_filter

                lsb_sigma = float(cfg.get("lsb_smooth_sigma", 5.0))
                lsb_thresh = float(cfg.get("lsb_threshold", 0.8))
                lsb_minarea = int(cfg.get("lsb_min_area", 50))

                # Smooth detection image with Gaussian kernel
                smooth_data = gaussian_filter(data_sub, sigma=lsb_sigma)
                smooth_data = _ensure_native_contiguous(smooth_data)

                # Mask already-detected sources to avoid duplicates
                smooth_data[segmap > 0] = 0.0

                lsb_objects, lsb_segmap = sep.extract(
                    smooth_data, thresh=lsb_thresh, err=err,
                    minarea=lsb_minarea, deblend_nthresh=16,
                    deblend_cont=0.01, segmentation_map=True,
                )
                n_lsb = len(lsb_objects)
                logger.info("LSB pass: %d additional sources (sigma=%.1f, thresh=%.1f, minarea=%d)",
                            n_lsb, lsb_sigma, lsb_thresh, lsb_minarea)

                if n_lsb > 0:
                    lsb_ids = np.arange(n_raw + 1, n_raw + n_lsb + 1, dtype=np.int64)
                    lsb_x = lsb_objects["x"].astype(np.float64)
                    lsb_y = lsb_objects["y"].astype(np.float64)

                    # Refine LSB centroids on the original (unsmoothed) image.
                    # SEP centroids from the smoothed+masked image can be
                    # offset by 5-25+ pixels from the actual source.
                    ny_ds, nx_ds = data_sub.shape
                    refine_r = int(lsb_sigma * 3)  # search radius
                    for i in range(n_lsb):
                        ix, iy = int(lsb_x[i]), int(lsb_y[i])
                        rx0 = max(0, ix - refine_r)
                        rx1 = min(nx_ds, ix + refine_r + 1)
                        ry0 = max(0, iy - refine_r)
                        ry1 = min(ny_ds, iy + refine_r + 1)
                        patch = data_sub[ry0:ry1, rx0:rx1]
                        valid = np.isfinite(patch) & (patch > 0)
                        if np.any(valid):
                            # Intensity-weighted centroid on original image
                            wy, wx = np.where(valid)
                            weights = patch[valid]
                            wsum = weights.sum()
                            if wsum > 0:
                                cx_local = np.sum(wx * weights) / wsum
                                cy_local = np.sum(wy * weights) / wsum
                                lsb_x[i] = cx_local + rx0
                                lsb_y[i] = cy_local + ry0

                    try:
                        lsb_ra, lsb_dec = wcs.pixel_to_world_values(lsb_x, lsb_y)
                    except Exception:
                        lsb_ra, lsb_dec = lsb_x.copy(), lsb_y.copy()

                    lsb_flux = lsb_objects["flux"].astype(np.float64)
                    lsb_a = lsb_objects["a"].astype(np.float64)
                    lsb_b = lsb_objects["b"].astype(np.float64)
                    lsb_theta = lsb_objects["theta"].astype(np.float64)
                    lsb_flags = lsb_objects["flag"].astype(np.int32)
                    lsb_npix = lsb_objects["npix"].astype(np.int64)
                    lsb_peak = lsb_objects["peak"].astype(np.float64)
                    for arr in (lsb_flux, lsb_a, lsb_b, lsb_theta, lsb_peak):
                        arr[~np.isfinite(arr)] = 0.0

                    lsb_cat = pl.DataFrame({
                        "id": lsb_ids,
                        "x": lsb_x, "y": lsb_y,
                        "ra": lsb_ra, "dec": lsb_dec,
                        "flux": lsb_flux,
                        "fluxerr": np.zeros(n_lsb, dtype=np.float64),
                        "a": lsb_a, "b": lsb_b,
                        "theta": lsb_theta,
                        "flags": lsb_flags,
                        "npix": lsb_npix,
                        "peak": lsb_peak,
                        "is_lsb": np.ones(n_lsb, dtype=bool),
                    })

                    # Add is_lsb=False to main catalog
                    catalog = catalog.with_columns(
                        pl.Series("is_lsb", np.zeros(n_raw, dtype=bool))
                    )
                    catalog = pl.concat([catalog, lsb_cat], how="diagonal")

                    # Merge segmentation maps (offset LSB IDs)
                    lsb_segmap[lsb_segmap > 0] += n_raw
                    segmap = np.where(lsb_segmap > 0, lsb_segmap, segmap)
                    n_raw += n_lsb

                    del smooth_data
                else:
                    # Add is_lsb column even if no LSB sources found
                    catalog = catalog.with_columns(
                        pl.Series("is_lsb", np.zeros(catalog.height, dtype=bool))
                    )
            except Exception as e:
                logger.warning("LSB detection pass failed: %s", e)
                if "is_lsb" not in catalog.columns:
                    catalog = catalog.with_columns(
                        pl.Series("is_lsb", np.zeros(catalog.height, dtype=bool))
                    )
        else:
            catalog = catalog.with_columns(
                pl.Series("is_lsb", np.zeros(catalog.height, dtype=bool))
            )

        self.progress(0.75, f"Total: {n_raw} sources ({n_lsb} LSB)")

        # ----- Step 5: Write outputs -----
        self.progress(0.75, "Writing catalog and segmentation map...")
        self.check_cancel()

        cat_path = cat_dir / "detection_catalog.parquet"
        write_catalog(catalog, cat_path)
        logger.info("Wrote catalog: %s (%d sources)", cat_path.name, n_raw)

        seg_path = seg_dir / "segmentation_map.fits"
        seg_hdr = wcs.to_header()
        seg_hdr["NSOURCES"] = n_raw
        seg_hdu = fits.PrimaryHDU(data=segmap.astype(np.int32), header=seg_hdr)
        seg_hdu.writeto(seg_path, overwrite=True)
        logger.info("Wrote segmentation map: %s", seg_path.name)

        self.progress(0.90, "Computing statistics...")

        # Compute flux statistics
        valid_flux = flux[np.isfinite(flux) & (flux > 0)]
        if len(valid_flux) > 0:
            flux_stats = {
                "median": float(np.median(valid_flux)),
                "mean": float(np.mean(valid_flux)),
                "std": float(np.std(valid_flux)),
                "min": float(np.min(valid_flux)),
                "max": float(np.max(valid_flux)),
                "n_positive": int(len(valid_flux)),
            }
        else:
            flux_stats = {}

        # Verify outputs
        assert cat_path.exists(), "Catalog file not written"
        assert seg_path.exists(), "Segmentation map not written"

        self.progress(1.0, f"Detected {n_raw} sources ({n_clean} clean)")

        return {
            "sources_detected": n_raw,
            "sources_clean": n_clean,
            "sources_lsb": n_lsb,
            "catalog_path": str(cat_path),
            "segmentation_path": str(seg_path),
            "flux_stats": flux_stats,
        }
