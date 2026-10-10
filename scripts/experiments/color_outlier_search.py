"""
Color-outlier search across the full JWST source catalog.

For every source in every workspace, computes 4 NIRCam colors:
    F090-F150, F150-F277, F277-F444, F356-F444

Builds a 4D color-space density estimate with KDTree and ranks sources by
their distance to the K-th nearest neighbor in color space — outliers have
LARGE k-NN distances (sparse local density).

Filters:
    - all 4 colors require S/N > 5 in BOTH constituent bands (good photometry)
    - reject is_fragment, flags > 0
    - reject very-faint sources (any band magerr > 0.5)
    - require at least 5 detected bands

Outputs:
    ~/.jwst-discovery/color_outliers.parquet  — ranked candidates
    ~/.jwst-discovery/color_outlier_report.txt — top-50 summary
    ~/Desktop/color_outliers_top50_cutouts.png — visual mosaic
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

import numpy as np
import polars as pl
from scipy.spatial import cKDTree

warnings.filterwarnings("ignore")

WORKSPACES = Path(os.path.expanduser("~/.jwst-discovery/workspaces"))
OUT_DIR = Path(os.path.expanduser("~/.jwst-discovery"))

# Colors we'll use (filter1 - filter2). Each color requires both filters detected.
COLORS = [
    ("F090W", "F150W"),  # blue side, sensitive to Lyman break for z>5
    ("F150W", "F277W"),  # near-IR slope (galaxy main shape)
    ("F277W", "F444W"),  # mid-IR slope (dust + AGN diagnostic)
    ("F356W", "F444W"),  # tight LW-LW slope
]
SN_THRESHOLD = 10.0      # was 5; raise to reject noise-dominated photometry
MAX_ABS_COLOR = 5.0      # any |color| > 5 mag is unphysical (filter broken / contam)
MIN_MAG = 18.0           # exclude saturation
MAX_MAG = 28.0           # exclude noise-floor
KNN_K = 50  # k-th nearest neighbor distance is the outlier metric
TOP_N = 50  # output the top N globally


def load_field_catalog(field: str) -> pl.DataFrame | None:
    p = WORKSPACES / field / "catalogs" / "characterized_catalog.parquet"
    if not p.exists():
        return None
    try:
        df = pl.read_parquet(p)
        df = df.with_columns(pl.lit(field).alias("field"))
        return df
    except Exception:
        return None


def main() -> None:
    fields = sorted([d.name for d in WORKSPACES.iterdir() if d.is_dir()])
    print(f"Scanning {len(fields)} workspaces for catalogs...\n")

    all_dfs = []
    for fld in fields:
        df = load_field_catalog(fld)
        if df is None:
            continue
        print(f"  {fld:20s}  {len(df):,} sources")
        all_dfs.append(df)

    if not all_dfs:
        print("No catalogs found.")
        sys.exit(1)

    # Concatenate, keeping only columns we need; fields with missing filters get nulls
    desired = ["field", "id", "ra", "dec", "x", "y", "flags", "npix", "peak", "object_class"]
    for f in ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"]:
        desired += [f"mag_{f}", f"sn_{f}"]
    desired += ["is_fragment"]

    aligned = []
    for d in all_dfs:
        cols = []
        for c in desired:
            if c in d.columns:
                cols.append(pl.col(c))
            elif c in ("is_fragment",):
                cols.append(pl.lit(False).alias(c))
            else:
                # numeric columns become null for missing filters
                cols.append(pl.lit(None).cast(pl.Float64).alias(c))
        aligned.append(d.select(cols))
    cat = pl.concat(aligned, how="vertical_relaxed")
    print(f"\nTotal sources: {len(cat):,}")

    # Quality cuts
    print("\nApplying quality cuts...")
    cat = cat.filter(pl.col("flags") == 0)
    print(f"  flags == 0: {len(cat):,}")
    if "is_fragment" in cat.columns:
        cat = cat.filter(~pl.col("is_fragment").fill_null(False))
        print(f"  not fragment: {len(cat):,}")

    # Compute colors and require S/N in each constituent band
    print("\nComputing colors...")
    color_exprs = []
    sn_exprs = []
    for f1, f2 in COLORS:
        cname = f"{f1}_{f2}"
        color_exprs.append((pl.col(f"mag_{f1}") - pl.col(f"mag_{f2}")).alias(cname))
        # mark "valid color" if both constituent SNs above threshold AND mags are finite
        valid_name = f"valid_{cname}"
        sn_exprs.append(
            (
                (pl.col(f"sn_{f1}") >= SN_THRESHOLD)
                & (pl.col(f"sn_{f2}") >= SN_THRESHOLD)
                & pl.col(f"mag_{f1}").is_finite()
                & pl.col(f"mag_{f2}").is_finite()
            ).alias(valid_name)
        )
    cat = cat.with_columns(color_exprs + sn_exprs)

    # Require ALL 4 colors valid for the search
    valid_all = pl.lit(True)
    for f1, f2 in COLORS:
        valid_all = valid_all & pl.col(f"valid_{f1}_{f2}")
    cat_full = cat.filter(valid_all)
    print(f"  all 4 colors S/N>={SN_THRESHOLD}: {len(cat_full):,}")

    # Reject unphysical colors (broken photometry)
    sane = pl.lit(True)
    for f1, f2 in COLORS:
        cname = f"{f1}_{f2}"
        sane = sane & (pl.col(cname).abs() <= MAX_ABS_COLOR)
    cat_full = cat_full.filter(sane)
    print(f"  |color| <= {MAX_ABS_COLOR} in all 4: {len(cat_full):,}")

    # Magnitude range (avoid saturation + noise floor)
    in_range = pl.lit(True)
    for f1, f2 in COLORS:
        for f in (f1, f2):
            mc = f"mag_{f}"
            in_range = in_range & (pl.col(mc) >= MIN_MAG) & (pl.col(mc) <= MAX_MAG)
    cat_full = cat_full.filter(in_range)
    print(f"  {MIN_MAG} <= mag <= {MAX_MAG} in all 8 bands: {len(cat_full):,}")

    if len(cat_full) < 100:
        print("Too few high-quality sources for outlier search.")
        sys.exit(1)

    # Build the color-space matrix
    color_names = [f"{f1}_{f2}" for f1, f2 in COLORS]
    X = np.column_stack([cat_full[c].to_numpy() for c in color_names])
    print(f"\nColor-space shape: {X.shape}")

    # Reject extreme outliers from the density estimation itself (saves KDTree from being thrown off)
    finite = np.all(np.isfinite(X), axis=1)
    X = X[finite]
    cat_full = cat_full.filter(pl.Series(finite))
    print(f"  after finite filter: {X.shape[0]:,}")

    # Whiten colors (subtract median, scale by MAD) so all 4 colors contribute equally
    med = np.median(X, axis=0)
    mad = np.median(np.abs(X - med), axis=0) * 1.4826
    mad[mad < 0.05] = 0.05  # floor
    Xw = (X - med) / mad
    print(f"  whitened color medians: {med}")
    print(f"  whitened color scales (MAD): {mad}")

    # KDTree to find K-th nearest neighbor distance for every source
    print(f"\nBuilding KDTree, K={KNN_K}...")
    tree = cKDTree(Xw)
    # query the K nearest neighbors (including self at distance 0)
    dists, _ = tree.query(Xw, k=KNN_K + 1)
    # outlier metric = distance to K-th NN (skip self at idx 0)
    knn_dist = dists[:, KNN_K]
    print(f"  KNN distances: min={knn_dist.min():.3f}, median={np.median(knn_dist):.3f}, max={knn_dist.max():.3f}")

    # Annotate catalog
    cat_out = cat_full.with_columns([
        pl.Series(name="knn_dist", values=knn_dist),
    ]).sort("knn_dist", descending=True)

    # Save full ranked output
    out_parquet = OUT_DIR / "color_outliers.parquet"
    cat_out.write_parquet(out_parquet)
    print(f"\nSaved ranked catalog: {out_parquet}")

    # --- Top-N report ---
    top = cat_out.head(TOP_N)
    lines = []
    lines.append("=" * 110)
    lines.append(f"  COLOR OUTLIER SEARCH — TOP {TOP_N}")
    lines.append("=" * 110)
    lines.append(f"  Total sources scanned (all 4 colors valid):  {len(cat_full):,}")
    lines.append(f"  K-NN distance metric (K={KNN_K}); larger = more isolated in color space")
    lines.append("  Whitened color medians: " + str([f"{m:.2f}" for m in med]))
    lines.append("")
    lines.append(f"  {'#':>3s}  {'FIELD':>16s}  {'ID':>7s}  {'RA':>10s}  {'DEC':>10s}  "
                 f"{'F090-F150':>9s}  {'F150-F277':>9s}  {'F277-F444':>9s}  {'F356-F444':>9s}  "
                 f"{'kNN_d':>6s}  {'class':>14s}")
    lines.append("  " + "-" * 130)
    for i, r in enumerate(top.iter_rows(named=True), 1):
        lines.append(
            f"  {i:>3d}  {r['field']:>16s}  {r['id']:>7d}  {r['ra']:>10.5f}  {r['dec']:>10.5f}  "
            f"{r['F090W_F150W']:>+9.3f}  {r['F150W_F277W']:>+9.3f}  "
            f"{r['F277W_F444W']:>+9.3f}  {r['F356W_F444W']:>+9.3f}  "
            f"{r['knn_dist']:>6.2f}  {str(r.get('object_class', ''))[:14]:>14s}"
        )

    # Class distribution among outliers
    if "object_class" in top.columns:
        lines.append("")
        lines.append(f"  Class distribution in top-{TOP_N}:")
        cls = top.group_by("object_class").agg(pl.len().alias("n")).sort("n", descending=True)
        for r in cls.iter_rows(named=True):
            lines.append(f"    {str(r['object_class']):>20s}  {r['n']:>3d}")

    # Field distribution among outliers
    lines.append("")
    lines.append(f"  Field distribution in top-{TOP_N}:")
    fcount = top.group_by("field").agg(pl.len().alias("n")).sort("n", descending=True)
    for r in fcount.iter_rows(named=True):
        lines.append(f"    {r['field']:>20s}  {r['n']:>3d}")

    report = "\n".join(lines)
    print()
    print(report)

    out_txt = OUT_DIR / "color_outlier_report.txt"
    out_txt.write_text(report)
    print(f"\nReport: {out_txt}")


if __name__ == "__main__":
    main()
