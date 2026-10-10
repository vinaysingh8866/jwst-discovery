#!/usr/bin/env python3
"""Download SMACS 0723 (First JWST Deep Field) NIRCam data from MAST.

Target: SMACS J0723.3-7327 (galaxy cluster, strong gravitational lensing)
Program: 2736 (Early Release Observations)
Center: RA=110.8375, Dec=-73.4531
Filters: F090W, F150W, F200W, F277W, F356W, F444W
Total: ~6.3 GB
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen, Request

from astroquery.mast import Observations

WORKSPACE = Path.home() / ".jwst-discovery" / "workspaces" / "SMACS-0723"
RAW_DIR = WORKSPACE / "raw"
MAST_DOWNLOAD_URL = "https://mast.stsci.edu/api/v0.1/Download/file"
PROGRAM_ID = "2736"
FILTERS = ["F090W", "F150W", "F200W", "F277W", "F356W", "F444W"]


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

    print(f"=== Downloading SMACS 0723 (Program {PROGRAM_ID}) ===")
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
    detector_pattern = re.compile(r"_nrc[ab](?:[1-4]|long)_")
    filter_pattern = re.compile(r"clear-([a-z]\d+[a-z])", re.IGNORECASE)

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
        if filt not in selected or size < int(selected[filt]["size"]):
            selected[filt] = row

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
                    "target": "SMACS-0723",
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
                "target": "SMACS-0723",
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
    print("\nTo run pipeline on this data, update config:")
    print('  target_field: "SMACS-0723"')
    print('  program_id: "2736"')
    print('  center_ra: 110.8375')
    print('  center_dec: -73.4531')
    print(f'  filters: {FILTERS}')


if __name__ == "__main__":
    main()
