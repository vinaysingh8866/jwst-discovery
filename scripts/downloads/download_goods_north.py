#!/usr/bin/env python3
"""Download JADES DR3 GOODS-North NIRCam HLSP mosaics from MAST.

Downloads pre-combined per-filter i2d mosaics from the JADES HLSP.
These are significantly higher quality than individual program Level 3 products.

Target: GOODS-North (JADES DR3)
Center: RA=189.229, Dec=62.238
Filters: F090W, F115W, F150W, F182M, F200W, F210M, F277W, F335M, F356W, F410M, F444W
Total download: ~110 GB (11 filters x ~10 GB each)

Usage:
    python3 download_goods_north.py              # download all filters
    python3 download_goods_north.py --filters F200W,F277W  # specific filters only
"""
import argparse
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

WORKSPACE = Path.home() / ".jwst-discovery" / "workspaces" / "GOODS-North"
RAW_DIR = WORKSPACE / "raw"

# JADES DR3 GOODS-N HLSP base URL
HLSP_BASE = "https://archive.stsci.edu/hlsps/jades/dr3/goods-n/images"

# 11 NIRCam filters available in JADES DR3 GOODS-N
FILTERS = [
    "F090W", "F115W", "F150W", "F182M", "F200W", "F210M",
    "F277W", "F335M", "F356W", "F410M", "F444W",
]


def hlsp_url(filt: str) -> str:
    """Build the HLSP mosaic URL for a given filter."""
    f = filt.lower()
    # JADES HLSP uses _drz.fits (drizzled), not _i2d.fits (standard pipeline)
    return f"{HLSP_BASE}/hlsp_jades_jwst_nircam_goods-n_{f}_v1.0_drz.fits"


def download_with_curl(url: str, dest: Path, label: str = "") -> Path | None:
    """Download a file using curl with resume support."""
    if dest.exists() and dest.stat().st_size > 1_000_000:
        print(f"  [{label}] SKIP (cached): {dest.name} ({dest.stat().st_size/1e9:.2f} GB)", flush=True)
        return dest

    print(f"  [{label}] Starting: {dest.name}...", flush=True)
    tmp = dest.with_suffix(".part")

    try:
        # Use -C - to resume partial downloads
        cmd = [
            "curl", "-L", "-f", "--retry", "3", "--retry-delay", "10",
            "-C", "-",
            "-o", str(tmp),
            "--progress-bar",
            "-H", "User-Agent: jwst-discovery-pipeline/0.1",
            url,
        ]
        result = subprocess.run(cmd, capture_output=False, timeout=7200)  # 2 hour max

        if result.returncode != 0:
            # If resume failed (416 = range not satisfiable), file may be complete
            if result.returncode == 22 and tmp.exists() and tmp.stat().st_size > 1_000_000:
                tmp.rename(dest)
                return dest
            print(f"  [{label}] FAILED: curl returned {result.returncode}", flush=True)
            return None

        tmp.rename(dest)
        print(f"  [{label}] DONE: {dest.name} ({dest.stat().st_size/1e9:.2f} GB)", flush=True)
        return dest
    except subprocess.TimeoutExpired:
        print(f"  [{label}] TIMEOUT after 2 hours (partial download preserved)", flush=True)
        return None
    except Exception as e:
        print(f"  [{label}] FAILED: {e}", flush=True)
        return None


def main():
    parser = argparse.ArgumentParser(description="Download JADES DR3 GOODS-North mosaics")
    parser.add_argument("--filters", type=str, default=None,
                        help="Comma-separated filter list (default: all 11)")
    parser.add_argument("--workers", type=int, default=3,
                        help="Parallel download workers (default: 3)")
    args = parser.parse_args()

    filters = FILTERS
    if args.filters:
        filters = [f.strip().upper() for f in args.filters.split(",")]
        invalid = [f for f in filters if f not in FILTERS]
        if invalid:
            print(f"ERROR: Unknown filters: {invalid}")
            print(f"Available: {FILTERS}")
            sys.exit(1)

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    print("JADES DR3 GOODS-North Mosaic Download")
    print(f"Workspace: {WORKSPACE}")
    print(f"Filters: {filters} ({len(filters)} total)")
    print(f"Parallel workers: {args.workers}")
    print(f"Estimated total: ~{len(filters) * 10} GB")
    print()

    # Build download tasks
    tasks = []
    for filt in filters:
        url = hlsp_url(filt)
        dest = RAW_DIR / f"hlsp_jades_jwst_nircam_goods-n_{filt.lower()}_v1.0_drz.fits"
        tasks.append({"filter": filt, "url": url, "dest": dest})

    # Check existing
    cached = sum(1 for t in tasks if t["dest"].exists() and t["dest"].stat().st_size > 1_000_000)
    print(f"Already cached: {cached}/{len(tasks)}")
    print()

    start = time.time()
    downloaded = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for t in tasks:
            future = pool.submit(download_with_curl, t["url"], t["dest"], t["filter"])
            futures[future] = t

        for future in as_completed(futures):
            t = futures[future]
            result = future.result()
            if result:
                downloaded.append(t["filter"])
                print(f"\n  Progress: {len(downloaded)}/{len(tasks)} filters complete", flush=True)

    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"COMPLETE: {len(downloaded)}/{len(tasks)} filters in {elapsed:.0f}s")
    print(f"{'='*60}")

    # List files
    print("\nFiles in raw/:")
    total_size = 0
    for f in sorted(RAW_DIR.glob("*.fits")):
        size = f.stat().st_size
        total_size += size
        print(f"  {f.name} ({size/1e9:.2f} GB)")
    print(f"\n  Total: {total_size/1e9:.1f} GB")

    return len(downloaded)


if __name__ == "__main__":
    sys.exit(0 if main() > 0 else 1)
