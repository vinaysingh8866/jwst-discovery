#!/usr/bin/env python3
"""Download JWST NIRCam i2d mosaics for Abell S1063 (RXC J2248.7-4431).

Program: GO-3293 (PI: Atek, GLIMPSE — "JWST Lensing Infrastructure for
         Galaxy Growth and Light from the Epoch of Reionization")
Observation: jw03293-o001_t001
z_cluster = 0.348
9 NIRCam filters: F090W, F115W, F150W, F200W, F277W, F356W, F410M, F444W, F480M
Total download: ~33 GB

Hubble Frontier Field cluster with deep JWST GLIMPSE imaging.
No comprehensive JWST photometric catalog published yet.
"""

import sys
from pathlib import Path

from astroquery.mast import Observations
from astropy.coordinates import SkyCoord
import astropy.units as u

# ---------- Config ----------
TARGET = "AbellS1063"
PROGRAM_ID = "3293"
OBS_PREFIX = "jw03293-o001"
CENTER_RA = 342.1833       # Abell S1063 RA (J2000)
CENTER_DEC = -44.5308      # Abell S1063 Dec (J2000)
FILTERS = ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W", "F480M"]

WORKSPACE = Path.home() / ".jwst-discovery" / "workspaces" / "Abell-S1063"
RAW_DIR = WORKSPACE / "raw"


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print("Target: Abell S1063 (RXC J2248.7-4431)")
    print(f"Program: GO-{PROGRAM_ID} (PI: Atek, GLIMPSE)")
    print(f"Center: RA={CENTER_RA}, Dec={CENTER_DEC}")
    print(f"Workspace: {WORKSPACE}")
    print(f"Filters: {', '.join(FILTERS)}")
    print()

    # Check what's already downloaded
    existing = {f.name for f in RAW_DIR.glob("*.fits")}
    needed_filters = []
    for filt in FILTERS:
        expected = f"{OBS_PREFIX}_t001_nircam_clear-{filt.lower()}_i2d.fits"
        if expected in existing:
            size_mb = (RAW_DIR / expected).stat().st_size / 1e6
            print(f"  [CACHED] {expected} ({size_mb:.1f} MB)")
        else:
            needed_filters.append(filt)
            print(f"  [NEED]   {expected}")

    if not needed_filters:
        print("\nAll files already downloaded!")
        return

    print(f"\nDownloading {len(needed_filters)} mosaics...")

    # Query MAST
    coord = SkyCoord(ra=CENTER_RA, dec=CENTER_DEC, unit="deg")
    obs = Observations.query_criteria(
        coordinates=coord,
        radius=5 * u.arcmin,
        obs_collection="JWST",
        instrument_name="NIRCAM/IMAGE",
        proposal_id=PROGRAM_ID,
    )

    if len(obs) == 0:
        print("ERROR: No observations found!")
        sys.exit(1)

    print(f"Found {len(obs)} observations")

    # Get products
    products = Observations.get_product_list(obs)

    # Filter for i2d FITS mosaics from o001 observation block
    i2d_fits = products[
        [("i2d.fits" in str(row["productFilename"]) and "o001" in str(row["productFilename"]))
         for row in products]
    ]

    print(f"Found {len(i2d_fits)} i2d FITS files from o001")

    # Download only needed filters
    for filt in needed_filters:
        filt_lower = filt.lower()
        matching = i2d_fits[
            [(f"clear-{filt_lower}_i2d.fits" in str(row["productFilename"])) for row in i2d_fits]
        ]

        if len(matching) == 0:
            print(f"  WARNING: No i2d file found for {filt}")
            continue

        row = matching[0]
        fname = str(row["productFilename"])
        size_mb = int(row["size"]) / 1e6 if row["size"] else 0
        print(f"\n  Downloading {filt}: {fname} ({size_mb:.0f} MB)...")

        result = Observations.download_products(
            matching[:1],
            download_dir=str(RAW_DIR / "_tmp"),
        )

        # Move from MAST download structure to flat raw/ directory
        for dl_row in result:
            src = Path(str(dl_row["Local Path"]))
            if src.exists():
                dest = RAW_DIR / src.name
                src.rename(dest)
                print(f"  -> {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")

    # Clean up temp download dirs
    tmp_dir = RAW_DIR / "_tmp"
    if tmp_dir.exists():
        import shutil
        shutil.rmtree(tmp_dir)

    # Verify
    print("\n=== Download Summary ===")
    total_size = 0
    for filt in FILTERS:
        expected = f"{OBS_PREFIX}_t001_nircam_clear-{filt.lower()}_i2d.fits"
        path = RAW_DIR / expected
        if path.exists():
            size_mb = path.stat().st_size / 1e6
            total_size += size_mb
            print(f"  {filt}: {expected} ({size_mb:.1f} MB)")
        else:
            print(f"  {filt}: MISSING")

    print(f"\nTotal: {total_size:.1f} MB ({total_size/1024:.1f} GB)")


if __name__ == "__main__":
    main()
