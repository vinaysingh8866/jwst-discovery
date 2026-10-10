#!/usr/bin/env python3
"""Generate annotated cutouts for the 2 VALIDATED z>5 candidates showing all
nearby sources with labels."""

import os
import numpy as np
from pathlib import Path
from astropy.io import fits
from astropy.wcs import WCS
from astropy.nddata import Cutout2D
from astropy.coordinates import SkyCoord
import astropy.units as u
from PIL import Image, ImageDraw, ImageFont

import polars as pl

BASE = Path(os.path.expanduser("~/.jwst-discovery/workspaces"))
OUT_DIR = Path(os.path.expanduser("~/.jwst-discovery/tier1_cutouts"))
OUT_DIR.mkdir(exist_ok=True)

CANDIDATES = [
    ("Dragon-Arc", 12519),
    ("Cosmic-Gems", 22045),
]

CUTOUT_SIZE = 81  # larger to include 2.5" radius
SCALE = 8
THUMB = CUTOUT_SIZE * SCALE


def find_fits(field, filt_name):
    raw_dir = BASE / field / "raw"
    if not raw_dir.exists():
        return None
    for f in raw_dir.glob("*.fits"):
        if filt_name.lower() in f.name.lower():
            return f
    return None


def extract_cutout(fits_path, ra, dec, size=CUTOUT_SIZE):
    try:
        with fits.open(fits_path) as hdul:
            data = None
            header = None
            for ext in [1, 0, "SCI"]:
                try:
                    data = hdul[ext].data
                    header = hdul[ext].header
                    if data is not None and data.ndim == 2:
                        break
                except (KeyError, IndexError, TypeError):
                    continue
            if data is None or data.ndim != 2:
                return None, None
            wcs = WCS(header)
            coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg)
            try:
                cutout = Cutout2D(data, coord, size, wcs=wcs, mode="partial", fill_value=0)
                return cutout.data, cutout.wcs
            except Exception:
                return None, None
    except Exception:
        return None, None


def stretch(data):
    if data is None:
        return None
    d = data.copy().astype(float)
    d = np.nan_to_num(d, nan=0, posinf=0, neginf=0)
    med = np.median(d)
    mad = np.median(np.abs(d - med))
    sigma = max(mad * 1.4826, 1e-12)
    d = (d - med) / sigma
    d = np.arcsinh(d * 3) / np.arcsinh(15)
    vmin, vmax = np.percentile(d, [0.5, 99.7])
    if vmax <= vmin:
        vmax = vmin + 1
    d = np.clip((d - vmin) / (vmax - vmin), 0, 1)
    return d


def make_rgb(r_data, g_data, b_data):
    def _z(arr):
        return stretch(arr) if arr is not None else np.zeros((CUTOUT_SIZE, CUTOUT_SIZE))
    r = _z(r_data)
    g = _z(g_data)
    b = _z(b_data)
    if r is None: r = np.zeros((CUTOUT_SIZE, CUTOUT_SIZE))
    if g is None: g = np.zeros((CUTOUT_SIZE, CUTOUT_SIZE))
    if b is None: b = np.zeros((CUTOUT_SIZE, CUTOUT_SIZE))
    rgb = np.stack([r, g, b], axis=-1)
    rgb = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
    return Image.fromarray(rgb)


def main():
    try:
        font_title = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 22)
        font_label = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 14)
        font_small = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 12)
    except Exception:
        font_title = ImageFont.load_default()
        font_label = font_title
        font_small = font_title

    panel_w = THUMB + 20
    panel_h = THUMB + 120  # room for title and legend
    mosaic = Image.new("RGB", (panel_w * len(CANDIDATES) + 20, panel_h + 60), (0, 0, 0))
    mdraw = ImageDraw.Draw(mosaic)
    mdraw.text((10, 10), "Annotated cutouts: target + all neighbors within 2.5\"",
               fill=(240, 240, 250), font=font_title)

    for ci, (field, sid) in enumerate(CANDIDATES):
        x_off = 10 + ci * panel_w
        y_off = 50

        # Load catalog for this field
        cat = pl.read_parquet(BASE / field / "catalogs" / "final_catalog.parquet")
        target = cat.filter(pl.col("id") == sid).row(0, named=True)
        ra0, dec0 = target["ra"], target["dec"]
        z0 = target["z_phot_eazy"]
        sn0 = target["max_sn"]

        # Find neighbors within 2.5"
        ra_arr = cat["ra"].to_numpy()
        dec_arr = cat["dec"].to_numpy()
        ids = cat["id"].to_numpy()
        zs = cat["z_phot_eazy"].to_numpy()
        cosd = np.cos(np.radians(dec0))
        dra = (ra_arr - ra0) * cosd * 3600
        ddec = (dec_arr - dec0) * 3600
        sep = np.sqrt(dra**2 + ddec**2)
        mask = (sep < 2.5)
        neigh_idx = np.where(mask)[0]

        # Extract LW band cutouts (better S/N at z~5)
        filters = {}
        for f in ["F277W", "F356W", "F200W"]:
            fp = find_fits(field, f.lower())
            if fp:
                d, w = extract_cutout(fp, ra0, dec0)
                if d is not None:
                    filters[f] = (d, w)

        r_data = filters.get("F356W", (None, None))[0]
        g_data = filters.get("F277W", (None, None))[0]
        b_data = filters.get("F200W", (None, None))[0]
        wcs = filters.get("F277W", (None, None))[1]

        rgb_img = make_rgb(r_data, g_data, b_data)
        rgb_img = rgb_img.resize((THUMB, THUMB), Image.LANCZOS)
        rdraw = ImageDraw.Draw(rgb_img)

        # Convert source positions to pixel coords in cutout
        if wcs is not None:
            for i in neigh_idx:
                try:
                    x_pix, y_pix = wcs.world_to_pixel(
                        SkyCoord(ra=ra_arr[i] * u.deg, dec=dec_arr[i] * u.deg)
                    )
                    # Map from CUTOUT_SIZE → THUMB
                    px = int(float(x_pix) * SCALE)
                    py = int((CUTOUT_SIZE - 1 - float(y_pix)) * SCALE)  # flip Y for image coords
                    if 0 <= px < THUMB and 0 <= py < THUMB:
                        is_target = (int(ids[i]) == sid)
                        color = (0, 255, 100) if is_target else (
                            (255, 80, 80) if (not np.isfinite(zs[i]) or zs[i] < 1.5) else (255, 200, 80)
                        )
                        size = 22 if is_target else 14
                        # Draw circle
                        rdraw.ellipse([px - size, py - size, px + size, py + size],
                                      outline=color, width=3)
                        # Label
                        z_s = f"z={zs[i]:.2f}" if np.isfinite(zs[i]) else "z=?"
                        label = f"#{int(ids[i])}  {z_s}"
                        rdraw.text((px + size + 4, py - 8), label,
                                   fill=color, font=font_small)
                except Exception:
                    continue

        mosaic.paste(rgb_img, (x_off, y_off))
        mdraw.rectangle([x_off, y_off, x_off + THUMB - 1, y_off + THUMB - 1],
                        outline=(60, 100, 60), width=2)

        # Caption
        title = f"{field}  #{sid}  z={z0:.3f}  S/N={sn0:.0f}"
        mdraw.text((x_off, y_off + THUMB + 12), title, fill=(230, 230, 240), font=font_label)
        mdraw.text((x_off, y_off + THUMB + 34),
                   "green = target,  orange = high-z neighbor,  red = low-z foreground",
                   fill=(180, 180, 190), font=font_small)
        mdraw.text((x_off, y_off + THUMB + 52),
                   f"Cutout: {CUTOUT_SIZE * 0.031:.1f}\" (SW) / {CUTOUT_SIZE * 0.063:.1f}\" (LW) across",
                   fill=(140, 140, 150), font=font_small)

    out_path = Path(os.path.expanduser("~/Desktop/validated_z5_annotated.png"))
    mosaic.save(out_path, dpi=(150, 150))
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
