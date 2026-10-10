#!/usr/bin/env python3
"""Download JWST NIRCam i2d mosaics for Abell 1689.

Program: GO-1840 (PI: Alvarez-Marquez) — 6 filters
         GO-5782 — 7 filters (much larger mosaics)
Combined: 8 unique NIRCam filters
Total download: ~12.7 GB (combined mosaics only)

Classic strong-lensing cluster at z=0.183. DJA has processed mosaics (v7.4)
but NO JWST-era photometric catalog exists anywhere.
"""

from pathlib import Path

from astroquery.mast import Observations
from astropy.coordinates import SkyCoord
import astropy.units as u

# ---------- Config ----------
TARGET = "Abell1689"
CENTER_RA = 197.8729
CENTER_DEC = -1.3386
# Use PID 1840 for smaller mosaics first (2.1 GB), then PID 5782 for extras
PROGRAMS = [
    {
        "pid": "1840",
        "obs_prefix": "jw01840-o019",
        "target_suffix": "t009",
        "filters": ["F115W", "F150W", "F200W", "F277W", "F356W", "F444W"],
    },
    {
        "pid": "5782",
        "obs_prefix": "jw05782-o001",
        "target_suffix": "t001",
        "filters": ["F115W", "F150W", "F182M", "F200W", "F277W", "F300M", "F356W"],
    },
]

WORKSPACE = Path.home() / ".jwst-discovery" / "workspaces" / "Abell-1689"
RAW_DIR = WORKSPACE / "raw"


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print("Target: Abell 1689")
    print(f"Center: RA={CENTER_RA}, Dec={CENTER_DEC}")
    print(f"Workspace: {WORKSPACE}")
    print()

    existing = {f.name for f in RAW_DIR.glob("*.fits")}

    for prog in PROGRAMS:
        pid = prog["pid"]
        obs_prefix = prog["obs_prefix"]
        target_suffix = prog["target_suffix"]
        filters = prog["filters"]

        print(f"\n--- Program GO-{pid} ({len(filters)} filters) ---")

        needed_filters = []
        for filt in filters:
            expected = f"{obs_prefix}_{target_suffix}_nircam_clear-{filt.lower()}_i2d.fits"
            if expected in existing:
                size_mb = (RAW_DIR / expected).stat().st_size / 1e6
                print(f"  [CACHED] {expected} ({size_mb:.1f} MB)")
            else:
                needed_filters.append(filt)
                print(f"  [NEED]   {expected}")

        if not needed_filters:
            print(f"  All GO-{pid} files already downloaded!")
            continue

        print(f"\n  Downloading {len(needed_filters)} mosaics from GO-{pid}...")

        coord = SkyCoord(ra=CENTER_RA, dec=CENTER_DEC, unit="deg")
        obs = Observations.query_criteria(
            coordinates=coord,
            radius=5 * u.arcmin,
            obs_collection="JWST",
            instrument_name="NIRCAM/IMAGE",
            proposal_id=pid,
        )

        if len(obs) == 0:
            print(f"  ERROR: No observations found for GO-{pid}!")
            continue

        print(f"  Found {len(obs)} observations")
        products = Observations.get_product_list(obs)

        obs_key = obs_prefix.split("-")[1]  # e.g. "o019" or "o001"
        i2d_fits = products[
            [("i2d.fits" in str(row["productFilename"]) and obs_key in str(row["productFilename"]))
             for row in products]
        ]

        print(f"  Found {len(i2d_fits)} i2d FITS files from {obs_key}")

        for filt in needed_filters:
            filt_lower = filt.lower()
            matching = i2d_fits[
                [(f"clear-{filt_lower}_i2d.fits" in str(row["productFilename"])) for row in i2d_fits]
            ]

            if len(matching) == 0:
                print(f"    WARNING: No i2d file found for {filt}")
                continue

            row = matching[0]
            fname = str(row["productFilename"])
            size_mb = int(row["size"]) / 1e6 if row["size"] else 0
            print(f"\n    Downloading {filt}: {fname} ({size_mb:.0f} MB)...")

            result = Observations.download_products(
                matching[:1],
                download_dir=str(RAW_DIR / "_tmp"),
            )

            for dl_row in result:
                src = Path(str(dl_row["Local Path"]))
                if src.exists():
                    dest = RAW_DIR / src.name
                    src.rename(dest)
                    print(f"    -> {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")

    tmp_dir = RAW_DIR / "_tmp"
    if tmp_dir.exists():
        import shutil
        shutil.rmtree(tmp_dir)

    print("\n=== Download Summary ===")
    total_size = 0
    for f in sorted(RAW_DIR.glob("*.fits")):
        size_mb = f.stat().st_size / 1e6
        total_size += size_mb
        print(f"  {f.name} ({size_mb:.1f} MB)")

    print(f"\nTotal: {total_size:.1f} MB ({total_size/1024:.1f} GB)")


if __name__ == "__main__":
    main()
