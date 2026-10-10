#!/usr/bin/env python3
"""Scrutinize JWST discovery candidates with standardized quality checks.

Runs 15 automated checks on each candidate and produces PASS/WARN/FAIL verdicts.
Checks include EAZY grid edge, photo-z quality, SED consistency, spike detection,
morphology, band coverage, proximity, multi-wavelength, duplicates, SED slope,
crowding density, bright-neighbor contamination, fragment clustering, ICL risk,
and published catalog gap warnings.

Usage:
    python scripts/scrutinize_candidates.py                    # All fields, gold tier
    python scripts/scrutinize_candidates.py MACS-J0647         # Single field
    python scripts/scrutinize_candidates.py --all-tiers        # All tiers
    python scripts/scrutinize_candidates.py --min-z 8          # Only z>8
    python scripts/scrutinize_candidates.py --id 1931          # Specific source
    python scripts/scrutinize_candidates.py --save report.txt  # Save output
"""

import argparse
import math
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np
import polars as pl

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WORKSPACES_DIR = Path.home() / ".jwst-discovery" / "workspaces"

PIVOT_WAVELENGTH_UM = {
    "F070W": 0.704, "F090W": 0.901, "F115W": 1.154, "F140M": 1.405,
    "F150W": 1.501, "F162M": 1.627, "F182M": 1.845, "F200W": 1.989,
    "F210M": 2.093, "F250M": 2.503, "F277W": 2.762, "F300M": 2.996,
    "F335M": 3.365, "F356W": 3.568, "F360M": 3.621, "F410M": 4.092,
    "F430M": 4.280, "F444W": 4.421, "F460M": 4.624, "F480M": 4.834,
}

LYMAN_ALPHA_UM = 0.1216
EAZY_Z_GRID_EDGE = 12.85


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class Verdict(Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class CheckResult:
    name: str
    verdict: Verdict
    detail: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finite(v):
    """Return True if v is a finite number."""
    if v is None:
        return False
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _fval(v, default=float("nan")):
    """Coerce to float, returning default for None/NaN/non-numeric."""
    if v is None:
        return default
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _bval(v):
    """Coerce to bool, treating None/NaN as False."""
    if v is None:
        return False
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    try:
        return bool(v)
    except (TypeError, ValueError):
        return False


def _get_cluster_center(ws_path, field_name):
    """Try to extract cluster center RA/Dec from run_pipeline script or config."""
    import json as _json
    # Check for config.json in workspace
    cfg_path = ws_path / "config.json"
    if cfg_path.exists():
        try:
            with open(cfg_path) as f:
                cfg = _json.load(f)
                return _fval(cfg.get("center_ra")), _fval(cfg.get("center_dec"))
        except Exception:
            pass
    # Try to find the run_pipeline script for this field
    scripts_dir = Path(__file__).parent.parent
    for p in scripts_dir.glob("run_pipeline_*.py"):
        try:
            text = p.read_text()
            if field_name.lower().replace("-", "").replace(" ", "") in p.name.lower().replace("_", ""):
                # Parse center_ra and center_dec from config dict
                import re
                ra_m = re.search(r'"center_ra"\s*:\s*([\d.]+)', text)
                dec_m = re.search(r'"center_dec"\s*:\s*(-?[\d.]+)', text)
                if ra_m and dec_m:
                    return float(ra_m.group(1)), float(dec_m.group(1))
        except Exception:
            continue
    return None, None


def _get_nearby_rows(row, df, radius_arcsec=2.0):
    """Get rows of sources within radius_arcsec of this source."""
    ra = _fval(row["ra"])
    dec = _fval(row["dec"])
    src_id = row["id"]
    if not _finite(ra) or not _finite(dec):
        return []

    cos_dec = np.cos(np.radians(dec))
    ra_margin = radius_arcsec / 3600.0 / max(cos_dec, 0.01)
    dec_margin = radius_arcsec / 3600.0

    nearby = df.filter(
        (pl.col("ra") > ra - ra_margin) & (pl.col("ra") < ra + ra_margin) &
        (pl.col("dec") > dec - dec_margin) & (pl.col("dec") < dec + dec_margin) &
        (pl.col("id") != src_id)
    )

    return [nearby.row(j, named=True) for j in range(min(nearby.height, 50))]


def discover_filters(df):
    """Return sorted list of NIRCam filters present in the catalog."""
    filters = []
    for col in df.columns:
        if col.startswith("mag_") and not col.startswith(("mag_kron", "mag_psf", "mag_intrinsic", "magerr")):
            filt = col[4:]
            if filt in PIVOT_WAVELENGTH_UM:
                filters.append(filt)
    filters.sort(key=lambda f: PIVOT_WAVELENGTH_UM[f])
    return filters


def discover_workspaces():
    """Return list of (field_name, workspace_path) with final catalogs."""
    results = []
    if not WORKSPACES_DIR.exists():
        return results
    for d in sorted(WORKSPACES_DIR.iterdir()):
        if d.is_dir() and (d / "catalogs" / "final_catalog.parquet").exists():
            results.append((d.name, d))
    return results


def match_fields(patterns, all_fields):
    """Fuzzy-match field names (case-insensitive substring)."""
    matched = []
    for name, path in all_fields:
        for pat in patterns:
            if pat.lower() in name.lower():
                matched.append((name, path))
                break
    return matched


def get_z(row):
    """Get best available redshift from row dict."""
    for col in ("z_phot_eazy", "best_photo_z", "best_spec_z"):
        v = _fval(row.get(col))
        if _finite(v) and v > 0:
            return v
    return float("nan")


def select_candidates(df, args):
    """Filter catalog to candidates matching CLI args."""
    if args.id is not None:
        return df.filter(pl.col("id") == args.id)

    mask = pl.lit(True)

    # Tier filter
    if "confidence_tier" in df.columns:
        if args.all_tiers:
            pass
        elif args.min_tier is not None:
            mask = mask & (pl.col("confidence_tier").is_not_null()) & (pl.col("confidence_tier") >= 1) & (pl.col("confidence_tier") <= args.min_tier)
        else:
            mask = mask & (pl.col("confidence_tier") == 1)

    # Novel filter
    if not args.include_known and "novel" in df.columns:
        mask = mask & (pl.col("novel") == True)  # noqa: E712

    # Redshift filters
    z_col = "z_phot_eazy" if "z_phot_eazy" in df.columns else "best_photo_z"
    if args.min_z is not None and z_col in df.columns:
        mask = mask & (pl.col(z_col) > args.min_z)
    if args.max_z is not None and z_col in df.columns:
        mask = mask & (pl.col(z_col) < args.max_z)

    return df.filter(mask)


# ---------------------------------------------------------------------------
# The 10 checks
# ---------------------------------------------------------------------------

def check_eazy_grid_edge(row):
    z = _fval(row.get("z_phot_eazy"))
    quality = str(row.get("z_phot_quality", "") or "")
    chi2 = _fval(row.get("z_phot_chi2"))
    # EAZY failure (z=-1 or z=0 with unreliable quality) is a FAIL, not SKIP
    if not _finite(z) or z <= 0:
        if quality == "unreliable":
            return CheckResult("EAZY grid edge", Verdict.FAIL,
                               f"EAZY failed (z={z}, quality=unreliable, chi2={chi2:.1f})" if _finite(chi2) else "EAZY failed completely")
        return CheckResult("EAZY grid edge", Verdict.WARN, "No valid EAZY photo-z — redshift unconstrained")
    if z > EAZY_Z_GRID_EDGE:
        drop = str(row.get("dropout_flag", "") or "")
        has_drop = drop not in ("", "none", "None")
        if has_drop:
            return CheckResult("EAZY grid edge", Verdict.WARN, f"z={z:.2f} at grid max BUT has {drop}")
        return CheckResult("EAZY grid edge", Verdict.FAIL, f"z={z:.2f} at EAZY grid max (12.87), unreliable")
    return CheckResult("EAZY grid edge", Verdict.PASS, f"z={z:.2f}, not at grid max")


def check_photoz_quality(row):
    z = _fval(row.get("z_phot_eazy"))
    quality = str(row.get("z_phot_quality", "") or "")
    if not _finite(z) or z <= 0:
        if quality == "unreliable":
            return CheckResult("Photo-z quality", Verdict.FAIL, "EAZY fit unreliable — no valid redshift")
        return CheckResult("Photo-z quality", Verdict.WARN, "No EAZY photo-z available")
    chi2 = _fval(row.get("z_phot_chi2"))
    quality = str(row.get("z_phot_quality", "") or "")
    if not _finite(chi2):
        return CheckResult("Photo-z quality", Verdict.FAIL, "chi2 unavailable, EAZY did not converge")
    if chi2 < 1e-10:
        return CheckResult("Photo-z quality", Verdict.FAIL, f"chi2={chi2:.1e}, EAZY did not converge")
    # Suspiciously LOW chi2: underfitting, too few constraints, or noise-dominated
    if chi2 < 0.1:
        return CheckResult("Photo-z quality", Verdict.FAIL,
                           f"chi2={chi2:.3f}, suspiciously low — underconstrained fit or noise-dominated")
    if chi2 < 0.5:
        return CheckResult("Photo-z quality", Verdict.WARN,
                           f"chi2={chi2:.2f}, unusually low — may indicate weak constraints")
    if chi2 > 100:
        return CheckResult("Photo-z quality", Verdict.FAIL, f"chi2={chi2:.1f}, very poor template fit")
    if quality == "unreliable":
        return CheckResult("Photo-z quality", Verdict.FAIL, f"chi2={chi2:.1f}, quality=unreliable")
    if chi2 > 50:
        return CheckResult("Photo-z quality", Verdict.WARN, f"chi2={chi2:.1f}, mediocre template fit")
    if quality == "uncertain":
        return CheckResult("Photo-z quality", Verdict.WARN, f"chi2={chi2:.1f}, quality=uncertain")
    return CheckResult("Photo-z quality", Verdict.PASS, f"chi2={chi2:.1f}, quality={quality or 'good'}")


def check_sed_consistency(row, filters):
    z = get_z(row)
    drop = str(row.get("dropout_flag", "") or "")
    has_drop = drop not in ("", "none", "None")
    is_hz = (_finite(z) and z > 6) or has_drop

    if not is_hz:
        return CheckResult("SED consistency", Verdict.PASS, "Not a high-z candidate, N/A")

    if not _finite(z) or z <= 0:
        if has_drop:
            z = 10.0  # rough estimate for dropout without EAZY
        else:
            return CheckResult("SED consistency", Verdict.SKIP, "No redshift for Lyman break calc")

    lyman_obs = LYMAN_ALPHA_UM * (1 + z)

    below_detected = []
    above_sn5 = []
    above_mags = []

    for f in filters:
        wl = PIVOT_WAVELENGTH_UM[f]
        sn = _fval(row.get(f"sn_{f}"))
        mag = _fval(row.get(f"mag_{f}"))
        if wl < lyman_obs:
            if _finite(sn) and sn > 3:
                below_detected.append((f, sn))
        else:
            if _finite(sn) and sn > 5:
                above_sn5.append(f)
            if _finite(mag):
                above_mags.append((wl, mag))

    # Check for detections below the break
    if below_detected:
        bands = ", ".join(f"{f}(S/N={sn:.0f})" for f, sn in below_detected)
        return CheckResult("SED consistency", Verdict.FAIL, f"Detected BELOW Lyman break: {bands}")

    if len(above_sn5) == 0:
        return CheckResult("SED consistency", Verdict.FAIL, "No bands above break at S/N>5")

    if len(above_sn5) == 1:
        return CheckResult("SED consistency", Verdict.WARN, f"Only 1 band above break at S/N>5 ({above_sn5[0]})")

    # Check flat continuum redward of break
    above_mags.sort()
    steep_count = 0
    for i in range(len(above_mags) - 1):
        color = above_mags[i + 1][1] - above_mags[i][1]  # red - blue (positive = getting fainter)
        if abs(color) > 0.5:
            steep_count += 1

    if len(above_mags) > 1 and steep_count > len(above_mags) // 2:
        return CheckResult("SED consistency", Verdict.WARN,
                           f"Steep continuum redward of break ({steep_count}/{len(above_mags)-1} pairs >0.5 mag)")

    return CheckResult("SED consistency", Verdict.PASS,
                        f"{'Dropout' if has_drop else 'High-z'}, {len(above_sn5)} bands above break at S/N>5")


def check_single_band_spike(row, filters):
    if _bval(row.get("is_spike_artifact")):
        sf = str(row.get("spike_filter", "") or "")
        return CheckResult("Single-band spike", Verdict.FAIL, f"Flagged as spike artifact in {sf}")

    max_sn = _fval(row.get("max_sn"))
    med_sn = _fval(row.get("median_sn"))
    rh = _fval(row.get("r_half_pix"), 5.0)

    ratio = max_sn / med_sn if (_finite(max_sn) and _finite(med_sn) and med_sn > 0) else 0.0

    # Also check per-filter flux spikes
    fluxes = []
    for f in filters:
        fx = _fval(row.get(f"flux_{f}"))
        if _finite(fx) and fx > 0:
            fluxes.append(fx)

    spike_detected = False
    if len(fluxes) >= 3:
        med_flux = float(np.median(fluxes))
        if med_flux > 0:
            spike_detected = any(fx > 10 * med_flux for fx in fluxes)

    if ratio > 10 or spike_detected:
        detail = f"max_sn/median_sn={ratio:.1f}" if ratio > 10 else "single-band flux >10x median"
        if rh < 2.0:
            return CheckResult("Single-band spike", Verdict.FAIL, f"{detail}, unresolved (r_h={rh:.1f})")
        return CheckResult("Single-band spike", Verdict.WARN, f"{detail}, but resolved (r_h={rh:.1f})")

    return CheckResult("Single-band spike", Verdict.PASS, f"max_sn/median_sn={ratio:.1f}, no spike")


def check_morphology(row):
    rh = _fval(row.get("r_half_pix"), 5.0)
    ell = _fval(row.get("ellipticity"), 0.0)
    asym = _fval(row.get("asymmetry"))
    nf = _fval(row.get("nan_frac"), 0.0)

    problems = []
    if _finite(rh) and rh < 1.5:
        problems.append(f"r_h={rh:.1f}<1.5 (unresolved)")
    if _finite(rh) and rh > 20:
        problems.append(f"r_h={rh:.1f}>20 (extended/artifact)")
    if _finite(ell) and ell > 0.8:
        problems.append(f"ell={ell:.2f}>0.8 (elongated)")
    if _finite(asym) and asym > 0.5:
        problems.append(f"asym={asym:.2f}>0.5")
    if _finite(nf) and nf > 0.25:
        problems.append(f"nan_frac={nf:.2f}>0.25")

    if len(problems) >= 2:
        return CheckResult("Morphology", Verdict.FAIL, "; ".join(problems))
    if len(problems) == 1:
        return CheckResult("Morphology", Verdict.WARN, problems[0])
    return CheckResult("Morphology", Verdict.PASS, f"r_h={rh:.1f}, ell={ell:.2f}, OK")


def check_band_coverage(row):
    nv = row.get("n_valid_bands")
    ns = row.get("n_bands_sn5")

    nv = int(nv) if nv is not None and _finite(nv) else None
    ns = int(ns) if ns is not None and _finite(ns) else None

    if nv is not None and nv < 3:
        return CheckResult("Band coverage", Verdict.FAIL, f"Only {nv} valid bands, insufficient for photo-z")
    if ns is not None and ns < 2:
        return CheckResult("Band coverage", Verdict.WARN, f"Only {ns} bands at S/N>5, marginal")
    if nv is not None and ns is not None:
        return CheckResult("Band coverage", Verdict.PASS, f"{nv} bands, {ns} at S/N>5")
    return CheckResult("Band coverage", Verdict.SKIP, "Band count columns missing")


def check_proximity(row):
    is_frag = _bval(row.get("is_fragment"))
    near_star = _bval(row.get("near_bright_star"))
    at_edge = _bval(row.get("at_edge"))
    mosaic_edge = _bval(row.get("is_mosaic_edge"))

    if mosaic_edge:
        return CheckResult("Proximity", Verdict.FAIL, "At mosaic edge")
    if is_frag and near_star:
        return CheckResult("Proximity", Verdict.FAIL, "Fragment near bright star")

    issues = []
    if is_frag:
        issues.append("fragment")
    if near_star:
        issues.append("near bright star")
    if at_edge:
        issues.append("at image edge")

    if issues:
        return CheckResult("Proximity", Verdict.WARN, ", ".join(issues))
    return CheckResult("Proximity", Verdict.PASS, "No proximity issues")


def check_multiwavelength(row):
    z = get_z(row)
    gaia = _bval(row.get("gaia_match"))
    wise = _bval(row.get("wise_match"))
    spitzer = _bval(row.get("spitzer_match"))
    simbad_type = str(row.get("simbad_otype", "") or "").lower()
    novel = _bval(row.get("novel", True))

    if gaia and _finite(z) and z > 6:
        return CheckResult("Multi-wavelength", Verdict.FAIL,
                           f"Gaia match at z={z:.1f} -- likely foreground star")
    if gaia and ("star" in simbad_type or simbad_type.startswith("*")):
        return CheckResult("Multi-wavelength", Verdict.FAIL,
                           f"Gaia + SIMBAD star ({simbad_type})")

    notes = []
    if novel and wise:
        notes.append("WISE match")
    if novel and spitzer:
        notes.append("Spitzer match")
    if notes:
        return CheckResult("Multi-wavelength", Verdict.WARN,
                           f"Novel but has {' + '.join(notes)} -- known IR source?")

    return CheckResult("Multi-wavelength", Verdict.PASS, "No conflicting matches")


def check_duplicates(row, all_ra, all_dec, all_ids, all_flux):
    ra = _fval(row["ra"])
    dec = _fval(row["dec"])
    src_id = row["id"]
    src_flux = _fval(row.get("flux", 0))

    if not _finite(ra) or not _finite(dec):
        return CheckResult("Duplicates", Verdict.SKIP, "No coordinates")

    cos_dec = np.cos(np.radians(dec))
    ra_margin = 3.0 / 3600.0 / max(cos_dec, 0.01)
    dec_margin = 3.0 / 3600.0

    in_box = (
        (np.abs(all_ra - ra) < ra_margin) &
        (np.abs(all_dec - dec) < dec_margin) &
        (all_ids != src_id)
    )

    if not np.any(in_box):
        return CheckResult("Duplicates", Verdict.PASS, "No neighbors within 3\"")

    box_ra = all_ra[in_box]
    box_dec = all_dec[in_box]
    box_ids = all_ids[in_box]
    box_flux = all_flux[in_box]

    d_ra = (box_ra - ra) * cos_dec * 3600
    d_dec = (box_dec - dec) * 3600
    sep = np.sqrt(d_ra**2 + d_dec**2)

    within_1 = sep < 1.0
    if not np.any(within_1):
        return CheckResult("Duplicates", Verdict.PASS, "No duplicates within 1\"")

    for j in np.where(within_1)[0]:
        if src_flux > 0 and box_flux[j] > 0:
            ratio = box_flux[j] / src_flux
            if 0.3 < ratio < 3.0:
                return CheckResult("Duplicates", Verdict.WARN,
                                   f"Possible duplicate ID {int(box_ids[j])} at {sep[j]:.2f}\" (flux ratio={ratio:.1f})")

    return CheckResult("Duplicates", Verdict.PASS, "Neighbor at <1\" but flux differs")


def check_sed_slope(row, filters):
    slope = _fval(row.get("sed_slope"))

    # Check for non-monotonic SED (V-shape) with severity grading
    fluxes_wl = []
    for f in filters:
        fx = _fval(row.get(f"flux_{f}"))
        if _finite(fx) and fx > 0:
            fluxes_wl.append((PIVOT_WAVELENGTH_UM[f], fx, f))

    v_shape = False
    v_depth = 0.0  # depth of V in magnitudes
    v_filter = ""
    if len(fluxes_wl) >= 3:
        fluxes_wl.sort()
        fvals = [x[1] for x in fluxes_wl]
        min_idx = fvals.index(min(fvals))
        if 0 < min_idx < len(fvals) - 1:
            v_shape = True
            v_filter = fluxes_wl[min_idx][2]
            # Measure V depth: ratio of trough to average of neighbors
            left_avg = np.mean(fvals[:min_idx])
            right_avg = np.mean(fvals[min_idx + 1:])
            trough = fvals[min_idx]
            if trough > 0:
                wing_avg = (left_avg + right_avg) / 2
                v_depth = 2.5 * np.log10(wing_avg / trough)  # mag difference

    # Strong V-shape is non-physical for a galaxy — likely artifact or AGN
    if v_shape and v_depth > 1.0:
        detail = f"Deep V-shaped SED at {v_filter} ({v_depth:.1f} mag dip) — non-physical, likely artifact"
        return CheckResult("SED slope", Verdict.FAIL, detail)

    if _finite(slope):
        if abs(slope) > 3.0:
            return CheckResult("SED slope", Verdict.FAIL, f"slope={slope:.1f}, extreme")
        if slope > 2.0:
            detail = f"slope={slope:.1f}, steep positive"
            if v_shape:
                detail += f" + V-shaped at {v_filter} ({v_depth:.1f} mag)"
            return CheckResult("SED slope", Verdict.WARN, detail)
        if slope < -2.0:
            return CheckResult("SED slope", Verdict.WARN, f"slope={slope:.1f}, steep negative")
        if v_shape:
            return CheckResult("SED slope", Verdict.WARN,
                               f"slope={slope:.1f}, V-shaped at {v_filter} ({v_depth:.1f} mag dip)")
        return CheckResult("SED slope", Verdict.PASS, f"slope={slope:.1f}, plausible")

    # No slope column — check V-shape only
    if v_shape:
        if v_depth > 0.5:
            return CheckResult("SED slope", Verdict.WARN,
                               f"V-shaped SED at {v_filter} ({v_depth:.1f} mag dip)")
        return CheckResult("SED slope", Verdict.PASS, f"Mild SED curvature at {v_filter}")
    if len(fluxes_wl) < 3:
        return CheckResult("SED slope", Verdict.SKIP, "Too few bands for slope check")
    return CheckResult("SED slope", Verdict.PASS, "SED shape plausible")


# ---------------------------------------------------------------------------
# New deep-vetting checks (11-15)
# ---------------------------------------------------------------------------

def check_crowding(row, all_ra, all_dec, all_ids, all_flux):
    """Check local source density — crowded regions cause deblending artifacts."""
    ra = _fval(row["ra"])
    dec = _fval(row["dec"])
    src_id = row["id"]

    if not _finite(ra) or not _finite(dec):
        return CheckResult("Crowding", Verdict.SKIP, "No coordinates")

    cos_dec = np.cos(np.radians(dec))
    # Count neighbors within 3"
    ra_margin = 3.0 / 3600.0 / max(cos_dec, 0.01)
    dec_margin = 3.0 / 3600.0
    in_box = (
        (np.abs(all_ra - ra) < ra_margin) &
        (np.abs(all_dec - dec) < dec_margin) &
        (all_ids != src_id)
    )
    n_neighbors_3 = int(np.sum(in_box))

    # Count within 1"
    ra_margin_1 = 1.0 / 3600.0 / max(cos_dec, 0.01)
    dec_margin_1 = 1.0 / 3600.0
    in_box_1 = (
        (np.abs(all_ra - ra) < ra_margin_1) &
        (np.abs(all_dec - dec) < dec_margin_1) &
        (all_ids != src_id)
    )
    n_neighbors_1 = int(np.sum(in_box_1))

    if n_neighbors_3 > 30:
        return CheckResult("Crowding", Verdict.FAIL,
                           f"{n_neighbors_3} sources within 3\" — extreme crowding, likely deblending artifact")
    if n_neighbors_3 > 15:
        return CheckResult("Crowding", Verdict.WARN,
                           f"{n_neighbors_3} sources within 3\" — high density region")
    if n_neighbors_1 > 3:
        return CheckResult("Crowding", Verdict.WARN,
                           f"{n_neighbors_1} sources within 1\" — potential confusion")
    return CheckResult("Crowding", Verdict.PASS, f"{n_neighbors_3} neighbors within 3\"")


def check_bright_neighbor(row, all_ra, all_dec, all_ids, all_flux):
    """Check if a much brighter source is nearby — could be deblending artifact of it."""
    ra = _fval(row["ra"])
    dec = _fval(row["dec"])
    src_id = row["id"]
    src_flux = _fval(row.get("flux", 0))

    if not _finite(ra) or not _finite(dec):
        return CheckResult("Bright neighbor", Verdict.SKIP, "No coordinates")
    if not _finite(src_flux) or src_flux <= 0:
        return CheckResult("Bright neighbor", Verdict.SKIP, "No flux for comparison")

    cos_dec = np.cos(np.radians(dec))
    # Search within 5"
    ra_margin = 5.0 / 3600.0 / max(cos_dec, 0.01)
    dec_margin = 5.0 / 3600.0
    in_box = (
        (np.abs(all_ra - ra) < ra_margin) &
        (np.abs(all_dec - dec) < dec_margin) &
        (all_ids != src_id) &
        (all_flux > 0)
    )

    if not np.any(in_box):
        return CheckResult("Bright neighbor", Verdict.PASS, "No neighbors within 5\"")

    box_ra = all_ra[in_box]
    box_dec = all_dec[in_box]
    box_ids = all_ids[in_box]
    box_flux = all_flux[in_box]

    d_ra = (box_ra - ra) * cos_dec * 3600
    d_dec = (box_dec - dec) * 3600
    sep = np.sqrt(d_ra**2 + d_dec**2)

    # Find brightest neighbor within 5"
    max_ratio = 0
    max_id = 0
    max_sep = 0
    for j in range(len(box_flux)):
        if sep[j] < 5.0 and box_flux[j] > 0:
            ratio = box_flux[j] / src_flux
            if ratio > max_ratio:
                max_ratio = ratio
                max_id = int(box_ids[j])
                max_sep = sep[j]

    if max_ratio > 50 and max_sep < 2.0:
        return CheckResult("Bright neighbor", Verdict.FAIL,
                           f"ID {max_id} is {max_ratio:.0f}x brighter at {max_sep:.1f}\" — likely deblending artifact")
    if max_ratio > 20 and max_sep < 3.0:
        return CheckResult("Bright neighbor", Verdict.WARN,
                           f"ID {max_id} is {max_ratio:.0f}x brighter at {max_sep:.1f}\" — check for contamination")
    if max_ratio > 10 and max_sep < 1.5:
        return CheckResult("Bright neighbor", Verdict.WARN,
                           f"ID {max_id} is {max_ratio:.0f}x brighter at {max_sep:.1f}\"")

    return CheckResult("Bright neighbor", Verdict.PASS,
                        f"Brightest neighbor {max_ratio:.0f}x at {max_sep:.1f}\"")


def check_fragment_cluster(row, df_rows_nearby):
    """Check if multiple fragment-flagged sources cluster around this source."""
    n_frags = 0
    frag_ids = []
    for nr in df_rows_nearby:
        if _bval(nr.get("is_fragment")):
            n_frags += 1
            frag_ids.append(nr["id"])

    if n_frags >= 3:
        ids_str = ", ".join(str(i) for i in frag_ids[:5])
        return CheckResult("Fragment cluster", Verdict.WARN,
                           f"{n_frags} fragment-flagged sources nearby (IDs: {ids_str}) — extended or blended?")
    if n_frags >= 1:
        ids_str = ", ".join(str(i) for i in frag_ids)
        return CheckResult("Fragment cluster", Verdict.PASS,
                           f"{n_frags} fragment(s) nearby (IDs: {ids_str})")
    return CheckResult("Fragment cluster", Verdict.PASS, "No fragments nearby")


def check_icl_contamination(row, config_ra=None, config_dec=None):
    """Check if source could be intra-cluster light (ICL) — extended, low SB, near cluster center."""
    rh = _fval(row.get("r_half_pix"), 5.0)
    asym = _fval(row.get("asymmetry"), 0.0)
    chi2 = _fval(row.get("z_phot_chi2"))
    sn = _fval(row.get("max_sn"), 0)

    icl_indicators = 0
    details = []

    # Very extended
    if _finite(rh) and rh > 15:
        icl_indicators += 1
        details.append(f"r_h={rh:.1f} (very extended)")

    # Low surface brightness
    if _finite(rh) and _finite(sn) and rh > 5:
        sb_proxy = sn / (rh**2)  # crude SB proxy
        if sb_proxy < 1.0:
            icl_indicators += 1
            details.append(f"low surface brightness (S/N/r_h^2={sb_proxy:.2f})")

    # Suspiciously low chi2 combined with extended
    if _finite(chi2) and chi2 < 0.1 and _finite(rh) and rh > 10:
        icl_indicators += 1
        details.append(f"chi2={chi2:.3f} + extended — noise-dominated SED")

    # High asymmetry + extended = diffuse ICL blob
    if _finite(asym) and asym > 0.3 and _finite(rh) and rh > 10:
        icl_indicators += 1
        details.append(f"asymmetric ({asym:.2f}) + extended")

    # Check distance from cluster center if available
    if config_ra is not None and config_dec is not None:
        ra = _fval(row.get("ra"))
        dec = _fval(row.get("dec"))
        if _finite(ra) and _finite(dec):
            sep_arcmin = np.sqrt(((ra - config_ra) * np.cos(np.radians(dec)))**2 +
                                  (dec - config_dec)**2) * 60
            if sep_arcmin < 1.0 and icl_indicators > 0:
                icl_indicators += 1
                details.append(f"{sep_arcmin:.1f}' from cluster center")

    if icl_indicators >= 3:
        return CheckResult("ICL contamination", Verdict.FAIL,
                           "Likely ICL: " + "; ".join(details))
    if icl_indicators >= 2:
        return CheckResult("ICL contamination", Verdict.WARN,
                           "Possible ICL: " + "; ".join(details))
    if icl_indicators == 1:
        return CheckResult("ICL contamination", Verdict.PASS,
                           "Minor: " + "; ".join(details))
    return CheckResult("ICL contamination", Verdict.PASS, "No ICL indicators")


def check_brightness_novelty(row):
    """Extremely bright 'novel' sources likely indicate missing reference catalogs, not discoveries."""
    sn = _fval(row.get("max_sn"), 0)
    novel = _bval(row.get("novel", True))
    n_matched = _fval(row.get("n_catalogs_matched"), 0)

    # Get brightest magnitude
    brightest_mag = 99.0
    for col_name, val in row.items():
        if col_name.startswith("mag_") and not col_name.startswith(("mag_kron", "mag_psf", "mag_intrinsic", "magerr")):
            v = _fval(val)
            if _finite(v) and v < brightest_mag:
                brightest_mag = v

    if not novel or not _finite(sn):
        return CheckResult("Brightness vs novelty", Verdict.PASS, "Not novel or no S/N")

    # A truly novel bright source is almost impossible — published surveys go much deeper
    if brightest_mag < 23.0 and n_matched == 0:
        return CheckResult("Brightness vs novelty", Verdict.FAIL,
                           f"mag={brightest_mag:.1f}, S/N={sn:.0f}, 0 catalog matches — "
                           "too bright to be uncataloged, reference catalogs likely incomplete")
    if brightest_mag < 24.0 and n_matched == 0:
        return CheckResult("Brightness vs novelty", Verdict.WARN,
                           f"mag={brightest_mag:.1f}, S/N={sn:.0f}, 0 matches — "
                           "unusually bright for a novel source")
    if sn > 1000 and n_matched == 0:
        return CheckResult("Brightness vs novelty", Verdict.WARN,
                           f"S/N={sn:.0f} with 0 catalog matches — suspiciously bright")
    return CheckResult("Brightness vs novelty", Verdict.PASS,
                        f"mag={brightest_mag:.1f}, S/N={sn:.0f}")


# Known JWST catalogs not in our reference set (for gap warnings)
KNOWN_CATALOG_GAPS = {
    "MACS-J0647": "Worku+2025 (arXiv:2512.11985): 57 z>6 candidates, 14 spectroscopic — NOT in reference set",
    "Sunrise-Arc": "Welch+2022/Meena+2023 lensing catalogs — NOT in reference set",
    "Cosmic-Gems": "Bradley+2024 Cosmic Gems NIRCam catalog — NOT in reference set",
}


def check_catalog_gap(field):
    """Warn if the field has known published catalogs we're not matching against."""
    gap = KNOWN_CATALOG_GAPS.get(field)
    if gap:
        return CheckResult("Catalog gap", Verdict.WARN, gap)
    return CheckResult("Catalog gap", Verdict.PASS, "No known missing reference catalogs")


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

def compute_verdict(results):
    n_pass = sum(1 for r in results if r.verdict == Verdict.PASS)
    n_warn = sum(1 for r in results if r.verdict == Verdict.WARN)
    n_fail = sum(1 for r in results if r.verdict == Verdict.FAIL)

    if n_fail >= 3:
        verdict = "REJECT"
    elif n_fail >= 1:
        verdict = "UNCERTAIN"
    elif n_warn >= 3:
        verdict = "UNCERTAIN"
    elif n_warn >= 1:
        verdict = "PROMISING"
    else:
        verdict = "EXCELLENT"

    return verdict, n_pass, n_warn, n_fail


VERDICT_SYMBOLS = {
    Verdict.PASS: "+",
    Verdict.WARN: "*",
    Verdict.FAIL: "!",
    Verdict.SKIP: "-",
}


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_source_report(field, row, results, filters, ws_path):
    z = get_z(row)
    sn = _fval(row.get("max_sn"), 0)
    cls = str(row.get("object_class", "") or "")
    ra = _fval(row.get("ra"))
    dec = _fval(row.get("dec"))
    drop = str(row.get("dropout_flag", "") or "")
    rank = row.get("anomaly_rank")
    sid = row["id"]

    verdict, n_pass, n_warn, n_fail = compute_verdict(results)

    lines = []
    lines.append(f"{'=' * 70}")
    lines.append(f"SOURCE: {field} ID {sid} | z={z:.2f} | S/N={sn:.0f} | {cls}")
    lines.append(f"  RA={ra:.5f} Dec={dec:.5f} | {drop or 'no dropout'} | rank={rank}")
    lines.append(f"{'-' * 70}")

    for r in results:
        sym = VERDICT_SYMBOLS[r.verdict]
        vname = r.verdict.value
        lines.append(f"  {sym} {vname:<5} {r.name:<22} {r.detail}")

    lines.append(f"{'-' * 70}")
    lines.append(f"  VERDICT: {verdict} ({n_pass} PASS, {n_warn} WARN, {n_fail} FAIL)")
    lines.append("")

    # Photometry
    lines.append("  Photometry:")
    items = []
    for f in filters:
        mag = _fval(row.get(f"mag_{f}"))
        magerr = _fval(row.get(f"magerr_{f}"))
        sn_f = _fval(row.get(f"sn_{f}"))
        if _finite(mag):
            err = f"+/-{magerr:.1f}" if _finite(magerr) else ""
            snstr = f"S/N={sn_f:.0f}" if _finite(sn_f) else ""
            items.append(f"{f}: {mag:.1f}{err} ({snstr})")
        else:
            items.append(f"{f}: ---")

    for i in range(0, len(items), 3):
        chunk = items[i:i + 3]
        lines.append("    " + "  ".join(f"{item:<28s}" for item in chunk))

    # File paths
    sid_str = f"src{sid:06d}"
    cutout_path = ws_path / "report" / "cutouts" / f"{sid_str}_rgb.png"
    sed_path = ws_path / "report" / "seds" / f"{sid_str}_sed.png"
    lines.append("")
    lines.append(f"  Cutouts: {cutout_path}" + ("" if cutout_path.exists() else " (not found)"))
    lines.append(f"  SED:     {sed_path}" + ("" if sed_path.exists() else " (not found)"))
    lines.append("")

    return "\n".join(lines)


def format_summary_table(summaries, sort_by="verdict"):
    if not summaries:
        return "No candidates to summarize."

    # Sort
    verdict_order = {"EXCELLENT": 0, "PROMISING": 1, "UNCERTAIN": 2, "REJECT": 3}
    if sort_by == "verdict":
        summaries.sort(key=lambda s: (verdict_order.get(s["verdict"], 9), -s["z"] if _finite(s["z"]) else 0))
    elif sort_by == "z":
        summaries.sort(key=lambda s: -s["z"] if _finite(s["z"]) else 0)
    elif sort_by == "sn":
        summaries.sort(key=lambda s: -s["sn"] if _finite(s["sn"]) else 0)
    elif sort_by == "field":
        summaries.sort(key=lambda s: (s["field"], s["id"]))

    lines = []
    lines.append(f"\n{'=' * 90}")
    lines.append("SUMMARY")
    lines.append(f"{'=' * 90}")
    hdr = f"{'FIELD':<16} {'ID':>6} {'z':>7} {'S/N':>6} {'Class':<18} {'P':>3} {'W':>3} {'F':>3} {'VERDICT':<10}"
    lines.append(hdr)
    lines.append("-" * 90)

    counts = {"EXCELLENT": 0, "PROMISING": 0, "UNCERTAIN": 0, "REJECT": 0}
    for s in summaries:
        z_str = f"{s['z']:.2f}" if _finite(s["z"]) else "  ---"
        sn_str = f"{s['sn']:.0f}" if _finite(s["sn"]) else " ---"
        line = (f"{s['field']:<16} {s['id']:>6} {z_str:>7} {sn_str:>6} "
                f"{s['cls']:<18} {s['n_pass']:>3} {s['n_warn']:>3} {s['n_fail']:>3} {s['verdict']:<10}")
        lines.append(line)
        counts[s["verdict"]] = counts.get(s["verdict"], 0) + 1

    lines.append("-" * 90)
    lines.append(f"Total: {len(summaries)} candidates | "
                 f"EXCELLENT: {counts.get('EXCELLENT', 0)} | "
                 f"PROMISING: {counts.get('PROMISING', 0)} | "
                 f"UNCERTAIN: {counts.get('UNCERTAIN', 0)} | "
                 f"REJECT: {counts.get('REJECT', 0)}")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Scrutinize gold-tier JWST discovery candidates")
    parser.add_argument("fields", nargs="*", default=[], help="Field names (substring match, default: all)")
    parser.add_argument("--all-tiers", action="store_true", help="Include all tiers, not just gold")
    parser.add_argument("--min-tier", type=int, default=None, help="Max tier number (1=gold, 2=silver, 3=bronze)")
    parser.add_argument("--min-z", type=float, default=None, help="Minimum photo-z")
    parser.add_argument("--max-z", type=float, default=None, help="Maximum photo-z")
    parser.add_argument("--id", type=int, default=None, help="Scrutinize specific source ID")
    parser.add_argument("--save", type=str, default=None, help="Save output to file")
    parser.add_argument("--include-known", action="store_true", help="Include non-novel sources")
    parser.add_argument("--sort", choices=["z", "sn", "verdict", "field"], default="verdict", help="Sort summary")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    all_fields = discover_workspaces()

    if not all_fields:
        print(f"ERROR: No workspaces found in {WORKSPACES_DIR}")
        sys.exit(1)

    if args.fields:
        selected = match_fields(args.fields, all_fields)
        if not selected:
            print(f"ERROR: No fields matched: {args.fields}")
            print(f"Available: {[n for n, _ in all_fields]}")
            sys.exit(1)
    else:
        selected = all_fields

    all_summaries = []
    output_blocks = []

    for field_name, ws_path in selected:
        catalog_path = ws_path / "catalogs" / "final_catalog.parquet"
        df = pl.read_parquet(catalog_path)
        filters = discover_filters(df)

        candidates = select_candidates(df, args)
        if candidates.height == 0:
            if args.id is None:
                tier_desc = "gold tier" if not args.all_tiers else "any tier"
                print(f"  {field_name}: 0 {tier_desc} candidates, skipping")
            continue

        print(f"  {field_name}: {candidates.height} candidates to scrutinize ({len(filters)} filters)")

        # Pre-compute arrays for duplicate check
        all_ra = df["ra"].to_numpy().astype(np.float64)
        all_dec = df["dec"].to_numpy().astype(np.float64)
        all_ids = df["id"].to_numpy()
        flux_col = "flux" if "flux" in df.columns else None
        all_flux = df[flux_col].to_numpy().astype(np.float64) if flux_col else np.zeros(len(df))

        # Try to get cluster center from config files
        cluster_ra, cluster_dec = _get_cluster_center(ws_path, field_name)

        for i in range(candidates.height):
            row = candidates.row(i, named=True)

            # Get nearby rows for fragment cluster check
            nearby_rows = _get_nearby_rows(row, df, 2.0)  # within 2"

            results = [
                check_eazy_grid_edge(row),
                check_photoz_quality(row),
                check_sed_consistency(row, filters),
                check_single_band_spike(row, filters),
                check_morphology(row),
                check_band_coverage(row),
                check_proximity(row),
                check_multiwavelength(row),
                check_duplicates(row, all_ra, all_dec, all_ids, all_flux),
                check_sed_slope(row, filters),
                check_crowding(row, all_ra, all_dec, all_ids, all_flux),
                check_bright_neighbor(row, all_ra, all_dec, all_ids, all_flux),
                check_fragment_cluster(row, nearby_rows),
                check_icl_contamination(row, cluster_ra, cluster_dec),
                check_brightness_novelty(row),
                check_catalog_gap(field_name),
            ]

            verdict, n_pass, n_warn, n_fail = compute_verdict(results)
            report = format_source_report(field_name, row, results, filters, ws_path)
            output_blocks.append(report)

            all_summaries.append({
                "field": field_name,
                "id": row["id"],
                "z": get_z(row),
                "sn": _fval(row.get("max_sn", 0)),
                "cls": str(row.get("object_class", "") or ""),
                "n_pass": n_pass,
                "n_warn": n_warn,
                "n_fail": n_fail,
                "verdict": verdict,
            })

    # Print all reports
    print()
    for block in output_blocks:
        print(block)

    # Summary table
    summary = format_summary_table(all_summaries, args.sort)
    print(summary)

    # Save
    if args.save:
        with open(args.save, "w") as f:
            for block in output_blocks:
                f.write(block + "\n")
            f.write(summary + "\n")
        print(f"Saved to {args.save}")


if __name__ == "__main__":
    main()
