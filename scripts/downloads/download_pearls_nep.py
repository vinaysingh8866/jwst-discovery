#!/usr/bin/env python3
"""Download JWST NIRCam i2d mosaics for PEARLS NEP Time-Domain Field.

Program: GTO-2738 (PI: Windhorst)
Field: North Ecliptic Pole Time-Domain Field
8 NIRCam filters: F090W, F115W, F150W, F200W, F277W, F356W, F410M, F444W
Multi-epoch coverage, unique high-latitude field.
Total download: ~15-25 GB
"""
import sys
import re
from pathlib import Path

from astroquery.mast import Observations
from astropy.coordinates import SkyCoord
import astropy.units as u

TARGET = "PEARLS-NEP"
PROGRAM_ID = "2738"
CENTER_RA = 261.470
CENTER_DEC = 66.380
FILTERS = ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"]

WORKSPACE = Path.home() / ".jwst-discovery" / "workspaces" / "PEARLS-NEP"
RAW_DIR = WORKSPACE / "raw"


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print("Target: PEARLS NEP Time-Domain Field")
    print(f"Program: GTO-{PROGRAM_ID} (PI: Windhorst)")
    print(f"Center: RA={CENTER_RA}, Dec={CENTER_DEC}")
    print(f"Workspace: {WORKSPACE}")
    print(f"Filters: {', '.join(FILTERS)}")
    print()

    coord = SkyCoord(ra=CENTER_RA, dec=CENTER_DEC, unit="deg")
    obs = Observations.query_criteria(
        coordinates=coord,
        radius=10 * u.arcmin,
        obs_collection="JWST",
        instrument_name="NIRCAM/IMAGE",
        proposal_id=PROGRAM_ID,
    )

    if len(obs) == 0:
        print("ERROR: No observations found!")
        sys.exit(1)

    print(f"Found {len(obs)} observations")
    products = Observations.get_product_list(obs)

    detector_pattern = re.compile(r"_nrc[ab](?:[1-4]|long)_")
    candidates = {}
    for row in products:
        fname = str(row["productFilename"])
        if "_i2d.fits" not in fname:
            continue
        if detector_pattern.search(fname):
            continue
        for filt in FILTERS:
            if filt.lower() in fname.lower():
                size = int(row["size"]) if row["size"] else 0
                # Take the largest mosaic (most combined data)
                if filt not in candidates or size > int(candidates[filt]["size"]):
                    candidates[filt] = row
                break

    print(f"Selected {len(candidates)} mosaics: {sorted(candidates.keys())}")

    for filt in sorted(candidates.keys()):
        row = candidates[filt]
        fname = str(row["productFilename"])
        dest = RAW_DIR / fname
        if dest.exists() and dest.stat().st_size > 1000:
            print(f"  [{filt}] Already exists ({dest.stat().st_size/1e6:.1f} MB)")
            continue

        size_mb = int(row["size"]) / 1e6 if row["size"] else 0
        print(f"  Downloading {filt}: {fname} ({size_mb:.0f} MB)...")

        result = Observations.download_products(
            products[products["productFilename"] == fname][:1],
            download_dir=str(RAW_DIR / "_tmp"),
        )
        for dl_row in result:
            src = Path(str(dl_row["Local Path"]))
            if src.exists():
                d = RAW_DIR / src.name
                src.rename(d)
                print(f"  -> {d.name} ({d.stat().st_size / 1e6:.1f} MB)")

    tmp_dir = RAW_DIR / "_tmp"
    if tmp_dir.exists():
        import shutil
        shutil.rmtree(tmp_dir)

    print("\n=== Summary ===")
    total = 0
    for filt in FILTERS:
        files = list(RAW_DIR.glob(f"*{filt.lower()}*i2d.fits"))
        if files:
            s = files[0].stat().st_size / 1e6
            total += s
            print(f"  {filt}: {files[0].name} ({s:.1f} MB)")
        else:
            print(f"  {filt}: MISSING")
    print(f"\nTotal: {total:.1f} MB ({total/1024:.1f} GB)")


if __name__ == "__main__":
    main()
