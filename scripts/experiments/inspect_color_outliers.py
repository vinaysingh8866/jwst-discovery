"""
Generate cutouts and cross-match info for top color-outlier candidates.

For each top candidate:
- Find the F200W and F444W mosaics in its workspace
- Cut out a 100x100 patch centered on the source
- Show the source position, magnitudes, and 4 colors as a label
- Cross-match against existing reference catalogs (UNCOVER, JADES, DJA, ...)
- Output: a multi-panel image of the top candidates + an annotated report
"""

from __future__ import annotations

import argparse
import os
import warnings
from pathlib import Path

import numpy as np
import polars as pl
import matplotlib.pyplot as plt
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.visualization import ZScaleInterval
from astropy.wcs import WCS

warnings.filterwarnings("ignore")

WORKSPACES = Path(os.path.expanduser("~/.jwst-discovery/workspaces"))
REF_CATS = Path(os.path.expanduser("~/.jwst-discovery/reference_catalogs"))


def deduplicate(rows, dedup_arcsec: float = 1.0):
    """Drop near-duplicate sources (sub-arcsec) keeping the first (highest kNN)."""
    out = []
    coords = []
    for r in rows:
        c = SkyCoord(r["ra"] * u.deg, r["dec"] * u.deg)
        dup = False
        for c2 in coords:
            if c.separation(c2).arcsec < dedup_arcsec:
                dup = True
                break
        if not dup:
            out.append(r)
            coords.append(c)
    return out


def find_mosaic(field: str, filt: str) -> Path | None:
    """Find any i2d mosaic for this field+filter (use raw or epochs)."""
    raw = WORKSPACES / field / "raw"
    pattern = f"*nircam*{filt.lower()}*_i2d.fits"
    for d in [raw, WORKSPACES / field / "epochs" / filt.upper()]:
        if d.is_dir():
            matches = sorted(d.glob(pattern))
            if matches:
                return matches[0]
    return None


def cutout_at_radec(path: Path, ra: float, dec: float, half: int = 30) -> np.ndarray | None:
    try:
        with fits.open(path, memmap=True) as h:
            hdu = h["SCI"] if "SCI" in h else h[1] if h[1].data is not None else h[0]
            data = hdu.data
            w = WCS(hdu.header)
        px, py = w.world_to_pixel_values(ra, dec)
        px = float(px); py = float(py)
        ny, nx = data.shape
        if not (0 <= px < nx and 0 <= py < ny):
            return None
        cy, cx = int(round(py)), int(round(px))
        y0 = max(0, cy - half); y1 = min(ny, cy + half + 1)
        x0 = max(0, cx - half); x1 = min(nx, cx + half + 1)
        return data[y0:y1, x0:x1]
    except Exception:
        return None


def crossmatch_catalogs(ra: float, dec: float, field: str) -> str:
    """Search local reference catalogs for nearby cataloged sources."""
    target = SkyCoord(ra * u.deg, dec * u.deg)
    suffix_map = {
        "Abell-2744": ["a2744", "abell2744"],
        "Bullet-Cluster": ["bullet"],
        "MACS-J1423": ["macs1423"],
        "MACS-J1149": ["macs1149"],
        "MACS-J0416": ["macs0416"],
        "RXJ-2129": ["rxj2129"],
        "RXC-J0032": ["rxc0032"],
        "Cosmic-Gems": ["cosmic", "spt0615"],
        "El-Gordo": ["elgordo"],
        "JADES-GOODS-S": ["jades"],
        "Sunrise-Arc": ["whl0137", "sunrise"],
    }
    matches = []
    keys = suffix_map.get(field, [field.lower().replace("-", "")])
    import glob
    for cat_path in glob.glob(str(REF_CATS / "*.parquet")) + glob.glob(str(REF_CATS / "**/*.parquet"), recursive=True):
        name = os.path.basename(cat_path).lower()
        if not any(k in name for k in keys):
            continue
        try:
            df = pl.read_parquet(cat_path)
        except Exception:
            continue
        ra_col = next((c for c in df.columns if c.lower() in ("ra", "ra_deg", "alpha", "alpha_j2000", "raj2000")), None)
        dec_col = next((c for c in df.columns if c.lower() in ("dec", "dec_deg", "delta", "delta_j2000", "dej2000")), None)
        if not (ra_col and dec_col):
            continue
        ras = df[ra_col].to_numpy()
        decs = df[dec_col].to_numpy()
        finite = np.isfinite(ras) & np.isfinite(decs)
        if not finite.any():
            continue
        coords = SkyCoord(ras[finite] * u.deg, decs[finite] * u.deg)
        sep = target.separation(coords).arcsec
        idx = int(np.argmin(sep))
        if sep[idx] < 1.0:
            matches.append(f"{os.path.basename(cat_path).replace('.parquet','')}: {sep[idx]:.2f}\"")
    return "; ".join(matches) if matches else "no catalog match"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--out", default=os.path.expanduser("~/Desktop/color_outliers_top.png"))
    p.add_argument("--input", default=os.path.expanduser("~/.jwst-discovery/color_outliers.parquet"))
    args = p.parse_args()

    df = pl.read_parquet(args.input).head(200)  # take top 200 to dedupe
    rows = list(df.iter_rows(named=True))
    rows = deduplicate(rows, dedup_arcsec=1.0)[: args.top]
    print(f"After dedup: {len(rows)} unique sources (target={args.top})")

    # Visualization: 5 columns (F090W, F150W, F277W, F444W, RGB-like) per row
    n = len(rows)
    fig, axes = plt.subplots(n, 4, figsize=(11, 2.4 * n))
    if n == 1:
        axes = axes.reshape(1, 4)
    z = ZScaleInterval(contrast=0.15)

    cross_lines = []
    for i, r in enumerate(rows):
        f150 = find_mosaic(r["field"], "F150W")
        f277 = find_mosaic(r["field"], "F277W")
        f444 = find_mosaic(r["field"], "F444W")
        f090 = find_mosaic(r["field"], "F090W")

        for j, (path, lbl) in enumerate(zip(
            [f090, f150, f277, f444],
            ["F090W", "F150W", "F277W", "F444W"],
        )):
            ax = axes[i, j]
            ax.set_xticks([]); ax.set_yticks([])
            if path is None:
                ax.text(0.5, 0.5, f"no\n{lbl}", ha="center", va="center", transform=ax.transAxes, fontsize=8)
                continue
            img = cutout_at_radec(path, r["ra"], r["dec"], half=30)
            if img is None or not np.isfinite(img).any():
                ax.text(0.5, 0.5, "out", ha="center", va="center", transform=ax.transAxes, fontsize=8)
                continue
            finite = img[np.isfinite(img)]
            vmin, vmax = z.get_limits(finite) if len(finite) > 5 else (-1, 1)
            ax.imshow(img, origin="lower", cmap="gray", vmin=vmin, vmax=vmax, interpolation="nearest")
            ax.add_patch(plt.Circle((img.shape[1] // 2, img.shape[0] // 2), 5, fill=False, color="lime", lw=1.3))
            ax.set_title(lbl, fontsize=8)
            if j == 0:
                xmatch = crossmatch_catalogs(r["ra"], r["dec"], r["field"])
                ax.set_ylabel(
                    f"#{i+1} {r['field']} id={r['id']}\n"
                    f"RA={r['ra']:.4f}\nDec={r['dec']:.4f}\n"
                    f"k={r['knn_dist']:.2f}\n"
                    f"class={str(r.get('object_class','?'))[:14]}",
                    fontsize=7,
                )
                cross_lines.append(
                    f"#{i+1:2d} {r['field']:>15s} id={r['id']:>6d}  "
                    f"colors=[{r['F090W_F150W']:+.2f}, {r['F150W_F277W']:+.2f}, "
                    f"{r['F277W_F444W']:+.2f}, {r['F356W_F444W']:+.2f}]  "
                    f"class={str(r.get('object_class','?'))[:14]}  "
                    f"xmatch={xmatch}"
                )

    plt.suptitle(f"Top {len(rows)} color-outlier candidates (deduplicated, 4 NIRCam filters)", fontsize=12)
    plt.tight_layout()
    plt.savefig(args.out, dpi=130, bbox_inches="tight")
    print(f"Saved: {args.out}")

    print("\n=== CROSS-MATCH SUMMARY ===")
    for line in cross_lines:
        print(line)
    out_txt = args.out.replace(".png", "_xmatch.txt")
    Path(out_txt).write_text("\n".join(cross_lines))
    print(f"Saved: {out_txt}")


if __name__ == "__main__":
    main()
