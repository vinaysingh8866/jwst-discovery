#!/usr/bin/env python3
"""Generate multi-band mosaic for the 4 VALIDATED z>5 candidates that pass all SED physics checks.

Each row = one candidate: label + RGB composite + 8 grayscale filter cutouts.
"""

import os
import numpy as np
from pathlib import Path
from astropy.io import fits
from astropy.wcs import WCS
from astropy.nddata import Cutout2D
from astropy.coordinates import SkyCoord
import astropy.units as u
from PIL import Image, ImageDraw, ImageFont

BASE = Path(os.path.expanduser("~/.jwst-discovery/workspaces"))
OUT_DIR = Path(os.path.expanduser("~/.jwst-discovery/tier1_cutouts"))
OUT_DIR.mkdir(exist_ok=True)

# 2 VALIDATED z>5 candidates (survived all checks: SED physics + neighbor contamination)
VALIDATED = [
    # (field, src_id, ra, dec, z, S/N, n_bands, balmer_break, reduced_chi2, nearest_sep, nearest_z)
    ("Dragon-Arc",  12519, 167.513518,  65.001921, 5.07, 57, 7, 1.05, 1.9, 2.11, 0.40),
    ("Cosmic-Gems", 22045,  93.945020, -57.715747, 5.16, 22, 6, 1.40, 1.7, 1.29, 0.26),
]

ALL_FILTERS = ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"]

CUTOUT_SIZE = 51
SCALE = 6
THUMB = CUTOUT_SIZE * SCALE  # 306


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
                return None
            wcs = WCS(header)
            coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg)
            try:
                cutout = Cutout2D(data, coord, size, wcs=wcs, mode="partial", fill_value=0)
                return cutout.data
            except Exception:
                return None
    except Exception:
        return None


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


def make_grayscale_img(data):
    d = stretch(data)
    if d is None:
        return None
    arr = (d * 255).astype(np.uint8)
    return Image.fromarray(arr, mode="L").convert("RGB")


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


def draw_crosshair(draw, cx, cy, size=18, color=(0, 255, 0)):
    gap = 7
    draw.line([(cx, cy - size), (cx, cy - gap)], fill=color, width=2)
    draw.line([(cx, cy + gap), (cx, cy + size)], fill=color, width=2)
    draw.line([(cx - size, cy), (cx - gap, cy)], fill=color, width=2)
    draw.line([(cx + gap, cy), (cx + size, cy)], fill=color, width=2)


def main():
    print(f"Generating mosaic for {len(VALIDATED)} VALIDATED z>5 candidates...")

    try:
        font_title = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 28)
    except Exception:
        font_title = ImageFont.load_default()
    try:
        font_sub = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 15)
    except Exception:
        font_sub = ImageFont.load_default()
    try:
        font_label = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 16)
    except Exception:
        font_label = ImageFont.load_default()
    try:
        font_filt = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 14)
    except Exception:
        font_filt = font_label
    try:
        font_small = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 12)
    except Exception:
        font_small = font_filt

    label_w = 220
    pad = 6
    header_h = 78
    row_h = THUMB + pad

    n_img_cols = 1 + len(ALL_FILTERS)  # RGB + 8 filters
    mosaic_w = label_w + n_img_cols * (THUMB + pad) + pad
    mosaic_h = header_h + len(VALIDATED) * row_h + pad

    mosaic = Image.new("RGB", (mosaic_w, mosaic_h), (0, 0, 0))
    draw = ImageDraw.Draw(mosaic)

    # Title
    draw.text((pad + 4, 6),
              "2 VALIDATED z > 5 Candidates (all automated checks passed)",
              fill=(240, 240, 250), font=font_title)
    draw.text((pad + 4, 42),
              "Balmer break > 0.7  ·  rChi² < 6  ·  S/N < 150  ·  no V-shape  ·  no fg neighbor <1\"",
              fill=(150, 200, 150), font=font_sub)

    # Column headers
    x0 = label_w
    draw.text((x0 + THUMB // 2 - 16, 56), "RGB", fill=(180, 220, 180), font=font_filt)
    for fi, filt in enumerate(ALL_FILTERS):
        cx = x0 + (fi + 1) * (THUMB + pad)
        draw.text((cx + THUMB // 2 - 26, 56), filt, fill=(160, 160, 170), font=font_filt)

    y_start = header_h

    rgb_map = {
        "R": ["f444w", "f410m", "f356w"],
        "G": ["f277w", "f356w", "f200w"],
        "B": ["f150w", "f115w", "f090w", "f200w"],
    }

    for row_i, (field, sid, ra, dec, z, sn, nb, bb, rc, nsep, nz) in enumerate(VALIDATED):
        y = y_start + row_i * row_h
        print(f"  [{row_i+1}/{len(VALIDATED)}] {field} #{sid} z={z:.3f}...", end=" ")

        # Label block
        draw.text((pad + 2, y + 8), field, fill=(220, 220, 230), font=font_label)
        draw.text((pad + 2, y + 30), f"#{sid}", fill=(180, 180, 190), font=font_label)
        draw.text((pad + 2, y + 56), f"z = {z:.3f}", fill=(110, 210, 255), font=font_label)
        draw.text((pad + 2, y + 80), f"S/N    {sn}", fill=(160, 160, 170), font=font_small)
        draw.text((pad + 2, y + 98), f"bands  {nb}/8", fill=(160, 160, 170), font=font_small)
        draw.text((pad + 2, y + 116), f"break  {bb:.2f}", fill=(150, 220, 150), font=font_small)
        draw.text((pad + 2, y + 134), f"rChi²  {rc:.1f}", fill=(150, 220, 150), font=font_small)
        draw.text((pad + 2, y + 152), f"nbr  {nsep:.1f}\" z={nz:.2f}", fill=(160, 160, 170), font=font_small)
        draw.text((pad + 2, y + 178), "VALIDATED", fill=(100, 255, 140), font=font_sub)

        # Extract all filters
        filter_data = {}
        for filt in ALL_FILTERS:
            fpath = find_fits(field, filt.lower())
            if fpath:
                data = extract_cutout(fpath, ra, dec)
                if data is not None and np.any(data != 0):
                    filter_data[filt] = data

        # RGB composite
        channels = {}
        for ch, prefs in rgb_map.items():
            for filt in prefs:
                if filt.upper() in filter_data:
                    channels[ch] = filter_data[filt.upper()]
                    break

        if channels:
            rgb_img = make_rgb(channels.get("R"), channels.get("G"), channels.get("B"))
        else:
            rgb_img = Image.new("RGB", (CUTOUT_SIZE, CUTOUT_SIZE), (20, 20, 20))

        rgb_img = rgb_img.resize((THUMB, THUMB), Image.LANCZOS)
        rgb_draw = ImageDraw.Draw(rgb_img)
        draw_crosshair(rgb_draw, THUMB // 2, THUMB // 2, size=22)
        mosaic.paste(rgb_img, (label_w, y))
        draw.rectangle([label_w, y, label_w + THUMB - 1, y + THUMB - 1], outline=(60, 100, 60), width=2)

        # Individual filter cutouts (grayscale)
        n_ok = 0
        for fi, filt in enumerate(ALL_FILTERS):
            x = label_w + (fi + 1) * (THUMB + pad)

            if filt in filter_data:
                fimg = make_grayscale_img(filter_data[filt])
                if fimg is not None:
                    fimg = fimg.resize((THUMB, THUMB), Image.LANCZOS)
                    fdraw = ImageDraw.Draw(fimg)
                    draw_crosshair(fdraw, THUMB // 2, THUMB // 2, size=18,
                                   color=(0, 220, 0))
                    mosaic.paste(fimg, (x, y))
                    n_ok += 1
                    draw.rectangle([x, y, x + THUMB - 1, y + THUMB - 1], outline=(45, 45, 50))
                    continue

            # Missing filter
            draw.rectangle([x + 2, y + 2, x + THUMB - 3, y + THUMB - 3],
                           fill=(15, 15, 15), outline=(30, 30, 35))
            draw.text((x + THUMB // 2 - 8, y + THUMB // 2 - 8), "--",
                      fill=(50, 50, 50), font=font_filt)

        print(f"{n_ok}/{len(ALL_FILTERS)} filters")

        # Row separator
        if row_i < len(VALIDATED) - 1:
            draw.line([(pad, y + THUMB + pad // 2),
                       (mosaic_w - pad, y + THUMB + pad // 2)],
                      fill=(35, 35, 45), width=1)

    # Save
    out_path = OUT_DIR / "validated_z5_mosaic.png"
    mosaic.save(out_path, dpi=(150, 150))
    print(f"\nSaved: {out_path}")
    print(f"Size: {mosaic_w} x {mosaic_h} px")

    desktop_path = Path(os.path.expanduser("~/Desktop/validated_z5_mosaic.png"))
    mosaic.save(desktop_path, dpi=(150, 150))
    print(f"Desktop: {desktop_path}")


if __name__ == "__main__":
    main()
