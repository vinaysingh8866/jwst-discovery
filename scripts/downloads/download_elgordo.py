#!/usr/bin/env python3
"""Download El Gordo (ACT-CL J0102-4915) NIRCam data from MAST.

Target: ACT-CL J0102-4915 "El Gordo" (most massive high-z galaxy cluster)
Program: 1176 (PEARLS GTO)
Center: RA=15.7208, Dec=-49.2553
Filters: F090W, F115W, F150W, F200W, F277W, F356W, F410M, F444W
Cluster z: 0.87
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen, Request

from astroquery.mast import Observations

WORKSPACE = Path.home() / ".jwst-discovery" / "workspaces" / "El-Gordo"
RAW_DIR = WORKSPACE / "raw"
MAST_DOWNLOAD_URL = "https://mast.stsci.edu/api/v0.1/Download/file"
PROGRAM_ID = "1176"
FILTERS = ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"]


def download_file(url: str, dest: Path, label: str) -> bool:
    """Download a file with progress bar."""
    try:
        req = Request(url)
        req.add_header("User-Agent", "jwst-discovery-pipeline/0.1")

        with urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 1024  # 1MB chunks

            tmp = dest.with_suffix(".part")
            with open(tmp, "wb") as f:
                last_print = 0
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    # Progress every 10MB
                    if total > 0 and downloaded - last_print > 10 * 1024 * 1024:
                        pct = 100 * downloaded / total
                        mb = downloaded / 1e6
                        total_mb = total / 1e6
                        print(f"  [{label}] {mb:.0f}/{total_mb:.0f} MB ({pct:.0f}%)", flush=True)
                        last_print = downloaded

            tmp.rename(dest)
            size_mb = dest.stat().st_size / 1e6
            print(f"  [{label}] Done: {size_mb:.1f} MB", flush=True)
            return True

    except Exception as e:
        print(f"  [{label}] FAILED: {e}", flush=True)
        dest.with_suffix(".part").unlink(missing_ok=True)
        return False


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    print(f"=== Downloading El Gordo (Program {PROGRAM_ID} — PEARLS) ===")
    print(f"Workspace: {WORKSPACE}")
    print()

    # Query MAST
    print("Querying MAST for observations...", flush=True)
    obs = Observations.query_criteria(
        proposal_id=[PROGRAM_ID],
        obs_collection="JWST",
        instrument_name="NIRCAM/IMAGE",
        dataproduct_type="image",
    )
    print(f"Found {len(obs)} observations")

    if len(obs) == 0:
        print("ERROR: No observations found!")
        sys.exit(1)

    # Get products
    print("Fetching product list...", flush=True)
    products = Observations.get_product_list(obs)
    science = Observations.filter_products(products, productType="SCIENCE", extension="fits")

    # Select i2d mosaics per filter
    # PEARLS uses standard pipeline naming: clear-FILTER_i2d.fits
    # Skip per-detector files (nrca1, nrcb3, etc.)
    detector_pattern = re.compile(r"_nrc[ab](?:[1-4]|long)_")
    filter_pattern = re.compile(r"(?:clear-)?([a-z]\d+[a-z])", re.IGNORECASE)

    selected = {}
    for row in science:
        fname = str(row["productFilename"])
        if "_i2d.fits" not in fname or detector_pattern.search(fname):
            continue
        m = filter_pattern.search(fname)
        if not m:
            continue
        filt = m.group(1).upper()
        if filt not in FILTERS:
            continue
        size = int(row["size"]) if row["size"] else 0
        # Keep the largest mosaic per filter (combined mosaic, not single-detector)
        if filt not in selected or size > int(selected[filt]["size"]):
            selected[filt] = row

    if len(selected) == 0:
        print("\nNo combined i2d mosaics found. Listing all i2d files:")
        for row in science:
            fname = str(row["productFilename"])
            if "_i2d.fits" in fname:
                size = int(row["size"]) if row["size"] else 0
                print(f"  {fname} ({size/1e6:.1f} MB)")
        print("\nTrying alternate selection (largest per filter)...")
        for row in science:
            fname = str(row["productFilename"])
            if "_i2d.fits" not in fname:
                continue
            for filt in FILTERS:
                if filt.lower() in fname.lower():
                    size = int(row["size"]) if row["size"] else 0
                    if filt not in selected or size > int(selected[filt]["size"]):
                        selected[filt] = row
                    break

    print(f"\nSelected {len(selected)} mosaics:")
    total_size = 0
    for filt in sorted(selected.keys()):
        size_mb = int(selected[filt]["size"]) / 1e6
        total_size += size_mb
        fname = str(selected[filt]["productFilename"])
        print(f"  {filt:>6s}: {size_mb:>8.1f} MB  {fname}")
    print(f"  {'TOTAL':>6s}: {total_size:>8.1f} MB")
    print()

    # Download each filter
    manifest = {}
    downloaded_count = 0
    start_time = time.time()

    for filt in sorted(selected.keys()):
        row = selected[filt]
        fname = str(row["productFilename"])
        dest = RAW_DIR / fname
        size = int(row["size"]) if row["size"] else 0

        # Skip if already downloaded
        if dest.exists() and dest.stat().st_size > 0:
            actual = dest.stat().st_size
            if size == 0 or abs(actual - size) < size * 0.01:
                print(f"[{filt}] Already downloaded ({actual/1e6:.1f} MB), skipping")
                manifest[f"{PROGRAM_ID}:{filt}"] = {
                    "program_id": PROGRAM_ID,
                    "filter": filt,
                    "filename": fname,
                    "path": str(dest),
                    "size_mb": round(actual / 1e6, 1),
                    "downloaded_at": datetime.now(timezone.utc).isoformat(),
                    "target": "El-Gordo",
                }
                downloaded_count += 1
                continue

        uri = f"mast:JWST/product/{fname}"
        url = f"{MAST_DOWNLOAD_URL}?uri={uri}"
        print(f"[{filt}] Downloading {size/1e6:.0f} MB...")

        if download_file(url, dest, filt):
            actual = dest.stat().st_size
            manifest[f"{PROGRAM_ID}:{filt}"] = {
                "program_id": PROGRAM_ID,
                "filter": filt,
                "filename": fname,
                "path": str(dest),
                "size_mb": round(actual / 1e6, 1),
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
                "target": "El-Gordo",
            }
            downloaded_count += 1

    elapsed = time.time() - start_time

    # Write manifest
    manifest_path = WORKSPACE / "downloads.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print("\n=== Download complete ===")
    print(f"Files: {downloaded_count}/{len(selected)}")
    print(f"Time: {elapsed:.0f}s")
    print(f"Manifest: {manifest_path}")
    print("\nTo run pipeline on this data:")
    print('  target_field: "El-Gordo"')
    print(f'  program_id: "{PROGRAM_ID}"')
    print('  center_ra: 15.7208')
    print('  center_dec: -49.2553')
    print(f'  filters: {FILTERS}')


if __name__ == "__main__":
    main()
