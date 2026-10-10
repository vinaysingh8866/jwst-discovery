#!/usr/bin/env python3
"""Multi-template EAZY photo-z cross-check for JWST discovery candidates.

Runs EAZY with two fundamentally different template sets on all EXCELLENT+PROMISING
candidates, then compares P(z) distributions to flag catastrophic outliers.

Template sets:
  Run 1: fsps_full/tweak_fsps_QSF_12_v3 (current pipeline default)
  Run 2: sfhz/carnall_sfhz_13 (Carnall+2020 SFH-z templates)

For each candidate, computes:
  - z_best from each run
  - P(z) from chi2 grid: peaks, 68% CI, ODDS parameter
  - Cross-check agreement metric

Usage:
    python scripts/run_photoz_crosscheck.py                  # All EXCELLENT+PROMISING
    python scripts/run_photoz_crosscheck.py --field El-Gordo  # Single field
    python scripts/run_photoz_crosscheck.py --min-z 5        # High-z only
"""

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import polars as pl

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WORKSPACES_DIR = Path.home() / ".jwst-discovery" / "workspaces"
OUTPUT_DIR = Path.home() / ".jwst-discovery"
OUTPUT_PARQUET = OUTPUT_DIR / "photoz_crosscheck.parquet"
OUTPUT_REPORT = OUTPUT_DIR / "photoz_crosscheck_report.txt"
PLOT_DIR = OUTPUT_DIR / "photoz_plots"

EAZY_FILTER_IDS = {
    "F070W": 362, "F090W": 363, "F115W": 364, "F140M": 368,
    "F150W": 365, "F162M": 369, "F182M": 370, "F200W": 366,
    "F210M": 371, "F250M": 379, "F277W": 375, "F300M": 380,
    "F335M": 381, "F356W": 376, "F360M": 382, "F410M": 383,
    "F430M": 384, "F444W": 377, "F460M": 385, "F480M": 386,
}

PIVOT_WAVELENGTH_UM = {
    "F070W": 0.704, "F090W": 0.901, "F115W": 1.154, "F140M": 1.405,
    "F150W": 1.501, "F162M": 1.627, "F182M": 1.845, "F200W": 1.989,
    "F210M": 2.093, "F250M": 2.503, "F277W": 2.762, "F300M": 2.996,
    "F335M": 3.365, "F356W": 3.568, "F360M": 3.621, "F410M": 4.092,
    "F430M": 4.280, "F444W": 4.421, "F460M": 4.624, "F480M": 4.834,
}

TEMPLATE_CONFIGS = {
    "fsps": {
        "templates": "fsps_full/tweak_fsps_QSF_12_v3.param",
        "temp_err": "TEMPLATE_ERROR.eazy_v1.0",
    },
    "carnall": {
        "templates": "sfhz/carnall_sfhz_13.param",
        "temp_err": "TEMPLATE_ERROR.eazy_v1.0",
    },
}


# ---------------------------------------------------------------------------
# Helper: discover workspaces + filters
# ---------------------------------------------------------------------------

def discover_workspaces():
    workspaces = {}
    for ws in sorted(WORKSPACES_DIR.iterdir()):
        cat = ws / "catalogs" / "final_catalog.parquet"
        if cat.exists():
            workspaces[ws.name] = ws
    return workspaces


def discover_filters(df):
    filters = []
    for col in df.columns:
        if col.startswith("sn_F"):
            filt = col[3:]
            if filt in EAZY_FILTER_IDS:
                filters.append(filt)
    filters.sort(key=lambda f: PIVOT_WAVELENGTH_UM[f])
    return filters


def safe_val(row, col, default=0):
    v = row.get(col)
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return default
    return v


def get_scrutiny_verdicts(df):
    """Quick scrutiny verdict for each source (simplified)."""
    verdicts = {}
    for i in range(len(df)):
        row = df.row(i, named=True)
        src_id = int(row["id"])
        n_fail, n_warn = 0, 0

        z = safe_val(row, "z_phot_eazy", -1)
        if z > 12.85:
            n_fail += 1

        chi2 = safe_val(row, "z_phot_chi2", 0)
        quality = row.get("z_phot_quality", "") or ""
        if chi2 > 100 or quality == "unreliable":
            n_fail += 1
        elif chi2 > 50 or quality == "uncertain":
            n_warn += 1

        n_sn5 = safe_val(row, "n_bands_sn5", 0)
        n_bands = safe_val(row, "n_valid_bands", safe_val(row, "n_bands_covered", 0))
        if n_bands < 3:
            n_fail += 1
        elif n_sn5 < 2:
            n_warn += 1

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
# EAZY runner
# ---------------------------------------------------------------------------

def run_eazy_on_batch(source_ids, flux_dict, err_dict, eazy_filters, template_key, z_grid):
    """Run EAZY on a batch of sources with specified templates.

    Returns: (z_best, chi2_min, chi2_grid) arrays.
    chi2_grid shape: (n_sources, len(z_grid))
    """
    import eazy

    n_sources = len(source_ids)
    eazy_data = os.path.dirname(eazy.__file__) + "/data/eazy-photoz"
    tconf = TEMPLATE_CONFIGS[template_key]

    tmpdir = tempfile.mkdtemp(prefix=f"eazy_{template_key}_")
    cat_file = os.path.join(tmpdir, "photcat.fits")
    translate_file = os.path.join(tmpdir, "zphot.translate")
    param_file = os.path.join(tmpdir, "zphot.param")
    output_dir = os.path.join(tmpdir, "OUTPUT")
    os.makedirs(output_dir, exist_ok=True)

    try:
        from astropy.table import Table

        tbl = Table()
        tbl["id"] = np.arange(1, n_sources + 1, dtype=np.int64)
        tbl["z_spec"] = np.full(n_sources, -1.0)

        translate_lines = ["id  id", "z_spec  z_spec"]
        used_fids = []

        for filt in eazy_filters:
            fid = EAZY_FILTER_IDS[filt]
            fx = flux_dict[filt].copy()
            ex = err_dict[filt].copy()

            # EAZY sentinel: -99 for missing
            fx = np.where(np.isfinite(fx) & (fx > 0), fx, -99.0)
            ex = np.where(np.isfinite(ex) & (ex > 0), ex, -99.0)

            flux_col = f"f_{filt}"
            err_col = f"e_{filt}"
            tbl[flux_col] = fx
            tbl[err_col] = ex
            translate_lines.append(f"{flux_col}  F{fid}")
            translate_lines.append(f"{err_col}  E{fid}")
            used_fids.append(fid)

        tbl.write(cat_file, format="fits", overwrite=True)

        with open(translate_file, "w") as f:
            f.write("\n".join(translate_lines) + "\n")

        # Determine prior filter — use F200W if available, else longest wavelength
        prior_filt = 366  # F200W
        for filt in ["F200W", "F277W", "F356W", "F444W"]:
            if filt in eazy_filters:
                prior_filt = EAZY_FILTER_IDS[filt]
                break

        with open(param_file, "w") as f:
            f.write(f"CATALOG_FILE        {cat_file}\n")
            f.write("MAIN_OUTPUT_FILE    photz\n")
            f.write(f"OUTPUT_DIRECTORY    {output_dir}\n")
            f.write(f"FILTERS_RES         {eazy_data}/filters/FILTER.RES.latest\n")
            f.write(f"TEMPLATES_FILE      {eazy_data}/templates/{tconf['templates']}\n")
            f.write(f"TEMP_ERR_FILE       {eazy_data}/templates/{tconf['temp_err']}\n")
            f.write("TEMPLATE_COMBOS     a\n")
            f.write("Z_MIN               0.01\n")
            f.write("Z_MAX               13.0\n")
            f.write("Z_STEP              0.01\n")
            f.write("Z_STEP_TYPE         1\n")
            f.write("SYS_ERR             0.05\n")
            f.write("APPLY_IGM           y\n")
            f.write("FIX_ZSPEC           n\n")
            f.write("PRIOR_ABZP          25.0\n")
            f.write(f"PRIOR_FILE          {eazy_data}/templates/prior_K_TAO.dat\n")
            f.write(f"PRIOR_FILTER        {prior_filt}\n")
            f.write("CATALOG_FORMAT      FITS\n")

        import multiprocessing as mp
        try:
            mp.set_start_method("fork", force=False)
        except RuntimeError:
            pass

        ez = eazy.photoz.PhotoZ(
            param_file=param_file,
            translate_file=translate_file,
            zeropoint_file=None,
            load_prior=True,
            n_proc=0,
        )
        ez.fit_catalog(n_proc=0)

        z_best = np.atleast_1d(ez.zbest.copy())
        chi2_grid = np.atleast_2d(ez.chi2_fit.copy())  # shape (n_sources, n_z)
        chi2_min = np.min(chi2_grid, axis=1)
        exact_zgrid = ez.zgrid.copy()

        return z_best, chi2_min, chi2_grid, exact_zgrid

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# P(z) analysis
# ---------------------------------------------------------------------------

def analyze_pz(chi2_grid, z_grid):
    """Analyze P(z) from chi2 grid for each source.

    Returns dict of arrays: n_peaks, odds, z_lo68, z_hi68, is_multimodal.
    """
    from scipy.signal import find_peaks

    n_sources = chi2_grid.shape[0]
    n_peaks_arr = np.zeros(n_sources, dtype=int)
    odds_arr = np.zeros(n_sources)
    z_lo68 = np.zeros(n_sources)
    z_hi68 = np.zeros(n_sources)
    is_multimodal = np.zeros(n_sources, dtype=bool)

    for i in range(n_sources):
        chi2 = chi2_grid[i]
        chi2_shifted = chi2 - np.min(chi2)

        # P(z) = exp(-chi2/2), normalized
        log_pz = -chi2_shifted / 2.0
        log_pz -= np.max(log_pz)  # numerical stability
        pz = np.exp(log_pz)
        pz_sum = np.sum(pz)
        if pz_sum > 0:
            pz /= pz_sum
        else:
            pz = np.ones_like(pz) / len(pz)

        # Find peaks
        peaks, props = find_peaks(pz, height=0.01 * np.max(pz), distance=20)
        n_peaks_arr[i] = max(1, len(peaks))

        # Multimodal: second peak >10% of primary
        if len(peaks) >= 2:
            heights = pz[peaks]
            sorted_h = np.sort(heights)[::-1]
            if sorted_h[1] > 0.1 * sorted_h[0]:
                is_multimodal[i] = True

        # ODDS: integral of P(z) within ±0.06*(1+z_peak) of best z
        z_peak = z_grid[np.argmax(pz)]
        dz = 0.06 * (1 + z_peak)
        mask = np.abs(z_grid - z_peak) <= dz
        odds_arr[i] = np.sum(pz[mask])

        # 68% credible interval
        cumsum = np.cumsum(pz)
        lo_idx = np.searchsorted(cumsum, 0.16)
        hi_idx = np.searchsorted(cumsum, 0.84)
        z_lo68[i] = z_grid[min(lo_idx, len(z_grid) - 1)]
        z_hi68[i] = z_grid[min(hi_idx, len(z_grid) - 1)]

    return {
        "n_peaks": n_peaks_arr,
        "odds": odds_arr,
        "z_lo68": z_lo68,
        "z_hi68": z_hi68,
        "is_multimodal": is_multimodal,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Multi-template photo-z cross-check")
    parser.add_argument("--field", type=str, default=None, help="Field name (fuzzy match)")
    parser.add_argument("--min-z", type=float, default=None)
    parser.add_argument("--max-z", type=float, default=None)
    parser.add_argument("--max-per-field", type=int, default=None,
                        help="Max candidates per field (for testing)")
    args = parser.parse_args()

    workspaces = discover_workspaces()
    if not workspaces:
        print("No workspaces found")
        sys.exit(1)

    if args.field:
        key = args.field.lower().replace(" ", "").replace("_", "").replace("-", "")
        matched = {k: v for k, v in workspaces.items()
                   if key in k.lower().replace("-", "").replace("_", "")}
        if not matched:
            print(f"No workspace matching '{args.field}'")
            sys.exit(1)
        workspaces = matched

    print(f"Scanning {len(workspaces)} workspaces...", flush=True)

    # Collect all candidates across all fields
    all_rows = []  # list of dicts with field, id, fluxes, etc.
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

        if len(candidates) == 0:
            continue

        # Get scrutiny verdicts and filter to EXCELLENT+PROMISING
        verdicts = get_scrutiny_verdicts(candidates)
        keep_ids = [sid for sid, v in verdicts.items() if v in ("EXCELLENT", "PROMISING")]
        if not keep_ids:
            continue
        candidates = candidates.filter(pl.col("id").is_in(keep_ids))

        if args.max_per_field and len(candidates) > args.max_per_field:
            candidates = candidates.head(args.max_per_field)

        n = len(candidates)
        eazy_filters = [f for f in filters if f in EAZY_FILTER_IDS]
        if len(eazy_filters) < 3:
            print(f"  {field_name}: skip (only {len(eazy_filters)} EAZY filters)")
            continue

        print(f"  {field_name}: {n} candidates, {len(eazy_filters)} filters", flush=True)

        # Extract fluxes for EAZY (µJy)
        for i in range(n):
            row = candidates.row(i, named=True)
            entry = {
                "field": field_name,
                "id": int(row["id"]),
                "ra": safe_val(row, "ra", 0),
                "dec": safe_val(row, "dec", 0),
                "z_pipeline": safe_val(row, "z_phot_eazy", -1),
                "chi2_pipeline": safe_val(row, "z_phot_chi2", 0),
                "max_sn": safe_val(row, "max_sn", 0),
                "n_bands_sn5": int(safe_val(row, "n_bands_sn5", 0)),
                "object_class": row.get("object_class", "unknown") or "unknown",
                "scrutiny": verdicts.get(int(row["id"]), "UNKNOWN"),
                "eazy_filters": eazy_filters,
            }
            for filt in eazy_filters:
                fcol = f"flux_{filt}"
                ecol = f"fluxerr_{filt}"
                # Convert Jy → µJy
                fx = safe_val(row, fcol, 0) * 1e6
                ex = safe_val(row, ecol, 0) * 1e6
                entry[f"flux_{filt}"] = fx
                entry[f"err_{filt}"] = ex
            all_rows.append(entry)

    if not all_rows:
        print("No candidates to process")
        sys.exit(0)

    print(f"\nTotal: {len(all_rows)} candidates", flush=True)

    # Group by field (same filters within a field) for batch EAZY runs
    from collections import defaultdict
    field_groups = defaultdict(list)
    for row in all_rows:
        field_groups[row["field"]].append(row)

    # Run EAZY with both template sets
    results = {}  # id -> result dict
    for template_key in ["fsps", "carnall"]:
        print(f"\n{'='*60}", flush=True)
        print(f"  EAZY run: {template_key} templates", flush=True)
        print(f"{'='*60}", flush=True)

        for field_name, rows in sorted(field_groups.items()):
            n = len(rows)
            eazy_filters = rows[0]["eazy_filters"]

            print(f"  {field_name}: {n} sources, {len(eazy_filters)} filters...",
                  end="", flush=True)
            t1 = time.time()

            # Build flux/err arrays
            source_ids = [r["id"] for r in rows]
            flux_dict = {}
            err_dict = {}
            for filt in eazy_filters:
                flux_dict[filt] = np.array([r[f"flux_{filt}"] for r in rows])
                err_dict[filt] = np.array([r[f"err_{filt}"] for r in rows])

            try:
                z_best, chi2_min, chi2_grid, z_grid = run_eazy_on_batch(
                    source_ids, flux_dict, err_dict, eazy_filters,
                    template_key, z_grid=None)

                # P(z) analysis
                pz_results = analyze_pz(chi2_grid, z_grid)

                for idx, row in enumerate(rows):
                    rid = row["id"]
                    if rid not in results:
                        results[rid] = {
                            "field": row["field"],
                            "id": rid,
                            "ra": row["ra"],
                            "dec": row["dec"],
                            "z_pipeline": row["z_pipeline"],
                            "chi2_pipeline": row["chi2_pipeline"],
                            "max_sn": row["max_sn"],
                            "n_bands_sn5": row["n_bands_sn5"],
                            "object_class": row["object_class"],
                            "scrutiny": row["scrutiny"],
                        }
                    results[rid][f"z_{template_key}"] = float(z_best[idx])
                    results[rid][f"chi2_{template_key}"] = float(chi2_min[idx])
                    results[rid][f"n_peaks_{template_key}"] = int(pz_results["n_peaks"][idx])
                    results[rid][f"odds_{template_key}"] = float(pz_results["odds"][idx])
                    results[rid][f"z_lo68_{template_key}"] = float(pz_results["z_lo68"][idx])
                    results[rid][f"z_hi68_{template_key}"] = float(pz_results["z_hi68"][idx])
                    results[rid][f"multimodal_{template_key}"] = bool(pz_results["is_multimodal"][idx])

                elapsed = time.time() - t1
                print(f" {elapsed:.0f}s", flush=True)

            except Exception as e:
                print(f" FAILED: {e}", flush=True)
                import traceback
                traceback.print_exc()
                for row in rows:
                    rid = row["id"]
                    if rid not in results:
                        results[rid] = {
                            "field": row["field"], "id": rid,
                            "ra": row["ra"], "dec": row["dec"],
                            "z_pipeline": row["z_pipeline"],
                            "chi2_pipeline": row["chi2_pipeline"],
                            "max_sn": row["max_sn"],
                            "n_bands_sn5": row["n_bands_sn5"],
                            "object_class": row["object_class"],
                            "scrutiny": row["scrutiny"],
                        }
                    results[rid][f"z_{template_key}"] = np.nan
                    results[rid][f"chi2_{template_key}"] = np.nan
                    results[rid][f"n_peaks_{template_key}"] = 0
                    results[rid][f"odds_{template_key}"] = 0.0
                    results[rid][f"z_lo68_{template_key}"] = np.nan
                    results[rid][f"z_hi68_{template_key}"] = np.nan
                    results[rid][f"multimodal_{template_key}"] = False

    # --- Cross-check analysis ---
    print(f"\n{'='*60}", flush=True)
    print("  Computing cross-check metrics...", flush=True)

    for rid, r in results.items():
        z1 = r.get("z_fsps", np.nan)
        z2 = r.get("z_carnall", np.nan)
        z_pipe = r.get("z_pipeline", np.nan)

        # FSPS-vs-Carnall agreement (template cross-check)
        if np.isfinite(z1) and np.isfinite(z2) and min(z1, z2) > 0:
            dz_norm = abs(z1 - z2) / (1 + min(z1, z2))
            r["dz_norm"] = dz_norm

            if dz_norm < 0.02:
                r["agreement"] = "excellent"
            elif dz_norm < 0.05:
                r["agreement"] = "good"
            elif dz_norm < 0.15:
                r["agreement"] = "marginal"
            else:
                r["agreement"] = "disagreement"
        else:
            r["dz_norm"] = np.nan
            r["agreement"] = "unknown"

        # Pipeline-vs-refit dz: catches catastrophic pipeline photo-z errors
        # where EAZY says z=6 but both refits say z<1
        if (np.isfinite(z1) and np.isfinite(z2) and np.isfinite(z_pipe)
                and z_pipe > 0 and min(z1, z2) > 0):
            z_refit_mean = (z1 + z2) / 2.0
            dz_pipeline = abs(z_pipe - z_refit_mean) / (1 + min(z_pipe, z_refit_mean))
            r["dz_pipeline"] = dz_pipeline
        else:
            r["dz_pipeline"] = np.nan

        # CI width (FSPS) — wide CI means poorly constrained
        ci_lo = r.get("z_lo68_fsps", np.nan)
        ci_hi = r.get("z_hi68_fsps", np.nan)
        ci_width = ci_hi - ci_lo if np.isfinite(ci_lo) and np.isfinite(ci_hi) else np.nan
        r["ci_width_fsps"] = ci_width

        # Combined confidence — now incorporates CI width and pipeline agreement
        odds1 = r.get("odds_fsps", 0)
        odds2 = r.get("odds_carnall", 0)
        multi1 = r.get("multimodal_fsps", False)
        multi2 = r.get("multimodal_carnall", False)
        dz_pipe = r.get("dz_pipeline", np.nan)

        confidence = "low"

        # Pipeline catastrophic outlier → always low confidence
        if np.isfinite(dz_pipe) and dz_pipe > 0.15:
            confidence = "low"
        # Wide CI → cap at low-medium
        elif np.isfinite(ci_width) and ci_width > 3.0:
            confidence = "low-medium" if r.get("agreement") in ("excellent", "good") else "low"
        elif r.get("agreement") in ("excellent", "good"):
            if odds1 > 0.9 and odds2 > 0.9 and not multi1 and not multi2:
                confidence = "high"
            elif odds1 > 0.7 and odds2 > 0.7:
                confidence = "medium"
            else:
                confidence = "low-medium"
        elif r.get("agreement") == "marginal":
            confidence = "low-medium"

        r["confidence"] = confidence

    # --- Save results ---
    elapsed = time.time() - t0
    print(f"\nTotal time: {elapsed:.0f}s ({elapsed/60:.1f} min)", flush=True)

    # Convert to dataframe
    rows_list = list(results.values())

    # Ensure all keys exist
    all_keys = set()
    for r in rows_list:
        all_keys.update(r.keys())
    for r in rows_list:
        for k in all_keys:
            if k not in r:
                r[k] = np.nan if k not in ("field", "object_class", "scrutiny",
                                             "agreement", "confidence") else "unknown"

    df_out = pl.DataFrame(rows_list)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df_out.write_parquet(OUTPUT_PARQUET)
    print(f"Saved {len(df_out)} results to {OUTPUT_PARQUET}")

    # --- Generate report ---
    lines = []
    lines.append("=" * 80)
    lines.append("  MULTI-TEMPLATE PHOTO-Z CROSS-CHECK REPORT")
    lines.append("=" * 80)
    lines.append(f"  Date: {time.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"  Total candidates: {len(results):,}")
    lines.append("  Templates: FSPS (tweak_fsps_QSF_12_v3) vs Carnall SFH-z")
    lines.append(f"  Time: {elapsed:.0f}s ({elapsed/60:.1f} min)")
    lines.append("")

    # Agreement summary
    from collections import Counter
    agree_counts = Counter(r.get("agreement", "unknown") for r in results.values())
    conf_counts = Counter(r.get("confidence", "unknown") for r in results.values())

    lines.append("  AGREEMENT DISTRIBUTION:")
    for level in ["excellent", "good", "marginal", "disagreement", "unknown"]:
        cnt = agree_counts.get(level, 0)
        pct = 100 * cnt / max(len(results), 1)
        lines.append(f"    {level:15s} {cnt:6,d}  ({pct:5.1f}%)")

    lines.append("")
    lines.append("  CONFIDENCE DISTRIBUTION:")
    for level in ["high", "medium", "low-medium", "low"]:
        cnt = conf_counts.get(level, 0)
        pct = 100 * cnt / max(len(results), 1)
        lines.append(f"    {level:15s} {cnt:6,d}  ({pct:5.1f}%)")

    # High-z breakdown
    hz = [r for r in results.values() if r.get("z_pipeline", 0) > 5]
    lines.append("")
    lines.append(f"  HIGH-Z CANDIDATES (z_pipeline > 5): {len(hz)}")
    if hz:
        hz_agree = Counter(r.get("agreement") for r in hz)
        hz_conf = Counter(r.get("confidence") for r in hz)
        for level in ["excellent", "good", "marginal", "disagreement"]:
            if hz_agree.get(level, 0) > 0:
                lines.append(f"    Agreement {level}: {hz_agree[level]}")
        for level in ["high", "medium", "low-medium", "low"]:
            if hz_conf.get(level, 0) > 0:
                lines.append(f"    Confidence {level}: {hz_conf[level]}")

    # Top disagreements
    disagreements = sorted(
        [r for r in results.values() if r.get("agreement") == "disagreement"],
        key=lambda x: -x.get("max_sn", 0)
    )
    if disagreements:
        lines.append("")
        lines.append("-" * 80)
        lines.append(f"  TOP DISAGREEMENTS (photo-z mismatch, {len(disagreements)} total):")
        lines.append(f"  {'FIELD':20s} {'ID':>7s} {'z_pipe':>6s} {'z_fsps':>6s} "
                     f"{'z_carn':>6s} {'dz_n':>6s} {'S/N':>6s} {'ODDS_f':>6s} {'ODDS_c':>6s}")
        for r in disagreements[:30]:
            lines.append(f"  {r['field']:20s} {r['id']:7d} "
                         f"{r.get('z_pipeline',0):6.2f} {r.get('z_fsps',0):6.2f} "
                         f"{r.get('z_carnall',0):6.2f} {r.get('dz_norm',0):6.3f} "
                         f"{r.get('max_sn',0):6.0f} "
                         f"{r.get('odds_fsps',0):6.3f} {r.get('odds_carnall',0):6.3f}")

    # High-confidence high-z
    hz_high = sorted(
        [r for r in results.values()
         if r.get("z_pipeline", 0) > 5
         and r.get("confidence") in ("high", "medium")
         and r.get("agreement") in ("excellent", "good")],
        key=lambda x: -x.get("max_sn", 0)
    )
    if hz_high:
        lines.append("")
        lines.append("-" * 80)
        lines.append(f"  HIGH-CONFIDENCE HIGH-Z CANDIDATES ({len(hz_high)}):")
        lines.append(f"  {'FIELD':20s} {'ID':>7s} {'z_pipe':>6s} {'z_fsps':>6s} "
                     f"{'z_carn':>6s} {'dz_n':>6s} {'S/N':>6s} {'Conf':>8s}")
        for r in hz_high[:50]:
            lines.append(f"  {r['field']:20s} {r['id']:7d} "
                         f"{r.get('z_pipeline',0):6.2f} {r.get('z_fsps',0):6.2f} "
                         f"{r.get('z_carnall',0):6.2f} {r.get('dz_norm',0):6.3f} "
                         f"{r.get('max_sn',0):6.0f} {r.get('confidence',''):>8s}")

    report_text = "\n".join(lines)
    print(report_text)

    with open(OUTPUT_REPORT, "w") as f:
        f.write(report_text)
    print(f"\nReport saved to {OUTPUT_REPORT}")


if __name__ == "__main__":
    main()
