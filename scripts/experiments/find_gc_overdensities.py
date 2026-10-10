"""Hunt for globular-cluster overdensities in JWST workspaces.

Method (CDG-2-style):
  1. Filter final_catalog to compact, point-like, non-MW, red sources
     consistent with old GCs at 10-200 Mpc.
  2. Run DBSCAN on (RA, Dec) to find spatial groupings.
  3. Score each grouping by Poisson significance against field density.
  4. Emit a ranked candidate list per workspace.

This is a v1 catalog-only search. Diffuse-light stacking (to confirm/reject
the "ghost galaxy" behind the GCs) requires re-downloading the FITS images
for any candidate field — done only for survivors.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import polars as pl
from scipy import stats
from sklearn.cluster import DBSCAN

WORKSPACES_ROOT = Path.home() / ".jwst-discovery" / "workspaces"

# ---- Selection cuts for GC candidates ------------------------------------ #
# These target old, metal-poor GCs at 10-200 Mpc. Looser than perfect — we
# rely on the spatial-clustering step to reject random noise.
MAG_BAND = "mag_F150W"        # primary brightness filter
COLOR_BLUE = "color_F115W_F150W"
COLOR_RED  = "color_F150W_F200W"

CUTS = {
    "mag_min":   22.0,        # brighter than 22 = saturated/MW star territory
    "mag_max":   29.0,        # JWST depth limit for compact sources
    "rhalf_max":  3.5,        # px — unresolved/marginally resolved
    "color_blue_min": 0.0,    # F115W-F150W
    "color_blue_max": 1.6,
    "color_red_min": -0.2,    # F150W-F200W
    "color_red_max":  0.8,
}
# Sources flagged as compact/point-like by characterize.py
POINTLIKE_CLASSES = {"star", "ambiguous"}

# ---- DBSCAN parameters --------------------------------------------------- #
# eps in arcsec — typical GC system half-number radius for dwarfs at 10-50 Mpc
# is ~5-30". Use 12" as a balance.
DBSCAN_EPS_ARCSEC = 12.0
DBSCAN_MIN_SAMPLES = 4   # CDG-2 had ~7 GCs; require at least 4 for a candidate

# ---- Significance threshold ---------------------------------------------- #
P_VALUE_MAX = 1e-3


def filter_gc_candidates(cat: pl.DataFrame) -> pl.DataFrame:
    """Apply GC-candidate selection cuts. Returns filtered DataFrame."""
    needed = ["ra", "dec", "r_half_pix", MAG_BAND]
    missing = [c for c in needed if c not in cat.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    df = cat.filter(
        pl.col(MAG_BAND).is_finite()
        & pl.col(MAG_BAND).is_between(CUTS["mag_min"], CUTS["mag_max"])
        & pl.col("r_half_pix").is_finite()
        & (pl.col("r_half_pix") <= CUTS["rhalf_max"])
        & (pl.col("r_half_pix") > 0.5)
    )

    # Require point-like classification from characterize.py
    if "object_class" in df.columns:
        df = df.filter(pl.col("object_class").is_in(list(POINTLIKE_CLASSES)))

    # CRITICAL: real Local-Universe GCs have z=0 (stellar). Reject anything with
    # a finite high-z photometric or spectroscopic redshift fit — those are
    # compact high-z galaxies masquerading as point sources.
    if "best_photo_z" in df.columns:
        df = df.filter(
            pl.col("best_photo_z").is_null()
            | ~pl.col("best_photo_z").is_finite()
            | (pl.col("best_photo_z") < 0.1)
        )
    if "best_spec_z" in df.columns:
        df = df.filter(
            pl.col("best_spec_z").is_null()
            | ~pl.col("best_spec_z").is_finite()
            | (pl.col("best_spec_z") < 0.1)
        )

    # Reject MW stars: anything with a Gaia counterpart is a star.
    if "gaia_match" in df.columns:
        df = df.filter(~pl.col("gaia_match").fill_null(False))

    # Reject mosaic edges and bright-star halos.
    if "is_mosaic_edge" in df.columns:
        df = df.filter(~pl.col("is_mosaic_edge").fill_null(False))
    if "near_bright_star" in df.columns:
        df = df.filter(~pl.col("near_bright_star").fill_null(False))
    if "is_artifact_morph" in df.columns:
        df = df.filter(~pl.col("is_artifact_morph").fill_null(False))
    if "is_spike_artifact" in df.columns:
        df = df.filter(~pl.col("is_spike_artifact").fill_null(False))

    # Color cuts (only if columns exist and are finite)
    if COLOR_BLUE in df.columns:
        df = df.filter(
            pl.col(COLOR_BLUE).is_finite()
            & pl.col(COLOR_BLUE).is_between(CUTS["color_blue_min"], CUTS["color_blue_max"])
        )
    if COLOR_RED in df.columns:
        df = df.filter(
            pl.col(COLOR_RED).is_finite()
            & pl.col(COLOR_RED).is_between(CUTS["color_red_min"], CUTS["color_red_max"])
        )

    # Drop already-known galaxies in NED/SIMBAD with non-stellar types
    if "simbad_otype" in df.columns:
        df = df.filter(
            pl.col("simbad_otype").is_null()
            | ~pl.col("simbad_otype").str.contains("(?i)galax|cluster")
        )

    return df


def estimate_field_area_sqdeg(ra: np.ndarray, dec: np.ndarray) -> float:
    """Estimate field footprint area using bounding-box approximation
    with cos(dec) correction. Underestimates for irregular footprints
    but is fine for relative density (we use the same estimator everywhere).
    """
    if len(ra) < 2:
        return 0.0
    ra_span = float(np.ptp(ra))
    dec_span = float(np.ptp(dec))
    cos_dec = math.cos(math.radians(float(np.median(dec))))
    return ra_span * cos_dec * dec_span


def run_dbscan(ra: np.ndarray, dec: np.ndarray, eps_arcsec: float, min_samples: int) -> np.ndarray:
    """Run DBSCAN with arcsec-scale epsilon. Returns cluster labels (-1 = noise)."""
    if len(ra) == 0:
        return np.array([], dtype=int)
    cos_dec = math.cos(math.radians(float(np.median(dec))))
    # convert to a flat 2D coordinate where 1 unit = 1 arcsec
    x = ra * cos_dec * 3600.0
    y = dec * 3600.0
    coords = np.column_stack([x, y])
    db = DBSCAN(eps=eps_arcsec, min_samples=min_samples).fit(coords)
    return db.labels_


def poisson_pvalue(n_obs: int, expected: float) -> float:
    """Probability of observing >= n_obs sources in a region with given expectation."""
    if expected <= 0 or n_obs <= 0:
        return 1.0
    return float(stats.poisson.sf(n_obs - 1, expected))


def analyze_workspace(ws_dir: Path, verbose: bool = False) -> dict | None:
    """Run GC overdensity search on a single workspace.
    Returns summary dict, or None if catalog missing."""
    cat_path = ws_dir / "catalogs" / "final_catalog.parquet"
    if not cat_path.exists():
        cat_path = ws_dir / "catalogs" / "crossmatch_catalog.parquet"
    if not cat_path.exists():
        return None

    cat = pl.read_parquet(cat_path)
    n_total = cat.height

    try:
        gc_cands = filter_gc_candidates(cat)
    except ValueError as e:
        if verbose:
            print(f"  skip {ws_dir.name}: {e}")
        return None

    n_gc = gc_cands.height
    if n_gc < DBSCAN_MIN_SAMPLES:
        return {
            "field": ws_dir.name,
            "n_total": n_total,
            "n_gc_candidates": n_gc,
            "candidates": [],
            "field_area_sqdeg": 0.0,
            "field_density_per_sqarcmin": 0.0,
        }

    ra = gc_cands["ra"].to_numpy()
    dec = gc_cands["dec"].to_numpy()
    mag = gc_cands[MAG_BAND].to_numpy()

    # Cluster
    labels = run_dbscan(ra, dec, DBSCAN_EPS_ARCSEC, DBSCAN_MIN_SAMPLES)

    # Field density
    area_sqdeg = estimate_field_area_sqdeg(ra, dec)
    area_sqarcmin = area_sqdeg * 3600.0
    density = (n_gc / area_sqarcmin) if area_sqarcmin > 0 else 0.0

    # Per-cluster summary
    candidates = []
    unique_labels = sorted(set(labels) - {-1})
    for lab in unique_labels:
        mask = labels == lab
        n_in = int(mask.sum())
        ra_c = float(np.mean(ra[mask]))
        dec_c = float(np.mean(dec[mask]))
        # Half-number radius (median offset from centroid, arcsec)
        cos_d = math.cos(math.radians(dec_c))
        offsets_arcsec = np.sqrt(
            ((ra[mask] - ra_c) * cos_d * 3600.0) ** 2
            + ((dec[mask] - dec_c) * 3600.0) ** 2
        )
        r_half = float(np.median(offsets_arcsec))
        # Significance: expected count in a circle of r_half_arcsec
        circle_area_sqarcmin = math.pi * (r_half / 60.0) ** 2
        expected = density * circle_area_sqarcmin
        p_val = poisson_pvalue(n_in, expected)
        # Color homogeneity
        color_scatter = float("nan")
        if COLOR_RED in gc_cands.columns:
            colors = gc_cands.filter(pl.Series(mask))[COLOR_RED].to_numpy()
            colors = colors[np.isfinite(colors)]
            if len(colors) >= 2:
                color_scatter = float(np.std(colors))

        # Skip if not significant
        if p_val > P_VALUE_MAX:
            continue

        candidates.append({
            "cluster_id": int(lab),
            "n_members": n_in,
            "ra": ra_c,
            "dec": dec_c,
            "r_half_arcsec": r_half,
            "expected": expected,
            "p_value": p_val,
            "log10_p": float(np.log10(max(p_val, 1e-300))),
            "mag_median": float(np.median(mag[mask])),
            "mag_min": float(np.min(mag[mask])),
            "color_scatter": color_scatter,
        })

    candidates.sort(key=lambda c: c["p_value"])

    return {
        "field": ws_dir.name,
        "n_total": n_total,
        "n_gc_candidates": n_gc,
        "field_area_sqdeg": area_sqdeg,
        "field_density_per_sqarcmin": density,
        "candidates": candidates,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fields", nargs="*", help="Specific workspace names; default = all")
    parser.add_argument("--out", default="gc_overdensity_results.json")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.fields:
        ws_dirs = [WORKSPACES_ROOT / f for f in args.fields]
    else:
        ws_dirs = sorted([d for d in WORKSPACES_ROOT.iterdir() if d.is_dir()])

    print(f"Scanning {len(ws_dirs)} workspaces for GC overdensities...")
    print(f"Cuts: mag {CUTS['mag_min']}-{CUTS['mag_max']}, "
          f"r_half<{CUTS['rhalf_max']}px, object_class in {POINTLIKE_CLASSES}")
    print(f"DBSCAN: eps={DBSCAN_EPS_ARCSEC}\", min_samples={DBSCAN_MIN_SAMPLES}, p_max={P_VALUE_MAX:.0e}")
    print()

    all_results = []
    for ws in ws_dirs:
        result = analyze_workspace(ws, verbose=args.verbose)
        if result is None:
            if args.verbose:
                print(f"  skip {ws.name}: no catalog")
            continue
        n_cand = len(result["candidates"])
        marker = "***" if n_cand > 0 else "   "
        print(f"{marker} {result['field']:25s}  "
              f"GCs={result['n_gc_candidates']:>6d}  "
              f"density={result['field_density_per_sqarcmin']:>5.1f}/arcmin²  "
              f"candidates={n_cand}")
        for c in result["candidates"][:3]:
            print(f"      → cluster {c['cluster_id']}: "
                  f"N={c['n_members']}, "
                  f"r_half={c['r_half_arcsec']:.1f}\", "
                  f"log10(p)={c['log10_p']:.1f}, "
                  f"RA,Dec=({c['ra']:.5f},{c['dec']:.5f}), "
                  f"mag={c['mag_median']:.1f}")
        all_results.append(result)

    # Summary
    n_with_cands = sum(1 for r in all_results if r["candidates"])
    n_total_cands = sum(len(r["candidates"]) for r in all_results)
    print()
    print("=== Summary ===")
    print(f"Fields scanned: {len(all_results)}")
    print(f"Fields with candidates: {n_with_cands}")
    print(f"Total candidate overdensities: {n_total_cands}")

    out_path = Path(args.out)
    out_path.write_text(json.dumps(all_results, indent=2, default=float))
    print(f"\nResults written to {out_path}")


if __name__ == "__main__":
    main()
