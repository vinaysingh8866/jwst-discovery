#!/usr/bin/env python3
"""Generate multi-band cutout mosaic for 14 Tier 1 z>5 VALIDATED candidates.

Each row = one candidate: info label + RGB + 8 grayscale filter cutouts.
Standard astro style: white source on dark background.
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

TIER1 = [
    ("Bullet-Cluster",  7797, 104.599567, -55.943814, 5.415, 234, 8),
    ("RXC-J0032",      29588,   8.033949,  18.208796, 5.186,  69, 6),
    ("Bullet-Cluster", 16864, 104.581943, -55.956732, 5.164,  24, 7),
    ("Bullet-Cluster", 18499, 104.582910, -55.949063, 5.163,  35, 7),
    ("Bullet-Cluster",  1711, 104.617054, -55.967790, 5.159,  55, 8),
    ("RXC-J0600",       6810,  90.021416, -20.148456, 5.158,  37, 5),
    ("Bullet-Cluster",   273, 104.657418, -55.977000, 5.157,  20, 6),
    ("RXC-J0600",       1015,  90.078524, -20.181524, 5.156,  47, 6),
    ("Bullet-Cluster",  2495, 104.658979, -55.963564, 5.148,  24, 7),
    ("Bullet-Cluster", 19360, 104.668603, -55.945214, 5.129,  38, 8),
    ("Dragon-Arc",     12519, 167.513518,  65.001921, 5.068,  57, 7),
    ("RXC-J2211",       7942, 332.936174,  -3.780367, 5.055, 286, 7),
    ("Bullet-Cluster",  5107, 104.654132, -55.953358, 5.026,  33, 7),
    ("Bullet-Cluster",  5205, 104.654640, -55.953102, 5.021,  33, 6),
]

ALL_FILTERS = ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"]

CUTOUT_SIZE = 51
SCALE = 4
THUMB = CUTOUT_SIZE * SCALE  # 204


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
            for ext in [1, 0, "SCI"]:
                try:
                    data = hdul[ext].data
                    header = hdul[ext].header
                    if data is not None and data.ndim == 2:
                        break
                except (KeyError, IndexError, TypeError):
                    continue
            else:
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
    """Asinh stretch → 0-1 array. Returns None if data is None."""
    if data is None:
        return None
    d = data.copy().astype(float)
    d = np.nan_to_num(d, nan=0, posinf=0, neginf=0)
    med = np.median(d)
    mad = np.median(np.abs(d - med))
    sigma = max(mad * 1.4826, 1e-12)
    d = (d - med) / sigma
    # Asinh with moderate softening
    d = np.arcsinh(d * 3) / np.arcsinh(15)
    vmin, vmax = np.percentile(d, [0.5, 99.7])
    if vmax <= vmin:
        vmax = vmin + 1
    d = np.clip((d - vmin) / (vmax - vmin), 0, 1)
    return d


def make_grayscale_img(data):
    """Single band → grayscale PIL Image (white on black)."""
    d = stretch(data)
    if d is None:
        return None
    arr = (d * 255).astype(np.uint8)
    return Image.fromarray(arr, mode="L").convert("RGB")


def make_rgb(r_data, g_data, b_data):
    """3-channel → RGB PIL Image."""
    r = stretch(r_data) if r_data is not None else np.zeros((CUTOUT_SIZE, CUTOUT_SIZE))
    g = stretch(g_data) if g_data is not None else np.zeros((CUTOUT_SIZE, CUTOUT_SIZE))
    b = stretch(b_data) if b_data is not None else np.zeros((CUTOUT_SIZE, CUTOUT_SIZE))
    if r is None: r = np.zeros((CUTOUT_SIZE, CUTOUT_SIZE))
    if g is None: g = np.zeros((CUTOUT_SIZE, CUTOUT_SIZE))
    if b is None: b = np.zeros((CUTOUT_SIZE, CUTOUT_SIZE))
    rgb = np.stack([r, g, b], axis=-1)
    rgb = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
    return Image.fromarray(rgb)


def draw_crosshair(draw, cx, cy, size=14, color=(0, 255, 0)):
    """Green crosshair with gap in center."""
    gap = 5
    draw.line([(cx, cy - size), (cx, cy - gap)], fill=color, width=1)
    draw.line([(cx, cy + gap), (cx, cy + size)], fill=color, width=1)
    draw.line([(cx - size, cy), (cx - gap, cy)], fill=color, width=1)
    draw.line([(cx + gap, cy), (cx + size, cy)], fill=color, width=1)


def main():
    print(f"Generating multi-band mosaic for {len(TIER1)} candidates...")

    # Fonts
    try:
        font_title = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
    except Exception:
        font_title = ImageFont.load_default()
    try:
        font_label = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 13)
    except Exception:
        font_label = ImageFont.load_default()
    try:
        font_filt = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 12)
    except Exception:
        font_filt = font_label
    try:
        font_small = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 10)
    except Exception:
        font_small = font_filt

    # Layout
    label_w = 180
    pad = 4
    header_h = 50  # title + filter labels
    row_h = THUMB + pad

    n_img_cols = 1 + len(ALL_FILTERS)  # RGB + 8 filters
    mosaic_w = label_w + n_img_cols * (THUMB + pad) + pad
    mosaic_h = header_h + len(TIER1) * row_h + pad

    mosaic = Image.new("RGB", (mosaic_w, mosaic_h), (0, 0, 0))
    draw = ImageDraw.Draw(mosaic)

    # Title
    draw.text((pad + 2, 4),
              "14 Tier-1 Validated z > 5 Candidates",
              fill=(230, 230, 240), font=font_title)

    # Column headers
    x0 = label_w
    draw.text((x0 + THUMB // 2 - 12, 30), "RGB", fill=(180, 220, 180), font=font_filt)
    for fi, filt in enumerate(ALL_FILTERS):
        cx = x0 + (fi + 1) * (THUMB + pad)
        draw.text((cx + THUMB // 2 - 20, 30), filt, fill=(160, 160, 170), font=font_filt)

    y_start = header_h

    rgb_map = {
        "R": ["f444w", "f410m", "f356w"],
        "G": ["f277w", "f356w", "f200w"],
        "B": ["f150w", "f115w", "f090w", "f200w"],
    }

    for row_i, (field, sid, ra, dec, z, sn, nb) in enumerate(TIER1):
        y = y_start + row_i * row_h
        print(f"  [{row_i+1:2d}/14] {field} #{sid} z={z:.3f}...", end=" ")

        # Label
        draw.text((pad, y + 6), field, fill=(200, 200, 210), font=font_label)
        draw.text((pad, y + 24), f"#{sid}", fill=(170, 170, 180), font=font_label)
        draw.text((pad, y + 44), f"z = {z:.3f}", fill=(100, 200, 255), font=font_label)
        draw.text((pad, y + 64), f"S/N {sn}", fill=(150, 150, 160), font=font_small)
        draw.text((pad, y + 78), f"{nb} bands", fill=(130, 130, 140), font=font_small)

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
        draw_crosshair(rgb_draw, THUMB // 2, THUMB // 2)
        mosaic.paste(rgb_img, (label_w, y))
        draw.rectangle([label_w, y, label_w + THUMB - 1, y + THUMB - 1], outline=(40, 60, 40))

        # Individual filter cutouts (grayscale)
        n_ok = 0
        for fi, filt in enumerate(ALL_FILTERS):
            x = label_w + (fi + 1) * (THUMB + pad)

            if filt in filter_data:
                fimg = make_grayscale_img(filter_data[filt])
                if fimg is not None:
                    fimg = fimg.resize((THUMB, THUMB), Image.LANCZOS)
                    fdraw = ImageDraw.Draw(fimg)
                    draw_crosshair(fdraw, THUMB // 2, THUMB // 2, size=12,
                                   color=(0, 200, 0))
                    mosaic.paste(fimg, (x, y))
                    n_ok += 1
                    draw.rectangle([x, y, x + THUMB - 1, y + THUMB - 1], outline=(35, 35, 40))
                    continue

            # Missing filter
            draw.rectangle([x + 2, y + 2, x + THUMB - 3, y + THUMB - 3],
                           fill=(15, 15, 15), outline=(25, 25, 30))
            draw.text((x + THUMB // 2 - 6, y + THUMB // 2 - 6), "--",
                      fill=(40, 40, 40), font=font_filt)

        print(f"{n_ok}/{len(ALL_FILTERS)} filters")

        # Subtle row separator
        if row_i < len(TIER1) - 1:
            draw.line([(label_w, y + THUMB + pad // 2),
                       (mosaic_w - pad, y + THUMB + pad // 2)],
                      fill=(25, 25, 30), width=1)

    # Save
    out_path = OUT_DIR / "tier1_z5_multiband.png"
    mosaic.save(out_path, dpi=(150, 150))
    print(f"\nSaved: {out_path}")
    print(f"Size: {mosaic_w} x {mosaic_h} px")

    desktop_path = Path(os.path.expanduser("~/Desktop/tier1_z5_multiband.png"))
    mosaic.save(desktop_path, dpi=(150, 150))
    print(f"Desktop: {desktop_path}")


if __name__ == "__main__":
    main()
