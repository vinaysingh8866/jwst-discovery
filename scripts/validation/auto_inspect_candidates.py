#!/usr/bin/env python3
"""Automated image analysis of JWST discovery candidate cutouts.

Loads RGB + per-filter cutout PNGs for each candidate and runs automated
morphological/photometric checks to classify sources as real, noise,
artifact, fragment, edge, or crowded.

Usage:
    python scripts/auto_inspect_candidates.py                     # All EXCELLENT+PROMISING
    python scripts/auto_inspect_candidates.py --field El-Gordo    # Single field
    python scripts/auto_inspect_candidates.py --min-z 5           # High-z only
    python scripts/auto_inspect_candidates.py --all               # All novel sources
    python scripts/auto_inspect_candidates.py --save report.txt   # Save text report
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import polars as pl
from PIL import Image

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WORKSPACES_DIR = Path.home() / ".jwst-discovery" / "workspaces"
OUTPUT_LOG = Path.home() / ".jwst-discovery" / "auto_inspection_log.json"

PIVOT_WAVELENGTH_UM = {
    "F070W": 0.704, "F090W": 0.901, "F115W": 1.154, "F140M": 1.405,
    "F150W": 1.501, "F162M": 1.627, "F182M": 1.845, "F200W": 1.989,
    "F210M": 2.093, "F250M": 2.503, "F277W": 2.762, "F300M": 2.996,
    "F335M": 3.365, "F356W": 3.568, "F360M": 3.621, "F410M": 4.092,
    "F430M": 4.280, "F444W": 4.421, "F460M": 4.624, "F480M": 4.834,
}

LYMAN_ALPHA_UM = 0.1216


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ImageCheckResult:
    """Result of one image-based check."""
    name: str
    status: str  # PASS, WARN, FAIL
    detail: str
    value: float = 0.0


@dataclass
class CandidateInspection:
    """Full inspection result for one candidate."""
    field: str
    src_id: int
    ra: float
    dec: float
    z_phot: float
    max_sn: float
    object_class: str
    scrutiny_verdict: str
    classification: str  # REAL_LIKELY, NOISE, ARTIFACT, FRAGMENT, EDGE, CROWDED, UNCERTAIN
    checks: list = field(default_factory=list)
    n_pass: int = 0
    n_warn: int = 0
    n_fail: int = 0
    rgb_exists: bool = False
    sed_exists: bool = False
    n_filter_cutouts: int = 0
    notes: str = ""


# ---------------------------------------------------------------------------
# Image analysis helpers
# ---------------------------------------------------------------------------

def load_cutout_as_array(path):
    """Load a PNG cutout and return as grayscale float array."""
    try:
        img = Image.open(path)
        arr = np.array(img.convert("L"), dtype=np.float64)
        return arr
    except Exception:
        return None


def load_cutout_rgba(path):
    """Load a PNG cutout and return RGBA array."""
    try:
        img = Image.open(path)
        return np.array(img.convert("RGBA"), dtype=np.float64)
    except Exception:
        return None


def measure_source_visibility(arr):
    """Measure peak signal vs background in cutout.

    Returns (peak_to_bg_ratio, central_signal, bg_signal).
    Center is the middle 25% of the image; background is outer 25%.
    """
    if arr is None:
        return 0.0, 0.0, 0.0

    h, w = arr.shape
    ch, cw = h // 2, w // 2
    # Central region: inner 25%
    r = max(3, min(h, w) // 8)
    center = arr[ch - r:ch + r + 1, cw - r:cw + r + 1]

    # Background: outer ring (outside 75% radius)
    y, x = np.ogrid[:h, :w]
    dist = np.sqrt((y - ch) ** 2 + (x - cw) ** 2)
    outer_r = min(h, w) * 0.38
    bg_mask = dist > outer_r
    bg_pixels = arr[bg_mask]

    if len(bg_pixels) == 0 or center.size == 0:
        return 0.0, 0.0, 0.0

    central_signal = np.mean(center)
    bg_signal = np.mean(bg_pixels)

    # For inverted (gray_r) images, source is DARKER than background
    # Use absolute difference
    contrast = abs(central_signal - bg_signal)
    bg_std = max(np.std(bg_pixels), 1e-6)
    ratio = contrast / bg_std

    return ratio, central_signal, bg_signal


def measure_symmetry(arr):
    """Measure asymmetry of central source in 4 quadrants.

    Returns asymmetry ratio (1.0 = perfectly symmetric).
    """
    if arr is None:
        return 1.0

    h, w = arr.shape
    ch, cw = h // 2, w // 2
    r = max(3, min(h, w) // 6)

    # Extract central region
    region = arr[ch - r:ch + r, cw - r:cw + r]
    if region.size == 0:
        return 1.0

    rh, rw = region.shape
    mh, mw = rh // 2, rw // 2

    # 4 quadrants
    q1 = np.mean(region[:mh, :mw])
    q2 = np.mean(region[:mh, mw:])
    q3 = np.mean(region[mh:, :mw])
    q4 = np.mean(region[mh:, mw:])

    quads = [q1, q2, q3, q4]
    if min(quads) < 1e-6:
        return 10.0  # extremely asymmetric
    return max(quads) / min(quads)


def measure_concentration(arr):
    """Measure concentration: ratio of flux in inner vs outer aperture.

    High concentration = point source; low = extended.
    """
    if arr is None:
        return 0.0

    h, w = arr.shape
    ch, cw = h // 2, w // 2
    y, x = np.ogrid[:h, :w]
    dist = np.sqrt((y - ch) ** 2 + (x - cw) ** 2)

    # Inner: r < 15% of image; outer: 15% < r < 35%
    r_in = min(h, w) * 0.15
    r_out = min(h, w) * 0.35

    inner = arr[dist < r_in]
    outer = arr[(dist >= r_in) & (dist < r_out)]

    if len(inner) == 0 or len(outer) == 0:
        return 0.0

    # For gray_r images, source is darker. Use absolute deviation from median
    med = np.median(arr)
    inner_signal = np.mean(np.abs(inner - med))
    outer_signal = np.mean(np.abs(outer - med))

    if outer_signal < 1e-6:
        return 100.0 if inner_signal > 1e-6 else 0.0
    return inner_signal / outer_signal


def count_edge_pixels(rgba_arr):
    """Count fraction of masked pixels (no-data regions) in cutout.

    Uses RGBA: truly masked pixels are black (R+G+B~0) or transparent (alpha~0).
    For grayscale-only images (detection cutouts), falls back to near-black count.
    """
    if rgba_arr is None:
        return 1.0
    if rgba_arr.ndim < 3:
        # Grayscale array — near-black = masked (for gray_r, background is white)
        total = rgba_arr.size
        if total == 0:
            return 1.0
        return np.sum(rgba_arr < 2) / total

    h, w = rgba_arr.shape[:2]
    total = h * w
    if total == 0:
        return 1.0

    r, g, b = rgba_arr[:, :, 0], rgba_arr[:, :, 1], rgba_arr[:, :, 2]
    # Truly black pixels (no data in RGB composite)
    black_mask = (r + g + b) < 10
    # Also check alpha if 4 channels
    if rgba_arr.shape[2] >= 4:
        alpha = rgba_arr[:, :, 3]
        masked = black_mask | (alpha < 10)
    else:
        masked = black_mask
    return np.sum(masked) / total


def count_red_markers(rgba_arr):
    """Count red cross markers (other cataloged sources) in RGBA cutout.

    Red markers have high R, low G, low B.
    """
    if rgba_arr is None:
        return 0

    r, g, b = rgba_arr[:, :, 0], rgba_arr[:, :, 1], rgba_arr[:, :, 2]
    # Red pixels: R > 180, G < 80, B < 80
    red_mask = (r > 180) & (g < 80) & (b < 80)
    n_red = np.sum(red_mask)

    # Each cross marker is roughly 10-15 pixels
    return max(0, n_red // 10)


def count_red_near_center(rgba_arr, radius=10):
    """Count red markers within `radius` pixels of center."""
    if rgba_arr is None:
        return 0

    h, w = rgba_arr.shape[:2]
    ch, cw = h // 2, w // 2
    r0 = max(0, ch - radius)
    r1 = min(h, ch + radius + 1)
    c0 = max(0, cw - radius)
    c1 = min(w, cw + radius + 1)

    region = rgba_arr[r0:r1, c0:c1]
    r, g, b = region[:, :, 0], region[:, :, 1], region[:, :, 2]
    red_mask = (r > 180) & (g < 80) & (b < 80)
    n_red = np.sum(red_mask)
    return max(0, n_red // 8)


def check_multiband_visibility(cutout_dir, src_id, filters, z_phot):
    """Check how many filter cutouts show a visible source.

    Returns (n_visible, n_expected_visible, n_total_cutouts, visible_filters).
    """
    visible_filters = []
    total_cutouts = 0

    # Lyman break wavelength
    lyman_obs = LYMAN_ALPHA_UM * (1 + z_phot) if z_phot > 0 else 0

    for filt in filters:
        path = cutout_dir / f"src{src_id:06d}_{filt}.png"
        if not path.exists():
            continue
        total_cutouts += 1

        arr = load_cutout_as_array(path)
        if arr is None:
            continue

        ratio, _, _ = measure_source_visibility(arr)
        if ratio > 2.0:
            visible_filters.append(filt)

    # Expected visible: filters redward of Lyman break (for high-z sources)
    n_expected = 0
    if z_phot > 3:
        for filt in filters:
            wl = PIVOT_WAVELENGTH_UM.get(filt, 0)
            if wl > lyman_obs:
                n_expected += 1
    else:
        n_expected = len(filters)  # low-z, all should be visible

    return len(visible_filters), n_expected, total_cutouts, visible_filters


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def check_source_visible(rgb_arr, max_sn, n_bands_sn5):
    """Check 1: Is source reliably detected? Uses catalog S/N as primary,
    image contrast as secondary."""
    img_ratio, _, _ = measure_source_visibility(rgb_arr)

    # Primary: catalog S/N
    if max_sn < 3:
        return ImageCheckResult("Source visibility", "FAIL",
                                f"Very low S/N={max_sn:.1f}, image contrast={img_ratio:.1f}σ", max_sn)
    elif max_sn < 5:
        return ImageCheckResult("Source visibility", "WARN",
                                f"Low S/N={max_sn:.1f}, image contrast={img_ratio:.1f}σ", max_sn)
    elif n_bands_sn5 < 2 and max_sn < 10:
        return ImageCheckResult("Source visibility", "WARN",
                                f"S/N={max_sn:.1f} but only {n_bands_sn5} bands at S/N>5", max_sn)
    return ImageCheckResult("Source visibility", "PASS",
                            f"S/N={max_sn:.1f}, {n_bands_sn5} bands at S/N>5, image contrast={img_ratio:.1f}σ",
                            max_sn)


def check_point_vs_extended(rgb_arr):
    """Check 2: Point source vs extended?"""
    if rgb_arr is None:
        return ImageCheckResult("Morphology type", "WARN",
                                "No cutout image available", 0.0)
    conc = measure_concentration(rgb_arr)
    if conc > 8.0:
        return ImageCheckResult("Morphology type", "WARN",
                                f"Very compact/point-like: concentration={conc:.1f}", conc)
    elif conc < 0.5:
        return ImageCheckResult("Morphology type", "WARN",
                                f"Very diffuse/extended: concentration={conc:.1f}", conc)
    return ImageCheckResult("Morphology type", "PASS",
                            f"Extended source: concentration={conc:.1f}", conc)


def check_symmetry(rgb_arr):
    """Check 3: Is the source symmetric?"""
    if rgb_arr is None:
        return ImageCheckResult("Symmetry", "WARN",
                                "No cutout image available", 0.0)
    asym = measure_symmetry(rgb_arr)
    if asym > 5.0:
        return ImageCheckResult("Symmetry", "FAIL",
                                f"Extreme asymmetry={asym:.1f} (spike/artifact?)", asym)
    elif asym > 3.0:
        return ImageCheckResult("Symmetry", "WARN",
                                f"Moderate asymmetry={asym:.1f}", asym)
    return ImageCheckResult("Symmetry", "PASS",
                            f"Symmetric: ratio={asym:.1f}", asym)


def check_edge_masking(rgba_arr):
    """Check 4: Is the source near mosaic edge (many masked pixels)?"""
    if rgba_arr is None:
        return ImageCheckResult("Edge masking", "WARN",
                                "No cutout image available", 0.0)
    frac = count_edge_pixels(rgba_arr)
    if frac > 0.3:
        return ImageCheckResult("Edge masking", "FAIL",
                                f"{frac:.0%} pixels masked (edge)", frac)
    elif frac > 0.15:
        return ImageCheckResult("Edge masking", "WARN",
                                f"{frac:.0%} pixels masked", frac)
    return ImageCheckResult("Edge masking", "PASS",
                            f"{frac:.0%} pixels masked", frac)


def check_multiband(cutout_dir, src_id, filters, z_phot):
    """Check 5: Is source visible in multiple bands?"""
    n_vis, n_exp, n_total, vis_filts = check_multiband_visibility(
        cutout_dir, src_id, filters, z_phot)

    if n_total == 0:
        return ImageCheckResult("Multi-band", "FAIL",
                                "No filter cutouts found", 0)
    if n_vis == 0:
        return ImageCheckResult("Multi-band", "FAIL",
                                f"Source not visible in any of {n_total} bands", 0)
    if n_vis == 1:
        return ImageCheckResult("Multi-band", "WARN",
                                f"Only visible in 1/{n_total} bands: {vis_filts[0]}", n_vis)
    return ImageCheckResult("Multi-band", "PASS",
                            f"Visible in {n_vis}/{n_total} bands: {', '.join(vis_filts)}", n_vis)


def check_neighbor_contamination(rgba_arr):
    """Check 6: Are there other sources very close to center?"""
    if rgba_arr is None:
        return ImageCheckResult("Neighbor contamination", "WARN",
                                "No cutout image available", 0)
    n_total = count_red_markers(rgba_arr)
    n_near = count_red_near_center(rgba_arr, radius=12)

    if n_near >= 2:
        return ImageCheckResult("Neighbor contamination", "FAIL",
                                f"{n_near} neighbors within 12px of center ({n_total} total in cutout)",
                                n_near)
    elif n_near == 1:
        return ImageCheckResult("Neighbor contamination", "WARN",
                                f"1 neighbor within 12px ({n_total} total in cutout)", n_near)
    elif n_total > 5:
        return ImageCheckResult("Neighbor contamination", "WARN",
                                f"Crowded field: {n_total} neighbors in cutout", n_total)
    return ImageCheckResult("Neighbor contamination", "PASS",
                            f"{n_total} neighbors in cutout, none near center", n_total)


def check_sed_shape(row, filters):
    """Check 7: SED shape analysis from catalog photometry."""
    # Extract fluxes and S/N per band
    fluxes = []
    for filt in filters:
        sn_col = f"sn_{filt}"
        flux_col = f"flux_{filt}"
        sn = row.get(sn_col, 0) or 0
        flux = row.get(flux_col, 0) or 0
        if np.isfinite(sn) and np.isfinite(flux) and sn > 2:
            fluxes.append((PIVOT_WAVELENGTH_UM[filt], flux, sn, filt))

    if len(fluxes) < 2:
        return ImageCheckResult("SED shape", "WARN",
                                f"Only {len(fluxes)} bands with S/N>2", len(fluxes))

    fluxes.sort(key=lambda x: x[0])
    flux_vals = [f[1] for f in fluxes]

    # Check for V-shape: a dip in the middle
    if len(flux_vals) >= 3:
        max_flux = max(flux_vals)
        min_flux = min(flux_vals)
        if min_flux > 0 and max_flux / min_flux > 1:
            # Check if minimum is interior (V-shape)
            min_idx = flux_vals.index(min_flux)
            if 0 < min_idx < len(flux_vals) - 1:
                left = flux_vals[min_idx - 1]
                right = flux_vals[min_idx + 1]
                dip = min(left, right) / max(min_flux, 1e-30)
                if dip > 5:
                    return ImageCheckResult("SED shape", "FAIL",
                                            f"V-shaped SED: {dip:.1f}x dip at {fluxes[min_idx][3]}",
                                            dip)
                elif dip > 2:
                    return ImageCheckResult("SED shape", "WARN",
                                            f"Possible V-shape: {dip:.1f}x dip at {fluxes[min_idx][3]}",
                                            dip)

    # Check for single-band spike
    if len(flux_vals) >= 3:
        max_val = max(flux_vals)
        median_val = np.median(flux_vals)
        if median_val > 0 and max_val / median_val > 8:
            spike_filt = fluxes[flux_vals.index(max_val)][3]
            return ImageCheckResult("SED shape", "FAIL",
                                    f"Single-band spike in {spike_filt}: {max_val/median_val:.1f}x median",
                                    max_val / median_val)

    return ImageCheckResult("SED shape", "PASS",
                            f"Reasonable SED across {len(fluxes)} bands", len(fluxes))


def check_cutout_existence(cutout_dir, sed_dir, src_id, filters):
    """Check 8: Do the expected files exist?"""
    rgb = cutout_dir / f"src{src_id:06d}_rgb.png"
    sed = sed_dir / f"src{src_id:06d}_sed.png"
    det = cutout_dir / f"src{src_id:06d}_detection.png"

    n_filter = sum(1 for f in filters if (cutout_dir / f"src{src_id:06d}_{f}.png").exists())

    missing = []
    if not rgb.exists():
        missing.append("RGB")
    if not sed.exists():
        missing.append("SED")
    if not det.exists():
        missing.append("detection")

    if len(missing) >= 2:
        return ImageCheckResult("File existence", "FAIL",
                                f"Missing: {', '.join(missing)}, {n_filter} filter cutouts", n_filter)
    elif len(missing) == 1:
        return ImageCheckResult("File existence", "WARN",
                                f"Missing: {', '.join(missing)}, {n_filter} filter cutouts", n_filter)
    return ImageCheckResult("File existence", "PASS",
                            f"RGB + SED + detection + {n_filter} filters", n_filter)


# ---------------------------------------------------------------------------
# Workspace & candidate discovery
# ---------------------------------------------------------------------------

def discover_workspaces():
    """Find all workspaces with final catalogs."""
    workspaces = {}
    for ws in sorted(WORKSPACES_DIR.iterdir()):
        cat = ws / "catalogs" / "final_catalog.parquet"
        if cat.exists():
            workspaces[ws.name] = ws
    return workspaces


def discover_filters(df):
    """Find available filters from column names."""
    filters = []
    for col in df.columns:
        if col.startswith("sn_F"):
            filt = col[3:]
            if filt in PIVOT_WAVELENGTH_UM:
                filters.append(filt)
    filters.sort(key=lambda f: PIVOT_WAVELENGTH_UM[f])
    return filters


def safe_val(row, col, default=0):
    """Safely get a value from a row dict."""
    v = row.get(col)
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return default
    return v


# ---------------------------------------------------------------------------
# Main inspection
# ---------------------------------------------------------------------------

def inspect_candidate(ws_path, row, filters, scrutiny_verdict):
    """Run all image checks on a single candidate."""
    field = ws_path.name
    src_id = int(row["id"])
    cutout_dir = ws_path / "report" / "cutouts"
    sed_dir = ws_path / "report" / "seds"

    z_phot = safe_val(row, "z_phot_eazy", -1)
    max_sn = safe_val(row, "max_sn", 0)
    obj_class = row.get("object_class", "unknown") or "unknown"

    result = CandidateInspection(
        field=field,
        src_id=src_id,
        ra=safe_val(row, "ra", 0),
        dec=safe_val(row, "dec", 0),
        z_phot=z_phot,
        max_sn=max_sn,
        object_class=obj_class,
        scrutiny_verdict=scrutiny_verdict,
        classification="UNCERTAIN",
    )

    # Load images — prefer detection cutout (more reliable) for morphology/edge
    # Use RGB for color checks if available
    rgb_path = cutout_dir / f"src{src_id:06d}_rgb.png"
    det_path = cutout_dir / f"src{src_id:06d}_detection.png"
    primary_path = det_path if det_path.exists() else rgb_path

    rgb_arr = load_cutout_as_array(primary_path) if primary_path.exists() else None
    rgba_arr = load_cutout_rgba(primary_path) if primary_path.exists() else None
    # For neighbor detection, prefer RGB (has red cross markers)
    rgb_rgba = load_cutout_rgba(rgb_path) if rgb_path.exists() else rgba_arr
    result.rgb_exists = rgb_path.exists() or det_path.exists()
    result.sed_exists = (sed_dir / f"src{src_id:06d}_sed.png").exists()

    # Count filter cutouts
    result.n_filter_cutouts = sum(
        1 for f in filters if (cutout_dir / f"src{src_id:06d}_{f}.png").exists()
    )

    n_bands_sn5 = int(safe_val(row, "n_bands_sn5", 0))

    # Run checks
    checks = [
        check_source_visible(rgb_arr, max_sn, n_bands_sn5),
        check_point_vs_extended(rgb_arr),
        check_symmetry(rgb_arr),
        check_edge_masking(rgba_arr),
        check_multiband(cutout_dir, src_id, filters, z_phot),
        check_neighbor_contamination(rgb_rgba),
        check_sed_shape(row, filters),
        check_cutout_existence(cutout_dir, sed_dir, src_id, filters),
    ]

    result.checks = checks
    result.n_pass = sum(1 for c in checks if c.status == "PASS")
    result.n_warn = sum(1 for c in checks if c.status == "WARN")
    result.n_fail = sum(1 for c in checks if c.status == "FAIL")

    # Classify based on check results
    result.classification = classify_candidate(result, checks)

    return result


def classify_candidate(result, checks):
    """Assign classification based on check results.

    Uses catalog-based checks (S/N, SED, multi-band) as primary.
    Image checks (morphology, symmetry, edge, neighbor) are secondary
    and only used when images are available.
    """
    status_map = {c.name: c for c in checks}

    vis = status_map.get("Source visibility")
    sym = status_map.get("Symmetry")
    edge = status_map.get("Edge masking")
    multi = status_map.get("Multi-band")
    neigh = status_map.get("Neighbor contamination")
    sed = status_map.get("SED shape")

    has_image = result.rgb_exists

    # --- Hard failures (catalog-based, always apply) ---

    # NOISE: very low S/N
    if result.max_sn < 5:
        return "NOISE"

    # NOISE: no multi-band detection and low S/N
    if multi and multi.status == "FAIL" and result.max_sn < 10:
        return "NOISE"

    # ARTIFACT: only visible in 1 band + SED spike (catalog-based)
    if multi and multi.status == "FAIL" and sed and sed.status == "FAIL":
        return "ARTIFACT"

    # --- Image-based failures (only when we have images) ---

    # EDGE: too many masked pixels
    if has_image and edge and edge.status == "FAIL":
        return "EDGE"

    # ARTIFACT: extreme asymmetry AND SED anomaly
    if has_image and sym and sym.status == "FAIL" and sed and sed.status in ("FAIL", "WARN"):
        return "ARTIFACT"

    # FRAGMENT: neighbor very close to center
    if has_image and neigh and neigh.status == "FAIL":
        return "FRAGMENT"

    # CROWDED: many neighbors in cutout
    if has_image and neigh and neigh.value > 5:
        return "CROWDED"

    # --- Count catalog-quality failures (not image-availability) ---
    # S/N and SED shape are catalog-quality checks
    quality_checks = [vis, sed]
    quality_fails = sum(1 for c in quality_checks if c and c.status == "FAIL")
    quality_warns = sum(1 for c in quality_checks if c and c.status == "WARN")

    # Multi-band and file existence depend on image availability — don't penalize
    # if images simply don't exist for this source
    if has_image:
        # Include image-based checks
        image_checks = [sym, edge, neigh, multi]
        image_fails = sum(1 for c in image_checks
                         if c and c.status == "FAIL"
                         and "No cutout" not in c.detail)
    else:
        image_fails = 0

    total_fails = quality_fails + image_fails

    if total_fails == 0 and quality_warns <= 1:
        return "REAL_LIKELY"
    elif total_fails == 0:
        return "UNCERTAIN"  # multiple quality warnings
    elif total_fails >= 2:
        return "NOISE" if result.max_sn < 15 else "UNCERTAIN"
    else:
        return "UNCERTAIN"


# ---------------------------------------------------------------------------
# Scrutiny verdict lookup (simplified from scrutinize_candidates.py)
# ---------------------------------------------------------------------------

def get_scrutiny_verdicts(df, filters):
    """Quick scrutiny verdict for each source (simplified).

    Returns dict of {src_id: verdict_str}.
    """
    verdicts = {}
    for i in range(len(df)):
        row = df.row(i, named=True)
        src_id = int(row["id"])

        n_fail = 0
        n_warn = 0

        # Check 1: EAZY grid edge
        z = safe_val(row, "z_phot_eazy", -1)
        if z > 12.85:
            n_fail += 1

        # Check 2: Photo-z quality
        chi2 = safe_val(row, "z_phot_chi2", 0)
        quality = row.get("z_phot_quality", "") or ""
        if chi2 > 100 or quality == "unreliable":
            n_fail += 1
        elif chi2 > 50 or quality == "uncertain":
            n_warn += 1

        # Check 3: Band coverage
        n_sn5 = safe_val(row, "n_bands_sn5", 0)
        n_bands = safe_val(row, "n_valid_bands", safe_val(row, "n_bands_covered", 0))
        if n_bands < 3:
            n_fail += 1
        elif n_sn5 < 2:
            n_warn += 1

        # Determine verdict
        if n_fail >= 3:
            verdict = "REJECT"
        elif n_fail >= 1:
            verdict = "UNCERTAIN"
        elif n_warn <= 1:
            verdict = "EXCELLENT"
        elif n_warn <= 2:
            verdict = "PROMISING"
        else:
            verdict = "UNCERTAIN"

        verdicts[src_id] = verdict

    return verdicts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Automated image inspection of JWST candidates")
    parser.add_argument("field", nargs="?", default=None, help="Field name (fuzzy match)")
    parser.add_argument("--all", action="store_true", help="All novel sources (default: EXCELLENT+PROMISING only)")
    parser.add_argument("--min-z", type=float, default=None)
    parser.add_argument("--max-z", type=float, default=None)
    parser.add_argument("--save", type=str, default=None, help="Save text report to file")
    parser.add_argument("--id", type=int, default=None, help="Specific source ID")
    args = parser.parse_args()

    workspaces = discover_workspaces()
    if not workspaces:
        print("No workspaces found")
        sys.exit(1)

    # Filter by field
    if args.field:
        key = args.field.lower().replace(" ", "").replace("_", "").replace("-", "")
        matched = {k: v for k, v in workspaces.items()
                   if key in k.lower().replace("-", "").replace("_", "")}
        if not matched:
            print(f"No workspace matching '{args.field}'")
            sys.exit(1)
        workspaces = matched

    print(f"Scanning {len(workspaces)} workspaces...", flush=True)

    all_results = []
    t0 = time.time()

    for field_name, ws_path in sorted(workspaces.items()):
        cat_path = ws_path / "catalogs" / "final_catalog.parquet"
        df = pl.read_parquet(cat_path)
        filters = discover_filters(df)

        # Select novel candidates
        if "novel" in df.columns:
            candidates = df.filter(pl.col("novel").cast(bool))
        else:
            candidates = df

        # Filter by z
        z_col = "z_phot_eazy" if "z_phot_eazy" in candidates.columns else None
        if z_col and args.min_z is not None:
            candidates = candidates.filter(pl.col(z_col) >= args.min_z)
        if z_col and args.max_z is not None:
            candidates = candidates.filter(pl.col(z_col) <= args.max_z)

        if args.id is not None:
            candidates = candidates.filter(pl.col("id") == args.id)

        if len(candidates) == 0:
            continue

        # Get scrutiny verdicts
        verdicts = get_scrutiny_verdicts(candidates, filters)

        # Unless --all, filter to EXCELLENT + PROMISING
        if not args.all and args.id is None:
            keep_ids = [sid for sid, v in verdicts.items() if v in ("EXCELLENT", "PROMISING")]
            if not keep_ids:
                continue
            candidates = candidates.filter(pl.col("id").is_in(keep_ids))

        n = len(candidates)
        print(f"\n{field_name}: {n} candidates, {len(filters)} filters", flush=True)

        for i in range(n):
            row = candidates.row(i, named=True)
            src_id = int(row["id"])
            verdict = verdicts.get(src_id, "UNKNOWN")

            result = inspect_candidate(ws_path, row, filters, verdict)
            all_results.append(result)

            if (i + 1) % 100 == 0:
                print(f"  [{i+1}/{n}] ...", flush=True)

    elapsed = time.time() - t0
    print(f"\nInspected {len(all_results)} candidates in {elapsed:.0f}s", flush=True)

    # Tally classifications
    from collections import Counter
    class_counts = Counter(r.classification for r in all_results)

    # Print summary
    lines = []
    lines.append("")
    lines.append("=" * 80)
    lines.append("  AUTOMATED IMAGE INSPECTION RESULTS")
    lines.append("=" * 80)
    lines.append(f"  Total inspected: {len(all_results)}")
    lines.append(f"  Time: {elapsed:.0f}s")
    lines.append("")
    for cls in ["REAL_LIKELY", "NOISE", "ARTIFACT", "FRAGMENT", "EDGE", "CROWDED", "UNCERTAIN"]:
        cnt = class_counts.get(cls, 0)
        pct = 100 * cnt / max(len(all_results), 1)
        lines.append(f"  {cls:15s} {cnt:6,d}  ({pct:5.1f}%)")

    # Per-field breakdown
    lines.append("")
    lines.append("-" * 80)
    lines.append(f"  {'FIELD':20s} {'TOTAL':>6s} {'REAL':>6s} {'NOISE':>6s} {'ARTIF':>6s} "
                 f"{'FRAG':>6s} {'EDGE':>6s} {'CROWD':>6s} {'UNC':>6s}")
    lines.append("-" * 80)

    field_results = {}
    for r in all_results:
        if r.field not in field_results:
            field_results[r.field] = Counter()
        field_results[r.field][r.classification] += 1

    for field_key in sorted(field_results.keys()):
        fc = field_results[field_key]
        total = sum(fc.values())
        lines.append(f"  {field_key:20s} {total:6d} {fc.get('REAL_LIKELY',0):6d} "
                     f"{fc.get('NOISE',0):6d} {fc.get('ARTIFACT',0):6d} "
                     f"{fc.get('FRAGMENT',0):6d} {fc.get('EDGE',0):6d} "
                     f"{fc.get('CROWDED',0):6d} {fc.get('UNCERTAIN',0):6d}")

    # High-z breakdown
    lines.append("")
    lines.append("-" * 80)
    lines.append("  HIGH-Z CANDIDATES (z > 5)")
    lines.append("-" * 80)
    hz = [r for r in all_results if r.z_phot > 5]
    hz_counts = Counter(r.classification for r in hz)
    lines.append(f"  Total at z>5: {len(hz)}")
    for cls in ["REAL_LIKELY", "NOISE", "ARTIFACT", "FRAGMENT", "EDGE", "CROWDED", "UNCERTAIN"]:
        cnt = hz_counts.get(cls, 0)
        if cnt > 0:
            lines.append(f"    {cls:15s} {cnt:6d}")

    # Show REAL_LIKELY high-z details
    real_hz = [r for r in hz if r.classification == "REAL_LIKELY"]
    if real_hz:
        lines.append("")
        lines.append(f"  REAL_LIKELY at z>5 ({len(real_hz)} candidates):")
        lines.append(f"  {'FIELD':20s} {'ID':>7s} {'z':>6s} {'S/N':>6s} {'Class':15s} {'Checks':>10s}")
        for r in sorted(real_hz, key=lambda x: -x.max_sn):
            checks_str = f"{r.n_pass}P/{r.n_warn}W/{r.n_fail}F"
            lines.append(f"  {r.field:20s} {r.src_id:7d} {r.z_phot:6.2f} "
                         f"{r.max_sn:6.0f} {r.object_class:15s} {checks_str:>10s}")

    # Detailed per-candidate table
    lines.append("")
    lines.append("=" * 100)
    lines.append("  DETAILED CANDIDATE TABLE")
    lines.append("=" * 100)
    lines.append(f"  {'FIELD':20s} {'ID':>7s} {'z':>6s} {'S/N':>6s} {'Class':12s} "
                 f"{'Scrutiny':10s} {'Image':12s} {'P':>2s} {'W':>2s} {'F':>2s}")
    lines.append("-" * 100)

    for r in sorted(all_results, key=lambda x: (
        {"REAL_LIKELY": 0, "UNCERTAIN": 1, "CROWDED": 2, "NOISE": 3,
         "FRAGMENT": 4, "ARTIFACT": 5, "EDGE": 6}.get(x.classification, 9),
        -x.max_sn
    )):
        z_str = f"{r.z_phot:.2f}" if r.z_phot >= 0 else "---"
        lines.append(f"  {r.field:20s} {r.src_id:7d} {z_str:>6s} {r.max_sn:6.0f} "
                     f"{r.object_class:12s} {r.scrutiny_verdict:10s} "
                     f"{r.classification:12s} {r.n_pass:2d} {r.n_warn:2d} {r.n_fail:2d}")

    output = "\n".join(lines)
    print(output)

    # Save JSON log
    log_data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n_inspected": len(all_results),
        "elapsed_seconds": elapsed,
        "summary": dict(class_counts),
        "candidates": [
            {
                "field": r.field,
                "id": r.src_id,
                "ra": r.ra,
                "dec": r.dec,
                "z_phot": r.z_phot,
                "max_sn": r.max_sn,
                "object_class": r.object_class,
                "scrutiny_verdict": r.scrutiny_verdict,
                "classification": r.classification,
                "n_pass": r.n_pass,
                "n_warn": r.n_warn,
                "n_fail": r.n_fail,
                "rgb_exists": r.rgb_exists,
                "sed_exists": r.sed_exists,
                "n_filter_cutouts": r.n_filter_cutouts,
                "checks": [{"name": c.name, "status": c.status, "detail": c.detail, "value": c.value}
                           for c in r.checks],
            }
            for r in all_results
        ],
    }

    OUTPUT_LOG.parent.mkdir(parents=True, exist_ok=True)

    class NumpyEncoder(json.JSONEncoder):
        """JSON encoder that handles numpy types."""
        def default(self, obj):
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                v = float(obj)
                return None if not np.isfinite(v) else v
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            if isinstance(obj, (np.bool_,)):
                return bool(obj)
            return super().default(obj)

    def sanitize_for_json(obj):
        """Replace NaN/Inf with None for JSON serialization."""
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating, float)):
            v = float(obj)
            return None if not np.isfinite(v) else v
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, dict):
            return {k: sanitize_for_json(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [sanitize_for_json(v) for v in obj]
        return obj

    with open(OUTPUT_LOG, "w") as f:
        json.dump(sanitize_for_json(log_data), f, indent=2, cls=NumpyEncoder)
    print(f"\nJSON log saved to {OUTPUT_LOG}")

    if args.save:
        with open(args.save, "w") as f:
            f.write(output)
        print(f"Text report saved to {args.save}")


if __name__ == "__main__":
    main()
