#!/usr/bin/env python3
"""Fast parallel download of Bullet Cluster JWST mosaics using direct MAST URLs.

Bypasses astroquery's slow sequential downloader. Uses concurrent.futures
to download up to 4 files simultaneously with retry logic.
"""

import time
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

WORKSPACE = Path.home() / ".jwst-discovery" / "workspaces" / "Bullet-Cluster"
RAW_DIR = WORKSPACE / "raw"

# Direct MAST download URLs (from the product list we already queried)
OBS_PREFIX = "jw04598-o001_t001_nircam_clear"
MAST_BASE = "https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:JWST/product"

FILES = {
    "F090W": f"{OBS_PREFIX}-f090w_i2d.fits",
    "F115W": f"{OBS_PREFIX}-f115w_i2d.fits",
    "F150W": f"{OBS_PREFIX}-f150w_i2d.fits",
    "F200W": f"{OBS_PREFIX}-f200w_i2d.fits",
    "F277W": f"{OBS_PREFIX}-f277w_i2d.fits",
    "F356W": f"{OBS_PREFIX}-f356w_i2d.fits",
    "F410M": f"{OBS_PREFIX}-f410m_i2d.fits",
    "F444W": f"{OBS_PREFIX}-f444w_i2d.fits",
}

MAX_WORKERS = 4
CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB chunks
MAX_RETRIES = 3
TIMEOUT = 600  # 10 min per connection


def download_one(filt, fname):
    """Download a single FITS file with retry and resume support."""
    dest = RAW_DIR / fname
    if dest.exists() and dest.stat().st_size > 100_000_000:  # >100 MB = likely complete
        sz = dest.stat().st_size / 1e6
        return filt, f"CACHED ({sz:.0f} MB)"

    url = f"{MAST_BASE}/{fname}"
    tmp = RAW_DIR / f".{fname}.partial"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Resume partial download if exists
            start_byte = tmp.stat().st_size if tmp.exists() else 0
            headers = {"Range": f"bytes={start_byte}-"} if start_byte > 0 else {}

            t0 = time.time()
            r = requests.get(url, headers=headers, stream=True, timeout=TIMEOUT)

            if r.status_code == 416:  # Range not satisfiable = file complete
                if tmp.exists():
                    tmp.rename(dest)
                    return filt, "DONE (resumed)"

            r.raise_for_status()

            mode = "ab" if start_byte > 0 else "wb"
            total = start_byte
            with open(tmp, mode) as f:
                for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                    f.write(chunk)
                    total += len(chunk)

            elapsed = time.time() - t0
            speed = (total - start_byte) / elapsed / 1e6 if elapsed > 0 else 0
            tmp.rename(dest)
            return filt, f"DONE ({total/1e6:.0f} MB, {speed:.1f} MB/s)"

        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)  # exponential backoff
                continue
            return filt, f"FAILED after {MAX_RETRIES} attempts: {e}"

    return filt, "FAILED"


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Bullet Cluster — Parallel download ({MAX_WORKERS} threads)")
    print(f"Workspace: {RAW_DIR}")
    print()

    # Check existing
    needed = {}
    for filt, fname in FILES.items():
        dest = RAW_DIR / fname
        if dest.exists() and dest.stat().st_size > 100_000_000:
            print(f"  [CACHED] {filt}: {dest.stat().st_size/1e6:.0f} MB")
        else:
            partial = RAW_DIR / f".{fname}.partial"
            if partial.exists():
                print(f"  [RESUME] {filt}: {partial.stat().st_size/1e6:.0f} MB partial")
            else:
                print(f"  [NEED]   {filt}")
            needed[filt] = fname

    if not needed:
        print("\nAll files already downloaded!")
        return

    print(f"\nDownloading {len(needed)} files with {MAX_WORKERS} parallel threads...\n")
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(download_one, f, fn): f for f, fn in needed.items()}
        for future in as_completed(futures):
            filt, status = future.result()
            print(f"  {filt}: {status}")

    elapsed = time.time() - t0
    print(f"\nCompleted in {elapsed:.0f}s")

    # Summary
    print("\n=== Download Summary ===")
    total = 0
    for filt, fname in FILES.items():
        dest = RAW_DIR / fname
        if dest.exists():
            sz = dest.stat().st_size / 1e6
            total += sz
            print(f"  {filt}: {sz:.0f} MB")
        else:
            print(f"  {filt}: MISSING")
    print(f"\nTotal: {total:.0f} MB ({total/1024:.1f} GB)")


if __name__ == "__main__":
    main()
