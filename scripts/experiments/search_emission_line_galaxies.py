#!/usr/bin/env python3
"""
Systematic search for emission-line galaxy candidates and Lyman-break galaxies
across all JWST Discovery Pipeline catalogs.

Algorithm:
1. Emission-line excess: For each filter, interpolate continuum from adjacent
   filters and flag sources with flux_excess > 3x continuum.
2. Lyman-break galaxies: Sources with complete dropout in short-wavelength
   filters but clear detections in longer wavelengths.
"""

import os
import numpy as np
import polars as pl
from dataclasses import dataclass
from typing import Optional

# ============================================================================
# Filter definitions — pivot wavelengths in microns
# ============================================================================
FILTER_PIVOTS = {
    "F090W": 0.901,
    "F115W": 1.154,
    "F140M": 1.405,
    "F150W": 1.501,
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
    "F444W": 4.421,
    "F480M": 4.834,
}

# Emission lines — rest-frame wavelength in Angstroms
EMISSION_LINES = {
    "Ly-alpha":  1216.0,
    "[OII]":     3727.0,
    "H-beta":    4861.0,
    "[OIII]":    5007.0,
    "H-alpha":   6563.0,
}

# Lyman break wavelength (912 A)
LYMAN_BREAK_A = 912.0

# Catalog paths
BASE = os.path.expanduser("~/.jwst-discovery/workspaces")
FIELDS = [
    "JADES-GOODS-S",
    "Abell-2744",
    "Bullet-Cluster",
    "Cosmic-Gems",
    "El-Gordo",
    "MACS-J0417",
    "SMACS-0723",
    "PLCK-G165",
]


@dataclass
class EmissionLineCandidate:
    field: str
    source_id: int
    ra: float
    dec: float
    excess_filter: str
    excess_ratio: float
    sn: float
    photo_z: Optional[float]
    object_class: str
    novel: bool
    confidence_tier: str
    possible_lines: dict  # line_name -> redshift
    all_fluxes: dict  # filter -> flux (for context)
    all_sn: dict  # filter -> sn


@dataclass
class LBGCandidate:
    field: str
    source_id: int
    ra: float
    dec: float
    break_filter: str  # last non-detected filter (the break position)
    dropout_z_estimate: float
    n_detected_redward: int
    max_sn_redward: float
    photo_z: Optional[float]
    object_class: str
    novel: bool
    confidence_tier: str
    detected_filters: list
    non_detected_filters: list


def get_safe(row, col, default=None):
    """Safely get a value from a polars row dict, handling nulls."""
    val = row.get(col, default)
    if val is None:
        return default
    return val


def search_emission_line_candidates(df, field_name, available_filters):
    """
    Search for emission-line candidates in a catalog.

    For each source, for each filter, interpolate continuum from the two
    nearest adjacent filters and check for excess.
    """
    candidates = []

    # Sort filters by wavelength
    filters_sorted = sorted(available_filters, key=lambda f: FILTER_PIVOTS[f])
    wavelengths = [FILTER_PIVOTS[f] for f in filters_sorted]

    # Pre-extract columns as numpy arrays for speed
    n_sources = df.shape[0]
    ids = df["id"].to_numpy()
    ras = df["ra"].to_numpy()
    decs = df["dec"].to_numpy()

    # Extract flux and S/N arrays
    flux_arrays = {}
    sn_arrays = {}
    for filt in filters_sorted:
        flux_col = f"flux_{filt}"
        sn_col = f"sn_{filt}"
        if flux_col in df.columns:
            flux_arrays[filt] = df[flux_col].to_numpy().astype(np.float64)
            sn_arrays[filt] = df[sn_col].to_numpy().astype(np.float64)

    # Flag arrays
    flag_cols = ["is_artifact_morph", "is_mosaic_edge", "is_fragment", "is_spike_artifact"]
    flag_arrays = {}
    for fc in flag_cols:
        if fc in df.columns:
            flag_arrays[fc] = df[fc].to_numpy()

    # Metadata
    novel_arr = df["novel"].to_numpy() if "novel" in df.columns else np.zeros(n_sources, dtype=bool)
    photo_z_arr = df["best_photo_z"].to_numpy().astype(np.float64) if "best_photo_z" in df.columns else np.full(n_sources, np.nan)
    obj_class_arr = df["object_class"].to_list() if "object_class" in df.columns else ["unknown"] * n_sources
    tier_arr = df["confidence_tier"].to_list() if "confidence_tier" in df.columns else ["unknown"] * n_sources

    for i in range(n_sources):
        # Check artifact flags
        is_artifact = False
        for fc, arr in flag_arrays.items():
            try:
                if arr[i]:
                    is_artifact = True
                    break
            except (TypeError, IndexError):
                pass
        if is_artifact:
            continue

        # Get fluxes and S/N for this source
        fluxes = {}
        sns = {}
        for filt in filters_sorted:
            f_val = flux_arrays[filt][i]
            s_val = sn_arrays[filt][i]
            if not np.isnan(f_val) and not np.isnan(s_val):
                fluxes[filt] = f_val
                sns[filt] = s_val

        # For each filter, check for emission-line excess
        for j, filt in enumerate(filters_sorted):
            if filt not in fluxes:
                continue
            if sns[filt] < 10.0:
                continue
            if fluxes[filt] <= 0:
                continue

            # Find adjacent filters with S/N > 3 for continuum interpolation
            # Look for nearest blueward and redward filters
            left_idx = None
            right_idx = None

            for k in range(j - 1, -1, -1):
                f_k = filters_sorted[k]
                if f_k in sns and sns[f_k] > 3.0 and fluxes.get(f_k, 0) > 0:
                    left_idx = k
                    break

            for k in range(j + 1, len(filters_sorted)):
                f_k = filters_sorted[k]
                if f_k in sns and sns[f_k] > 3.0 and fluxes.get(f_k, 0) > 0:
                    right_idx = k
                    break

            # Need both adjacent filters for interpolation
            if left_idx is None or right_idx is None:
                continue

            left_filt = filters_sorted[left_idx]
            right_filt = filters_sorted[right_idx]

            # Linear interpolation in flux vs wavelength
            w_left = wavelengths[left_idx]
            w_right = wavelengths[right_idx]
            w_center = wavelengths[j]

            f_left = fluxes[left_filt]
            f_right = fluxes[right_filt]

            # Interpolate
            frac = (w_center - w_left) / (w_right - w_left)
            continuum = f_left + frac * (f_right - f_left)

            if continuum <= 0:
                continue

            excess = fluxes[filt] / continuum

            if excess > 3.0:
                # Calculate possible emission lines
                pivot_angstrom = FILTER_PIVOTS[filt] * 1e4  # convert microns to Angstroms
                possible_lines = {}
                for line_name, rest_wave in EMISSION_LINES.items():
                    z = (pivot_angstrom / rest_wave) - 1.0
                    if z > 0:
                        possible_lines[line_name] = round(z, 3)

                pz = photo_z_arr[i]
                if np.isnan(pz):
                    pz = None

                cand = EmissionLineCandidate(
                    field=field_name,
                    source_id=int(ids[i]),
                    ra=float(ras[i]),
                    dec=float(decs[i]),
                    excess_filter=filt,
                    excess_ratio=float(excess),
                    sn=float(sns[filt]),
                    photo_z=pz,
                    object_class=str(obj_class_arr[i]),
                    novel=bool(novel_arr[i]),
                    confidence_tier=str(tier_arr[i]),
                    possible_lines=possible_lines,
                    all_fluxes={f: float(fluxes[f]) for f in filters_sorted if f in fluxes},
                    all_sn={f: float(sns[f]) for f in filters_sorted if f in sns},
                )
                candidates.append(cand)

    return candidates


def search_lbg_candidates(df, field_name, available_filters):
    """
    Search for Lyman-break galaxy candidates.

    Look for sources with complete dropout in short-wavelength filters
    (S/N < 2) but clear detection (S/N > 5) in 3+ redward filters.
    """
    candidates = []

    filters_sorted = sorted(available_filters, key=lambda f: FILTER_PIVOTS[f])

    n_sources = df.shape[0]
    ids = df["id"].to_numpy()
    ras = df["ra"].to_numpy()
    decs = df["dec"].to_numpy()

    sn_arrays = {}
    for filt in filters_sorted:
        sn_col = f"sn_{filt}"
        if sn_col in df.columns:
            sn_arrays[filt] = df[sn_col].to_numpy().astype(np.float64)

    flag_cols = ["is_artifact_morph", "is_mosaic_edge", "is_fragment", "is_spike_artifact"]
    flag_arrays = {}
    for fc in flag_cols:
        if fc in df.columns:
            flag_arrays[fc] = df[fc].to_numpy()

    novel_arr = df["novel"].to_numpy() if "novel" in df.columns else np.zeros(n_sources, dtype=bool)
    photo_z_arr = df["best_photo_z"].to_numpy().astype(np.float64) if "best_photo_z" in df.columns else np.full(n_sources, np.nan)
    obj_class_arr = df["object_class"].to_list() if "object_class" in df.columns else ["unknown"] * n_sources
    tier_arr = df["confidence_tier"].to_list() if "confidence_tier" in df.columns else ["unknown"] * n_sources

    for i in range(n_sources):
        # Check artifact flags
        is_artifact = False
        for fc, arr in flag_arrays.items():
            try:
                if arr[i]:
                    is_artifact = True
                    break
            except (TypeError, IndexError):
                pass
        if is_artifact:
            continue

        # Get S/N for each filter
        sns = {}
        for filt in filters_sorted:
            val = sn_arrays[filt][i]
            if not np.isnan(val):
                sns[filt] = val
            else:
                sns[filt] = 0.0

        # Find the break: consecutive non-detections from the blue end,
        # followed by detections on the red end
        # Try each possible break position
        for break_pos in range(1, len(filters_sorted) - 2):
            # All filters blueward of break must be non-detected (S/N < 2)
            blue_filters = filters_sorted[:break_pos + 1]
            red_filters = filters_sorted[break_pos + 1:]

            all_blue_dropout = all(sns.get(f, 0) < 2.0 for f in blue_filters)
            if not all_blue_dropout:
                continue

            # Count detected redward filters (S/N > 5)
            detected_red = [f for f in red_filters if sns.get(f, 0) > 5.0]
            n_detected = len(detected_red)

            if n_detected < 3:
                continue

            max_sn_red = max(sns.get(f, 0) for f in detected_red)

            # The first detected filter after the break
            first_detected = None
            for f in red_filters:
                if sns.get(f, 0) > 5.0:
                    first_detected = f
                    break

            if first_detected is None:
                continue

            # Estimate dropout redshift from break position
            # Lyman break at 912A observed at the break filter's wavelength
            break_filt = filters_sorted[break_pos]
            break_wavelength_a = FILTER_PIVOTS[break_filt] * 1e4
            first_det_wavelength_a = FILTER_PIVOTS[first_detected] * 1e4

            # z ~ (lambda_break / 912) - 1, using midpoint between last dropout and first detection
            midpoint_a = (break_wavelength_a + first_det_wavelength_a) / 2.0
            z_est = (midpoint_a / LYMAN_BREAK_A) - 1.0

            if z_est < 3.0:  # LBGs are typically z > 3
                continue

            pz = photo_z_arr[i]
            if np.isnan(pz):
                pz = None

            non_det_filters = [f for f in blue_filters if sns.get(f, 0) < 2.0]

            cand = LBGCandidate(
                field=field_name,
                source_id=int(ids[i]),
                ra=float(ras[i]),
                dec=float(decs[i]),
                break_filter=break_filt,
                dropout_z_estimate=round(z_est, 2),
                n_detected_redward=n_detected,
                max_sn_redward=float(max_sn_red),
                photo_z=pz,
                object_class=str(obj_class_arr[i]),
                novel=bool(novel_arr[i]),
                confidence_tier=str(tier_arr[i]),
                detected_filters=[f for f in red_filters if sns.get(f, 0) > 5.0],
                non_detected_filters=non_det_filters,
            )
            candidates.append(cand)
            break  # Only report the highest-z break for each source

    return candidates


def print_emission_line_results(all_candidates):
    """Print top emission-line candidates sorted by excess ratio."""

    # Separate novel and non-novel
    novel = [c for c in all_candidates if c.novel]
    known = [c for c in all_candidates if not c.novel]

    # Sort by excess ratio
    novel.sort(key=lambda c: c.excess_ratio, reverse=True)
    known.sort(key=lambda c: c.excess_ratio, reverse=True)

    print("=" * 120)
    print("EMISSION-LINE GALAXY CANDIDATES")
    print("=" * 120)
    print(f"\nTotal candidates found: {len(all_candidates)}")
    print(f"  Novel (not in reference catalogs): {len(novel)}")
    print(f"  Known (matched to references):     {len(known)}")

    # Field breakdown
    from collections import Counter
    field_counts = Counter(c.field for c in all_candidates)
    novel_field_counts = Counter(c.field for c in novel)
    print("\nPer-field breakdown:")
    print(f"  {'Field':<20} {'Total':>8} {'Novel':>8}")
    print(f"  {'-'*20} {'-'*8} {'-'*8}")
    for f in FIELDS:
        print(f"  {f:<20} {field_counts.get(f, 0):>8} {novel_field_counts.get(f, 0):>8}")

    # --- Top 30 NOVEL candidates ---
    print("\n" + "=" * 120)
    print("TOP 30 NOVEL EMISSION-LINE CANDIDATES (sorted by excess ratio)")
    print("=" * 120)

    for rank, c in enumerate(novel[:30], 1):
        print(f"\n--- #{rank} ---")
        print(f"  Field: {c.field}  |  Source ID: {c.source_id}  |  RA: {c.ra:.6f}  Dec: {c.dec:.6f}")
        print(f"  Excess filter: {c.excess_filter}  |  Excess ratio: {c.excess_ratio:.2f}x  |  S/N: {c.sn:.1f}")
        print(f"  Photo-z: {c.photo_z if c.photo_z is not None else 'N/A'}  |  Class: {c.object_class}  |  Tier: {c.confidence_tier}")
        print(f"  Novel: {c.novel}")

        # Possible emission lines
        print(f"  Possible emission lines at filter {c.excess_filter} ({FILTER_PIVOTS[c.excess_filter]:.3f} um):")
        for line_name, z in sorted(c.possible_lines.items(), key=lambda x: x[1]):
            marker = ""
            if c.photo_z is not None and abs(z - c.photo_z) < 0.3:
                marker = " <-- CONSISTENT WITH PHOTO-Z"
            print(f"    {line_name:>10}: z = {z:.3f}{marker}")

        # Show SED context
        print("  SED (S/N): ", end="")
        for filt in sorted(c.all_sn.keys(), key=lambda f: FILTER_PIVOTS[f]):
            sn_val = c.all_sn[filt]
            marker = " ***" if filt == c.excess_filter else ""
            print(f"{filt}={sn_val:.1f}{marker}  ", end="")
        print()

    # --- Top 30 ALL candidates (including known) ---
    all_sorted = sorted(all_candidates, key=lambda c: c.excess_ratio, reverse=True)
    print("\n" + "=" * 120)
    print("TOP 30 OVERALL EMISSION-LINE CANDIDATES (including known sources)")
    print("=" * 120)

    for rank, c in enumerate(all_sorted[:30], 1):
        print(f"\n--- #{rank} ---")
        print(f"  Field: {c.field}  |  Source ID: {c.source_id}  |  RA: {c.ra:.6f}  Dec: {c.dec:.6f}")
        print(f"  Excess filter: {c.excess_filter}  |  Excess ratio: {c.excess_ratio:.2f}x  |  S/N: {c.sn:.1f}")
        print(f"  Photo-z: {c.photo_z if c.photo_z is not None else 'N/A'}  |  Class: {c.object_class}  |  Tier: {c.confidence_tier}")
        print(f"  Novel: {c.novel}")

        print(f"  Possible emission lines at filter {c.excess_filter} ({FILTER_PIVOTS[c.excess_filter]:.3f} um):")
        for line_name, z in sorted(c.possible_lines.items(), key=lambda x: x[1]):
            marker = ""
            if c.photo_z is not None and abs(z - c.photo_z) < 0.3:
                marker = " <-- CONSISTENT WITH PHOTO-Z"
            print(f"    {line_name:>10}: z = {z:.3f}{marker}")


def print_lbg_results(all_lbg):
    """Print top LBG candidates."""

    novel = [c for c in all_lbg if c.novel]
    known = [c for c in all_lbg if not c.novel]

    # Sort by estimated redshift (highest first)
    novel.sort(key=lambda c: c.dropout_z_estimate, reverse=True)
    known.sort(key=lambda c: c.dropout_z_estimate, reverse=True)

    print("\n\n" + "=" * 120)
    print("LYMAN-BREAK GALAXY (LBG) / DROPOUT CANDIDATES")
    print("=" * 120)
    print(f"\nTotal LBG candidates found: {len(all_lbg)}")
    print(f"  Novel: {len(novel)}")
    print(f"  Known: {len(known)}")

    from collections import Counter
    field_counts = Counter(c.field for c in all_lbg)
    novel_field_counts = Counter(c.field for c in novel)
    print("\nPer-field breakdown:")
    print(f"  {'Field':<20} {'Total':>8} {'Novel':>8}")
    print(f"  {'-'*20} {'-'*8} {'-'*8}")
    for f in FIELDS:
        print(f"  {f:<20} {field_counts.get(f, 0):>8} {novel_field_counts.get(f, 0):>8}")

    # Redshift distribution
    z_bins = [(3, 5), (5, 7), (7, 9), (9, 12), (12, 20)]
    print("\nRedshift distribution (all LBG candidates):")
    for z_lo, z_hi in z_bins:
        n = sum(1 for c in all_lbg if z_lo <= c.dropout_z_estimate < z_hi)
        n_novel = sum(1 for c in novel if z_lo <= c.dropout_z_estimate < z_hi)
        print(f"  z = {z_lo}-{z_hi}: {n} total, {n_novel} novel")

    # --- Top 20 NOVEL LBG candidates ---
    print("\n" + "=" * 120)
    print("TOP 20 NOVEL LBG CANDIDATES (sorted by dropout redshift, highest first)")
    print("=" * 120)

    for rank, c in enumerate(novel[:20], 1):
        print(f"\n--- #{rank} ---")
        print(f"  Field: {c.field}  |  Source ID: {c.source_id}  |  RA: {c.ra:.6f}  Dec: {c.dec:.6f}")
        print(f"  Break at: {c.break_filter}  |  Dropout z ~ {c.dropout_z_estimate:.2f}")
        print(f"  Photo-z: {c.photo_z if c.photo_z is not None else 'N/A'}  |  Class: {c.object_class}  |  Tier: {c.confidence_tier}")
        print(f"  Novel: {c.novel}")
        print(f"  Non-detected (S/N<2): {', '.join(c.non_detected_filters)}")
        print(f"  Detected (S/N>5):     {', '.join(c.detected_filters)} ({c.n_detected_redward} filters, max S/N={c.max_sn_redward:.1f})")
        if c.photo_z is not None:
            delta = abs(c.dropout_z_estimate - c.photo_z)
            agree = "YES" if delta < 1.0 else "NO"
            print(f"  Photo-z agreement: dropout z={c.dropout_z_estimate:.2f} vs photo-z={c.photo_z:.2f} (delta={delta:.2f}) -> {agree}")

    # --- Top 20 ALL LBG candidates ---
    all_sorted = sorted(all_lbg, key=lambda c: c.dropout_z_estimate, reverse=True)
    print("\n" + "=" * 120)
    print("TOP 20 OVERALL LBG CANDIDATES (including known, highest z first)")
    print("=" * 120)

    for rank, c in enumerate(all_sorted[:20], 1):
        print(f"\n--- #{rank} ---")
        print(f"  Field: {c.field}  |  Source ID: {c.source_id}  |  RA: {c.ra:.6f}  Dec: {c.dec:.6f}")
        print(f"  Break at: {c.break_filter}  |  Dropout z ~ {c.dropout_z_estimate:.2f}")
        print(f"  Photo-z: {c.photo_z if c.photo_z is not None else 'N/A'}  |  Class: {c.object_class}  |  Tier: {c.confidence_tier}")
        print(f"  Novel: {c.novel}")
        print(f"  Non-detected: {', '.join(c.non_detected_filters)}")
        print(f"  Detected:     {', '.join(c.detected_filters)} ({c.n_detected_redward} filters, max S/N={c.max_sn_redward:.1f})")
        if c.photo_z is not None:
            delta = abs(c.dropout_z_estimate - c.photo_z)
            agree = "YES" if delta < 1.0 else "NO"
            print(f"  Photo-z agreement: dropout z={c.dropout_z_estimate:.2f} vs photo-z={c.photo_z:.2f} (delta={delta:.2f}) -> {agree}")


def main():
    all_emission_candidates = []
    all_lbg_candidates = []

    for field_name in FIELDS:
        path = os.path.join(BASE, field_name, "catalogs/final_catalog.parquet")
        if not os.path.exists(path):
            print(f"[SKIP] {field_name}: catalog not found")
            continue

        print(f"[PROCESSING] {field_name}...")
        df = pl.read_parquet(path)

        # Determine available filters
        available_filters = []
        for filt in FILTER_PIVOTS:
            if f"flux_{filt}" in df.columns and f"sn_{filt}" in df.columns:
                available_filters.append(filt)

        print(f"  Sources: {df.shape[0]}, Filters: {available_filters}")

        # Search for emission-line candidates
        em_cands = search_emission_line_candidates(df, field_name, available_filters)
        print(f"  Emission-line candidates: {len(em_cands)} (novel: {sum(1 for c in em_cands if c.novel)})")
        all_emission_candidates.extend(em_cands)

        # Search for LBG candidates
        lbg_cands = search_lbg_candidates(df, field_name, available_filters)
        print(f"  LBG candidates: {len(lbg_cands)} (novel: {sum(1 for c in lbg_cands if c.novel)})")
        all_lbg_candidates.extend(lbg_cands)

    print("\n")

    # Print results
    print_emission_line_results(all_emission_candidates)
    print_lbg_results(all_lbg_candidates)

    # Summary statistics
    print("\n\n" + "=" * 120)
    print("SUMMARY")
    print("=" * 120)
    print(f"Total emission-line candidates: {len(all_emission_candidates)}")
    print(f"  Novel: {sum(1 for c in all_emission_candidates if c.novel)}")
    print(f"Total LBG candidates: {len(all_lbg_candidates)}")
    print(f"  Novel: {sum(1 for c in all_lbg_candidates if c.novel)}")

    # Most common excess filters
    from collections import Counter
    filter_counts = Counter(c.excess_filter for c in all_emission_candidates)
    print("\nEmission-line excess by filter:")
    for filt, count in filter_counts.most_common():
        novel_count = sum(1 for c in all_emission_candidates if c.excess_filter == filt and c.novel)
        print(f"  {filt}: {count} total ({novel_count} novel)")

    # Cross-check: novel candidates with photo-z consistent with a line
    print("\nNovel candidates with photo-z consistent with an emission line:")
    novel_em = [c for c in all_emission_candidates if c.novel and c.photo_z is not None]
    consistent = 0
    for c in novel_em:
        for line_name, z in c.possible_lines.items():
            if abs(z - c.photo_z) < 0.3:
                consistent += 1
                break
    print(f"  {consistent} / {len(novel_em)} novel candidates with photo-z have line-z agreement")


if __name__ == "__main__":
    main()
