#!/usr/bin/env python3
"""Consolidate image inspection + photo-z cross-check into final validation verdicts.

Merges:
  1. auto_inspection_log.json (image analysis classifications)
  2. photoz_crosscheck.parquet (dual-EAZY agreement/confidence)

Assigns final verdicts:
  VALIDATED       — image=REAL + photo-z agree (excellent/good) + high confidence
  LIKELY_REAL     — image=REAL + photo-z agree (>=marginal) + medium+ confidence
  NEEDS_SPECTRO   — image=REAL but photo-z disagree or multimodal P(z)
  DOWNGRADED      — photo-z catastrophic outlier, crowded/uncertain image
  REJECTED        — image=NOISE/ARTIFACT/FRAGMENT/EDGE

Usage:
    python scripts/consolidate_validation.py
    python scripts/consolidate_validation.py --field El-Gordo
    python scripts/consolidate_validation.py --min-z 5
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import polars as pl

# ---------------------------------------------------------------------------
# SED physics constants
# ---------------------------------------------------------------------------

PIVOT_WAVELENGTH_UM = {
    "F070W": 0.704, "F090W": 0.901, "F115W": 1.154, "F140M": 1.405,
    "F150W": 1.501, "F162M": 1.627, "F182M": 1.845, "F200W": 1.989,
    "F210M": 2.093, "F250M": 2.503, "F277W": 2.762, "F300M": 2.996,
    "F335M": 3.365, "F356W": 3.568, "F360M": 3.621, "F410M": 4.092,
    "F430M": 4.280, "F444W": 4.421, "F460M": 4.624, "F480M": 4.834,
}

# Short-wavelength and long-wavelength NIRCam filters for V-shape check
SW_FILTERS = ["F090W", "F115W", "F150W"]
LW_FILTERS = ["F277W", "F356W", "F410M", "F444W"]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

OUTPUT_DIR = Path.home() / ".jwst-discovery"
INSPECTION_LOG = OUTPUT_DIR / "auto_inspection_log.json"
PHOTOZ_CROSSCHECK = OUTPUT_DIR / "photoz_crosscheck.parquet"
OUTPUT_PARQUET = OUTPUT_DIR / "validated_candidates.parquet"
OUTPUT_REPORT = OUTPUT_DIR / "validation_summary.txt"


# ---------------------------------------------------------------------------
# SED physics checks
# ---------------------------------------------------------------------------

WORKSPACES_DIR = Path.home() / ".jwst-discovery" / "workspaces"


def load_workspace_photometry():
    """Load per-filter magnitudes, chi2, and nearest-neighbor info for all sources.

    Returns dict keyed by (field, id) → {mag_F090W, sn_F090W, ..., chi2, n_bands_sn5,
                                         nearest_sep, nearest_z, nearest_id}
    """
    phot_db = {}
    if not WORKSPACES_DIR.exists():
        return phot_db

    for ws in sorted(WORKSPACES_DIR.iterdir()):
        phot_cat = ws / "catalogs" / "photometry_catalog.parquet"
        char_cat = ws / "catalogs" / "characterized_catalog.parquet"
        final_cat = ws / "catalogs" / "final_catalog.parquet"
        if not phot_cat.exists():
            continue

        field = ws.name
        try:
            pdf = pl.read_parquet(phot_cat)
        except Exception:
            continue

        # Load chi2 from characterized catalog if available
        chi2_map = {}
        z_map = {}
        if char_cat.exists():
            try:
                cdf = pl.read_parquet(char_cat)
                cols_to_select = ["id", "z_phot_chi2"]
                if "z_phot_eazy" in cdf.columns:
                    cols_to_select.append("z_phot_eazy")
                for row in cdf.select(cols_to_select).iter_rows(named=True):
                    sid = int(row["id"])
                    chi2_val = row.get("z_phot_chi2")
                    if chi2_val is not None and isinstance(chi2_val, (int, float)):
                        chi2_map[sid] = float(chi2_val)
                    z_val = row.get("z_phot_eazy")
                    if z_val is not None and isinstance(z_val, (int, float)) and np.isfinite(z_val):
                        z_map[sid] = float(z_val)
            except Exception:
                pass

        # Load RA/Dec + fragment flag from final catalog for neighbor computation
        nearest_map = {}
        if final_cat.exists():
            try:
                fdf = pl.read_parquet(final_cat)
                ids = fdf["id"].to_numpy().astype(np.int64)
                ras = fdf["ra"].to_numpy().astype(np.float64)
                decs = fdf["dec"].to_numpy().astype(np.float64)
                is_frag = (fdf["is_fragment"].to_numpy().astype(bool)
                           if "is_fragment" in fdf.columns else np.zeros(len(fdf), dtype=bool))

                # Only consider non-fragments as potential contaminants
                valid = np.isfinite(ras) & np.isfinite(decs) & ~is_frag
                v_ids = ids[valid]
                v_ra = ras[valid]
                v_dec = decs[valid]

                # For each source, find nearest non-self non-fragment neighbor within 2"
                cos_dec = np.cos(np.radians(np.mean(v_dec))) if len(v_dec) else 1.0
                # Sort by RA for fast lookup
                order = np.argsort(v_ra)
                sra = v_ra[order]
                sdec = v_dec[order]
                sids = v_ids[order]
                ra_tol = 2.5 / 3600.0 / cos_dec

                for idx in range(len(sids)):
                    sid = int(sids[idx])
                    ra0 = sra[idx]
                    dec0 = sdec[idx]
                    best_sep = 999.0
                    best_id = -1
                    # Search forward
                    for j in range(idx + 1, len(sids)):
                        if sra[j] - ra0 > ra_tol:
                            break
                        ddec = abs(sdec[j] - dec0) * 3600.0
                        if ddec > 2.5:
                            continue
                        dra = abs(sra[j] - ra0) * cos_dec * 3600.0
                        sep = np.sqrt(dra * dra + ddec * ddec)
                        if sep < best_sep:
                            best_sep = sep
                            best_id = int(sids[j])
                    # Search backward
                    for j in range(idx - 1, -1, -1):
                        if ra0 - sra[j] > ra_tol:
                            break
                        ddec = abs(sdec[j] - dec0) * 3600.0
                        if ddec > 2.5:
                            continue
                        dra = abs(sra[j] - ra0) * cos_dec * 3600.0
                        sep = np.sqrt(dra * dra + ddec * ddec)
                        if sep < best_sep:
                            best_sep = sep
                            best_id = int(sids[j])
                    if best_id >= 0:
                        nearest_map[sid] = (best_sep, best_id)
            except Exception:
                pass

        # Discover filters in this catalog
        avail_filters = [col[4:] for col in pdf.columns
                         if col.startswith("mag_") and col[4:] in PIVOT_WAVELENGTH_UM]

        for row in pdf.iter_rows(named=True):
            src_id = int(row["id"])
            key = (field, src_id)
            entry = {}

            n_sn5 = 0
            for filt in avail_filters:
                mag_val = row.get(f"mag_{filt}")
                sn_val = row.get(f"sn_{filt}")
                if mag_val is not None and isinstance(mag_val, (int, float)) and np.isfinite(mag_val):
                    entry[f"mag_{filt}"] = float(mag_val)
                if sn_val is not None and isinstance(sn_val, (int, float)) and np.isfinite(sn_val):
                    entry[f"sn_{filt}"] = float(sn_val)
                    if float(sn_val) >= 5:
                        n_sn5 += 1

            entry["n_bands_sn5"] = n_sn5
            if src_id in chi2_map:
                entry["chi2"] = chi2_map[src_id]
            if src_id in nearest_map:
                sep, nid = nearest_map[src_id]
                entry["nearest_sep"] = sep
                entry["nearest_id"] = nid
                entry["nearest_z"] = z_map.get(nid)
            phot_db[key] = entry

    return phot_db


def compute_sed_flags(phot, z_phot, max_sn):
    """Compute SED physics flags for one candidate.

    Returns dict with:
        flat_sed:          True if z>5 but no Balmer break (F200W-F277W < 0.3 mag)
        v_shape_sed:       True if F200W is >0.5 mag fainter than both SW and LW averages
        likely_foreground:  True if z>5 and unrealistically bright (S/N > 150)
        poor_fit:          True if reduced chi2 > 10
        crowded_contam:    True if z>5 with low-z (z<1) neighbor within 1.0"
        balmer_break:      F200W-F277W color (mag) or None
        reduced_chi2:      chi2 / (n_bands - 1) or None
        nearest_sep:       distance to nearest non-fragment neighbor (arcsec) or None
        nearest_z:         redshift of nearest neighbor or None
    """
    flags = {
        "flat_sed": False,
        "v_shape_sed": False,
        "likely_foreground": False,
        "poor_fit": False,
        "crowded_contam": False,
        "low_break_sn": False,
        "balmer_break": None,
        "reduced_chi2": None,
        "nearest_sep": None,
        "nearest_z": None,
    }

    if not phot or z_phot is None or z_phot <= 0:
        return flags

    # Pass through nearest neighbor info
    if "nearest_sep" in phot:
        flags["nearest_sep"] = round(phot["nearest_sep"], 3)
    if "nearest_z" in phot and phot["nearest_z"] is not None:
        flags["nearest_z"] = round(phot["nearest_z"], 3)

    # --- Balmer break check (z > 4.5) ---
    # At z~5, the 4000Å break falls between F200W (2.0µm) and F277W (2.8µm)
    # A genuine z>5 galaxy should show F200W-F277W > 0.3 mag (break makes F200W fainter)
    # Require S/N > 3 in BOTH bands — otherwise the break is comparing noise to signal
    if z_phot > 4.5:
        mag_200 = phot.get("mag_F200W")
        mag_277 = phot.get("mag_F277W")
        sn_200 = phot.get("sn_F200W", 0)
        sn_277 = phot.get("sn_F277W", 0)

        if mag_200 is not None and mag_277 is not None and sn_200 >= 3 and sn_277 >= 3:
            balmer = mag_200 - mag_277  # positive = F200W fainter = break present
            flags["balmer_break"] = round(balmer, 3)
            if balmer < 0.3:
                flags["flat_sed"] = True
        elif sn_200 < 3:
            # F200W is a marginal/non-detection — break unconstrained
            flags["low_break_sn"] = True

    # --- V-shape SED check ---
    # F200W dip: compare F200W mag to average of SW (F090W-F150W) and LW (F277W+)
    # If F200W is >0.5 mag fainter than BOTH averages → suspicious V-shape
    mag_200 = phot.get("mag_F200W")
    sn_200 = phot.get("sn_F200W", 0)
    if mag_200 is not None and sn_200 > 2:
        sw_mags = [phot[f"mag_{f}"] for f in SW_FILTERS
                   if f"mag_{f}" in phot and phot.get(f"sn_{f}", 0) > 2]
        lw_mags = [phot[f"mag_{f}"] for f in LW_FILTERS
                   if f"mag_{f}" in phot and phot.get(f"sn_{f}", 0) > 2]

        if sw_mags and lw_mags:
            sw_avg = np.mean(sw_mags)
            lw_avg = np.mean(lw_mags)
            # F200W fainter than both SW and LW averages → dip
            dip_sw = mag_200 - sw_avg  # positive = F200W fainter
            dip_lw = mag_200 - lw_avg
            if dip_sw > 0.5 and dip_lw > 0.5:
                flags["v_shape_sed"] = True

    # --- Foreground brightness check (z > 5) ---
    # At z>5, even bright galaxies rarely exceed S/N~100 in NIRCam.
    # S/N > 150 with z>5 strongly suggests a foreground interloper.
    if z_phot > 5.0 and max_sn > 150:
        flags["likely_foreground"] = True

    # --- Reduced chi2 check ---
    n_bands = phot.get("n_bands_sn5", 0)
    chi2 = phot.get("chi2")
    if chi2 is not None and n_bands > 1:
        rchi2 = chi2 / (n_bands - 1)
        flags["reduced_chi2"] = round(rchi2, 2)
        if rchi2 > 10:
            flags["poor_fit"] = True

    # --- Crowded contamination check (z>5 with foreground neighbor) ---
    # A z>5 galaxy with a low-z (z<1) neighbor within 1" has likely contaminated
    # photometry — the neighbor's flux leaks into the aperture.
    sep = phot.get("nearest_sep")
    nz = phot.get("nearest_z")
    if z_phot > 5.0 and sep is not None and sep < 1.0 and nz is not None and nz < 1.0:
        flags["crowded_contam"] = True

    return flags


def main():
    parser = argparse.ArgumentParser(description="Consolidate validation results")
    parser.add_argument("--field", type=str, default=None)
    parser.add_argument("--min-z", type=float, default=None)
    parser.add_argument("--max-z", type=float, default=None)
    args = parser.parse_args()

    # Load image inspection results
    if not INSPECTION_LOG.exists():
        print(f"Missing {INSPECTION_LOG} — run auto_inspect_candidates.py first")
        sys.exit(1)

    with open(INSPECTION_LOG) as f:
        inspection = json.load(f)

    # Index by (field, id)
    img_results = {}
    for c in inspection["candidates"]:
        key = (c["field"], c["id"])
        img_results[key] = c

    print(f"Loaded {len(img_results)} image inspection results")

    # Load workspace photometry for SED physics checks
    print("Loading workspace photometry for SED physics checks...")
    phot_db = load_workspace_photometry()
    print(f"Loaded photometry for {len(phot_db)} sources across workspaces")

    # Load photo-z crosscheck results
    has_photoz = PHOTOZ_CROSSCHECK.exists()
    pz_results = {}
    if has_photoz:
        pz_df = pl.read_parquet(PHOTOZ_CROSSCHECK)
        for row in pz_df.iter_rows(named=True):
            key = (row["field"], row["id"])
            pz_results[key] = row
        print(f"Loaded {len(pz_results)} photo-z cross-check results")
    else:
        print("No photo-z crosscheck found — using image analysis only")

    # Merge and assign verdicts
    all_keys = set(img_results.keys()) | set(pz_results.keys())
    results = []

    for key in sorted(all_keys):
        field, src_id = key
        img = img_results.get(key, {})
        pz = pz_results.get(key, {})

        # Filter
        if args.field:
            fkey = args.field.lower().replace(" ", "").replace("_", "").replace("-", "")
            if fkey not in field.lower().replace("-", "").replace("_", ""):
                continue

        z_phot = img.get("z_phot", pz.get("z_pipeline", 0)) or 0
        if args.min_z and z_phot < args.min_z:
            continue
        if args.max_z and z_phot > args.max_z:
            continue

        # Extract key metrics
        img_class = img.get("classification", "UNKNOWN")
        max_sn = img.get("max_sn", pz.get("max_sn", 0)) or 0
        scrutiny = img.get("scrutiny_verdict", pz.get("scrutiny", "UNKNOWN"))
        obj_class = img.get("object_class", pz.get("object_class", "unknown"))

        agreement = pz.get("agreement", "unknown")
        confidence = pz.get("confidence", "unknown")
        dz_norm = pz.get("dz_norm", None)
        z_fsps = pz.get("z_fsps", None)
        z_carnall = pz.get("z_carnall", None)
        odds_fsps = pz.get("odds_fsps", 0) or 0
        odds_carnall = pz.get("odds_carnall", 0) or 0
        multimodal_fsps = pz.get("multimodal_fsps", False)
        multimodal_carnall = pz.get("multimodal_carnall", False)

        # Pipeline-vs-refit dz: catches cases where EAZY says z=6 but
        # both FSPS/Carnall refits say z<1 (refit agreement masks pipeline error)
        dz_pipeline = None
        if z_fsps is not None and z_carnall is not None and z_phot > 0:
            z_refit_mean = (z_fsps + z_carnall) / 2.0
            dz_pipeline = abs(z_phot - z_refit_mean) / (1 + min(z_phot, z_refit_mean))

        # CI width from photo-z crosscheck
        ci_lo_fsps = pz.get("z_lo68_fsps", None)
        ci_hi_fsps = pz.get("z_hi68_fsps", None)
        ci_width = None
        if ci_lo_fsps is not None and ci_hi_fsps is not None:
            ci_width = ci_hi_fsps - ci_lo_fsps

        # Fragment flag from image inspection
        is_fragment = img.get("is_fragment", False)

        # Photo-z quality from pipeline catalog
        z_quality = img.get("z_phot_quality", pz.get("z_phot_quality", "none"))

        # SED physics checks (loaded from workspace photometry)
        phot = phot_db.get(key, {})
        sed_flags = compute_sed_flags(phot, z_phot, max_sn)

        # Assign verdict
        verdict = assign_verdict(
            img_class=img_class,
            agreement=agreement,
            confidence=confidence,
            odds_fsps=odds_fsps,
            odds_carnall=odds_carnall,
            multimodal_fsps=multimodal_fsps,
            multimodal_carnall=multimodal_carnall,
            scrutiny=scrutiny,
            max_sn=max_sn,
            has_photoz=has_photoz and key in pz_results,
            dz_pipeline=dz_pipeline,
            ci_width=ci_width,
            is_fragment=is_fragment,
            z_quality=z_quality,
            z_phot=z_phot,
            flat_sed=sed_flags["flat_sed"],
            v_shape_sed=sed_flags["v_shape_sed"],
            likely_foreground=sed_flags["likely_foreground"],
            poor_fit=sed_flags["poor_fit"],
            crowded_contam=sed_flags["crowded_contam"],
            low_break_sn=sed_flags["low_break_sn"],
        )

        results.append({
            "field": field,
            "id": src_id,
            "ra": img.get("ra", pz.get("ra", 0)),
            "dec": img.get("dec", pz.get("dec", 0)),
            "z_phot": z_phot,
            "z_fsps": z_fsps,
            "z_carnall": z_carnall,
            "dz_norm": dz_norm,
            "dz_pipeline": dz_pipeline,
            "ci_width": ci_width,
            "max_sn": max_sn,
            "object_class": obj_class,
            "scrutiny": scrutiny,
            "image_class": img_class,
            "agreement": agreement,
            "confidence": confidence,
            "odds_fsps": odds_fsps,
            "odds_carnall": odds_carnall,
            "multimodal_fsps": multimodal_fsps,
            "multimodal_carnall": multimodal_carnall,
            "is_fragment": is_fragment,
            "z_phot_quality": z_quality,
            "n_pass": img.get("n_pass", 0),
            "n_warn": img.get("n_warn", 0),
            "n_fail": img.get("n_fail", 0),
            "flat_sed": sed_flags["flat_sed"],
            "v_shape_sed": sed_flags["v_shape_sed"],
            "likely_foreground": sed_flags["likely_foreground"],
            "poor_fit": sed_flags["poor_fit"],
            "crowded_contam": sed_flags["crowded_contam"],
            "low_break_sn": sed_flags["low_break_sn"],
            "balmer_break": sed_flags["balmer_break"],
            "reduced_chi2": sed_flags["reduced_chi2"],
            "nearest_sep": sed_flags["nearest_sep"],
            "nearest_z": sed_flags["nearest_z"],
            "verdict": verdict,
        })

    if not results:
        print("No candidates to consolidate")
        sys.exit(0)

    print(f"\nConsolidated {len(results)} candidates")

    # Ensure consistent types — convert None to NaN for float columns
    float_cols = ["z_phot", "z_fsps", "z_carnall", "dz_norm", "dz_pipeline",
                  "ci_width", "max_sn", "odds_fsps", "odds_carnall",
                  "balmer_break", "reduced_chi2", "nearest_sep", "nearest_z"]
    for r in results:
        for col in float_cols:
            if r.get(col) is None:
                r[col] = float("nan")
            else:
                r[col] = float(r[col])

    # Save parquet
    df = pl.DataFrame(results, infer_schema_length=len(results))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUTPUT_PARQUET)
    print(f"Saved to {OUTPUT_PARQUET}")

    # Generate report
    lines = generate_report(results, has_photoz)
    report = "\n".join(lines)
    print(report)

    with open(OUTPUT_REPORT, "w") as f:
        f.write(report)
    print(f"\nReport saved to {OUTPUT_REPORT}")


def assign_verdict(*, img_class, agreement, confidence, odds_fsps, odds_carnall,
                   multimodal_fsps, multimodal_carnall, scrutiny, max_sn, has_photoz,
                   dz_pipeline=None, ci_width=None, is_fragment=False,
                   z_quality="none", z_phot=0,
                   flat_sed=False, v_shape_sed=False, likely_foreground=False,
                   poor_fit=False, crowded_contam=False, low_break_sn=False):
    """Assign final validation verdict.

    Includes SED physics checks for high-z candidates:
    - flat_sed: z>5 but no Balmer break → likely low-z interloper
    - v_shape_sed: F200W dip relative to both SW and LW → suspicious SED
    - likely_foreground: z>5 but unrealistically bright → foreground object
    - poor_fit: reduced chi2 > 10 → unreliable photo-z
    - crowded_contam: z>5 with low-z neighbor within 1" → contaminated aperture
    - low_break_sn: z>4.5 with F200W S/N < 3 → Balmer break unconstrained
    """

    # REJECTED: image analysis shows artifact/noise/edge/fragment
    if img_class in ("NOISE", "ARTIFACT", "EDGE"):
        return "REJECTED"

    if img_class == "FRAGMENT":
        return "REJECTED"

    # Fragment from proximity dedup → REJECTED
    if is_fragment:
        return "REJECTED"

    # Unreliable photo-z quality (grid-edge, chi2-low, Lyman-fail) → DOWNGRADED
    if z_quality == "unreliable":
        return "DOWNGRADED"

    # --- SED physics checks (high-z specific) ---

    # Likely foreground: z>5 but S/N > 150 → almost certainly a bright foreground object
    if likely_foreground:
        return "DOWNGRADED"

    # Flat SED at z>5: no Balmer break where one is expected → low-z interloper
    if flat_sed:
        return "DOWNGRADED"

    # Poor EAZY fit: reduced chi2 > 10 → photo-z unreliable
    if poor_fit:
        return "NEEDS_SPECTRO"

    # V-shape SED: suspicious dip at F200W → needs spectroscopy to confirm
    if v_shape_sed and z_phot > 4.5:
        return "NEEDS_SPECTRO"

    # Crowded contamination: z>5 with a foreground neighbor within 1"
    # → photometry likely contaminated, needs spectroscopy to confirm
    if crowded_contam:
        return "NEEDS_SPECTRO"

    # Low F200W S/N at z>4.5: Balmer break cannot be validated from noise-level
    # photometry → needs spectroscopy to confirm
    if low_break_sn and z_phot > 4.5:
        return "NEEDS_SPECTRO"

    # --- End SED physics checks ---

    # Pipeline-vs-refit catastrophic disagreement: pipeline says z=X but both
    # refits say something very different → DOWNGRADED
    if dz_pipeline is not None and dz_pipeline > 0.15:
        return "DOWNGRADED"

    # Very wide CI (>3.0) = poorly constrained photo-z → NEEDS_SPECTRO at best
    wide_ci = ci_width is not None and ci_width > 3.0

    # Without photo-z crosscheck, classify based on image + scrutiny
    if not has_photoz:
        if img_class == "REAL_LIKELY" and scrutiny == "EXCELLENT":
            return "LIKELY_REAL"
        elif img_class == "REAL_LIKELY":
            return "LIKELY_REAL"
        elif img_class == "CROWDED":
            return "DOWNGRADED"
        return "NEEDS_SPECTRO"

    # Wide CI caps verdict at NEEDS_SPECTRO
    if wide_ci:
        if img_class in ("REAL_LIKELY", "UNCERTAIN") and agreement in ("excellent", "good"):
            return "NEEDS_SPECTRO"
        return "DOWNGRADED" if img_class in ("CROWDED", "UNCERTAIN") else "NEEDS_SPECTRO"

    # VALIDATED: image=REAL + excellent/good agreement + high confidence
    if img_class == "REAL_LIKELY" and agreement in ("excellent", "good"):
        if confidence == "high":
            return "VALIDATED"
        if confidence == "medium":
            if odds_fsps > 0.8 and odds_carnall > 0.8:
                return "VALIDATED"
            return "LIKELY_REAL"
        # Low confidence but good agreement
        return "LIKELY_REAL"

    # LIKELY_REAL: image=REAL + marginal agreement
    if img_class == "REAL_LIKELY" and agreement == "marginal":
        return "LIKELY_REAL"

    # NEEDS_SPECTRO: image=REAL but photo-z disagree or multimodal
    if img_class == "REAL_LIKELY" and agreement == "disagreement":
        return "NEEDS_SPECTRO"

    if img_class == "REAL_LIKELY" and (multimodal_fsps or multimodal_carnall):
        return "NEEDS_SPECTRO"

    # UNCERTAIN image + any photo-z
    if img_class == "UNCERTAIN":
        if agreement in ("excellent", "good") and confidence in ("high", "medium"):
            return "LIKELY_REAL"
        elif agreement == "disagreement":
            return "DOWNGRADED"
        return "NEEDS_SPECTRO"

    # CROWDED
    if img_class == "CROWDED":
        return "DOWNGRADED"

    return "NEEDS_SPECTRO"


def generate_report(results, has_photoz):
    """Generate text report."""
    lines = []
    lines.append("=" * 80)
    lines.append("  CONSOLIDATED VALIDATION REPORT")
    lines.append("=" * 80)
    lines.append(f"  Date: {time.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"  Total candidates: {len(results):,}")
    lines.append(f"  Photo-z crosscheck: {'Yes' if has_photoz else 'No'}")
    lines.append("")

    # Verdict summary
    verdict_counts = Counter(r["verdict"] for r in results)
    lines.append("  VERDICT DISTRIBUTION:")
    for v in ["VALIDATED", "LIKELY_REAL", "NEEDS_SPECTRO", "DOWNGRADED", "REJECTED"]:
        cnt = verdict_counts.get(v, 0)
        pct = 100 * cnt / max(len(results), 1)
        lines.append(f"    {v:18s} {cnt:6,d}  ({pct:5.1f}%)")

    # Per-field breakdown
    lines.append("")
    lines.append("-" * 90)
    lines.append(f"  {'FIELD':20s} {'TOTAL':>6s} {'VALID':>6s} {'LIKELY':>6s} "
                 f"{'SPECT':>6s} {'DOWN':>6s} {'REJECT':>6s}")
    lines.append("-" * 90)

    field_counts = {}
    for r in results:
        if r["field"] not in field_counts:
            field_counts[r["field"]] = Counter()
        field_counts[r["field"]][r["verdict"]] += 1

    for f in sorted(field_counts.keys()):
        fc = field_counts[f]
        t = sum(fc.values())
        lines.append(f"  {f:20s} {t:6d} {fc.get('VALIDATED',0):6d} "
                     f"{fc.get('LIKELY_REAL',0):6d} {fc.get('NEEDS_SPECTRO',0):6d} "
                     f"{fc.get('DOWNGRADED',0):6d} {fc.get('REJECTED',0):6d}")

    # SED physics flags summary
    n_flat = sum(1 for r in results if r.get("flat_sed"))
    n_vshape = sum(1 for r in results if r.get("v_shape_sed"))
    n_foreground = sum(1 for r in results if r.get("likely_foreground"))
    n_poorfit = sum(1 for r in results if r.get("poor_fit"))
    n_crowded = sum(1 for r in results if r.get("crowded_contam"))
    n_lowbreak = sum(1 for r in results if r.get("low_break_sn"))
    if n_flat + n_vshape + n_foreground + n_poorfit + n_crowded + n_lowbreak > 0:
        lines.append("")
        lines.append("  SED PHYSICS FLAGS:")
        if n_flat:
            lines.append(f"    Flat SED (no Balmer break at z>5):   {n_flat:5d}")
        if n_vshape:
            lines.append(f"    V-shape SED (F200W dip):             {n_vshape:5d}")
        if n_foreground:
            lines.append(f"    Likely foreground (S/N>150 at z>5):  {n_foreground:5d}")
        if n_poorfit:
            lines.append(f"    Poor fit (reduced chi2 > 10):        {n_poorfit:5d}")
        if n_crowded:
            lines.append(f"    Crowded contamination (z>5, fg<1\"):  {n_crowded:5d}")
        if n_lowbreak:
            lines.append(f"    Low F200W S/N (break unconstrained): {n_lowbreak:5d}")

    # High-z breakdown
    hz = [r for r in results if (r.get("z_phot") or 0) > 5]
    lines.append("")
    lines.append("-" * 80)
    lines.append(f"  HIGH-Z CANDIDATES (z > 5): {len(hz)}")
    if hz:
        hz_v = Counter(r["verdict"] for r in hz)
        for v in ["VALIDATED", "LIKELY_REAL", "NEEDS_SPECTRO", "DOWNGRADED", "REJECTED"]:
            if hz_v.get(v, 0) > 0:
                lines.append(f"    {v:18s} {hz_v[v]:5d}")

    # VALIDATED candidates table
    validated = sorted(
        [r for r in results if r["verdict"] == "VALIDATED"],
        key=lambda x: -(x.get("max_sn") or 0)
    )
    if validated:
        lines.append("")
        lines.append("-" * 100)
        lines.append(f"  VALIDATED CANDIDATES ({len(validated)}):")
        lines.append(f"  {'FIELD':20s} {'ID':>7s} {'z':>6s} {'z_f':>6s} {'z_c':>6s} "
                     f"{'dz_n':>6s} {'S/N':>6s} {'Class':12s} {'Agree':>10s}")
        for r in validated[:100]:
            z_str = f"{r['z_phot']:.2f}" if r.get("z_phot") else "---"
            zf = f"{r['z_fsps']:.2f}" if r.get("z_fsps") else "---"
            zc = f"{r['z_carnall']:.2f}" if r.get("z_carnall") else "---"
            dz = f"{r['dz_norm']:.3f}" if r.get("dz_norm") is not None else "---"
            lines.append(f"  {r['field']:20s} {r['id']:7d} {z_str:>6s} {zf:>6s} "
                         f"{zc:>6s} {dz:>6s} {r['max_sn']:6.0f} "
                         f"{r.get('object_class',''):12s} {r.get('agreement',''):>10s}")

    # High-z validated/likely
    hz_good = sorted(
        [r for r in results
         if (r.get("z_phot") or 0) > 5
         and r["verdict"] in ("VALIDATED", "LIKELY_REAL")],
        key=lambda x: -(x.get("max_sn") or 0)
    )
    if hz_good:
        lines.append("")
        lines.append("-" * 100)
        lines.append(f"  VALIDATED/LIKELY at z>5 ({len(hz_good)}):")
        lines.append(f"  {'FIELD':20s} {'ID':>7s} {'z':>6s} {'z_f':>6s} {'z_c':>6s} "
                     f"{'dz_n':>6s} {'S/N':>6s} {'Verdict':15s} {'Agree':>10s}")
        for r in hz_good[:50]:
            z_str = f"{r['z_phot']:.2f}" if r.get("z_phot") else "---"
            zf = f"{r['z_fsps']:.2f}" if r.get("z_fsps") else "---"
            zc = f"{r['z_carnall']:.2f}" if r.get("z_carnall") else "---"
            dz = f"{r['dz_norm']:.3f}" if r.get("dz_norm") is not None else "---"
            lines.append(f"  {r['field']:20s} {r['id']:7d} {z_str:>6s} {zf:>6s} "
                         f"{zc:>6s} {dz:>6s} {r['max_sn']:6.0f} "
                         f"{r['verdict']:15s} {r.get('agreement',''):>10s}")

    return lines


if __name__ == "__main__":
    main()
