#!/usr/bin/env python3
"""Download JADES GOODS-South NIRCam imaging data from MAST.

Strategy:
1. First check for JADES HLSP mosaics (pre-combined per-filter, ideal quality)
2. Fall back to program 1210 Level 3 products if no HLSP mosaics
3. Fall back to program 1180

Uses parallel curl downloads for speed.

Target: GOODS-South (JADES)
Center: RA=53.126, Dec=-27.802
Programs: 1180 (medium), 1210 (deep), 1286 (wide)
Filters: F090W, F115W, F150W, F200W, F277W, F335M, F356W, F410M, F444W
"""
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from astroquery.mast import Observations

WORKSPACE = Path.home() / ".jwst-discovery" / "workspaces" / "JADES-GOODS-S"
RAW_DIR = WORKSPACE / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

FILTERS = ["F090W", "F115W", "F150W", "F200W", "F277W", "F335M", "F356W", "F410M", "F444W"]
CENTER_RA = 53.126
CENTER_DEC = -27.802

MAST_URL = "https://mast.stsci.edu/api/v0.1/Download/file"


def download_with_curl(url, dest, label=""):
    """Download a file using curl for better speed."""
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [{label}] SKIP (cached): {dest.name} ({dest.stat().st_size/1e6:.1f} MB)", flush=True)
        return dest

    print(f"  [{label}] Starting: {dest.name}...", flush=True)
    tmp = dest.with_suffix(".part")

    try:
        result = subprocess.run(
            [
                "curl", "-L", "-f", "--retry", "3", "--retry-delay", "5",
                "-o", str(tmp),
                "--progress-bar",
                "-H", "User-Agent: jwst-discovery-pipeline/0.1",
                url,
            ],
            capture_output=False,
            timeout=3600,  # 1 hour max per file
        )
        if result.returncode != 0:
            tmp.unlink(missing_ok=True)
            print(f"  [{label}] FAILED: curl returned {result.returncode}", flush=True)
            return None

        tmp.rename(dest)
        print(f"  [{label}] DONE: {dest.name} ({dest.stat().st_size/1e6:.1f} MB)", flush=True)
        return dest
    except subprocess.TimeoutExpired:
        tmp.unlink(missing_ok=True)
        print(f"  [{label}] TIMEOUT after 1 hour", flush=True)
        return None
    except Exception as e:
        tmp.unlink(missing_ok=True)
        print(f"  [{label}] FAILED: {e}", flush=True)
        return None


def try_program_mosaics(program_id="1210"):
    """Find Level 3 combined mosaics from a specific program."""
    print(f"\n=== Searching program {program_id} for combined mosaics ===", flush=True)

    obs = Observations.query_criteria(
        proposal_id=[program_id],
        obs_collection="JWST",
        instrument_name="NIRCAM/IMAGE",
        dataproduct_type="image",
    )

    if len(obs) == 0:
        print(f"  No observations found for program {program_id}", flush=True)
        return {}

    print(f"  Found {len(obs)} observations", flush=True)

    products = Observations.get_product_list(obs)
    science = Observations.filter_products(
        products, productType="SCIENCE", extension="fits"
    )
    print(f"  Science FITS products: {len(science)}", flush=True)

    # Separate Level 3 (combined) from Level 2 (per-detector)
    detector_pattern = re.compile(r"_nrc[ab](?:[1-4]|long)_")
    filter_pattern = re.compile(r"clear-([a-z]\d+[a-z])", re.IGNORECASE)

    level3 = []
    level2 = []
    for row in science:
        fname = str(row["productFilename"])
        if "_i2d.fits" not in fname:
            continue
        if detector_pattern.search(fname):
            level2.append(row)
        else:
            level3.append(row)

    print(f"  Level 3 (combined) i2d: {len(level3)}", flush=True)
    print(f"  Level 2 (per-detector) i2d: {len(level2)}", flush=True)

    if level3:
        print("\n  Level 3 products:", flush=True)
        for row in level3[:20]:
            print(f"    {row['productFilename']} ({int(row['size'])/1e6:.1f} MB)", flush=True)

    # Select best per filter from Level 3
    selected = {}
    for row in (level3 if level3 else level2):
        fname = str(row["productFilename"])
        m = filter_pattern.search(fname)
        if m:
            filt = m.group(1).upper()
            if filt in FILTERS:
                size = int(row["size"]) if row["size"] else 0
                if filt not in selected or size > int(selected[filt]["size"]):
                    selected[filt] = row

    print(f"\n  Selected mosaics for {len(selected)} filters: {sorted(selected.keys())}", flush=True)
    return selected


def download_all_parallel(selected, max_workers=4):
    """Download all selected products in parallel using curl."""
    if not selected:
        print("  No products to download", flush=True)
        return []

    tasks = []
    for filt in sorted(selected.keys()):
        row = selected[filt]
        fname = str(row["productFilename"])
        uri = f"mast:JWST/product/{fname}"
        if "hlsp" in fname.lower():
            uri = f"mast:HLSP/product/{fname}"

        url = f"{MAST_URL}?uri={uri}"
        dest = RAW_DIR / fname
        tasks.append({"filter": filt, "url": url, "dest": dest, "filename": fname})

    downloaded = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for t in tasks:
            future = pool.submit(download_with_curl, t["url"], t["dest"], t["filter"])
            futures[future] = t

        for future in as_completed(futures):
            t = futures[future]
            result = future.result()
            if result:
                downloaded.append({"filter": t["filter"], "path": str(result), "filename": t["filename"]})
                print(f"\n  Progress: {len(downloaded)}/{len(tasks)} files complete", flush=True)

    return downloaded


def main():
    print("JADES GOODS-South Data Download", flush=True)
    print(f"Workspace: {WORKSPACE}", flush=True)
    print(f"Filters: {FILTERS}", flush=True)
    print(f"Center: RA={CENTER_RA}, Dec={CENTER_DEC}", flush=True)
    print(flush=True)

    start = time.time()

    # Query MAST for Level 3 combined mosaics from program 1210 (deep)
    selected = try_program_mosaics("1210")

    # Fall back to program 1180 for any missing filters
    if len(selected) < len(FILTERS):
        missing = set(FILTERS) - set(selected.keys())
        print(f"\n  Missing from 1210: {sorted(missing)}", flush=True)
        prog_selected = try_program_mosaics("1180")
        for filt, row in prog_selected.items():
            if filt not in selected:
                selected[filt] = row

    # Report what we found
    print(f"\n{'='*60}", flush=True)
    print(f"DOWNLOAD PLAN: {len(selected)} / {len(FILTERS)} filters", flush=True)
    print(f"{'='*60}", flush=True)

    total_size = 0
    for filt in sorted(selected.keys()):
        row = selected[filt]
        size = int(row["size"]) if row["size"] else 0
        total_size += size
        print(f"  {filt}: {row['productFilename']} ({size/1e6:.1f} MB)", flush=True)

    missing = set(FILTERS) - set(selected.keys())
    if missing:
        print(f"\n  MISSING: {sorted(missing)}", flush=True)
    print(f"\n  Total download: {total_size/1e6:.0f} MB ({total_size/1e9:.1f} GB)", flush=True)

    # Download in parallel (4 concurrent downloads)
    print(f"\n{'='*60}", flush=True)
    print("DOWNLOADING (4 parallel)...", flush=True)
    print(f"{'='*60}", flush=True)
    downloaded = download_all_parallel(selected, max_workers=4)

    elapsed = time.time() - start
    print(f"\n{'='*60}", flush=True)
    print(f"COMPLETE: {len(downloaded)} files in {elapsed:.0f}s", flush=True)
    print(f"{'='*60}", flush=True)

    # List files in raw dir
    print("\nFiles in raw/:", flush=True)
    for f in sorted(RAW_DIR.glob("*.fits")):
        print(f"  {f.name} ({f.stat().st_size/1e6:.1f} MB)", flush=True)

    return len(downloaded)


if __name__ == "__main__":
    sys.exit(0 if main() > 0 else 1)
