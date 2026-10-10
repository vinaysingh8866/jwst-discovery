#!/usr/bin/env python3
"""Download MACS J0417.5-1154 (CANUCS) NIRCam data from MAST.

Target: MACS J0417.5-1154 (galaxy cluster, strong gravitational lensing)
Program: 1208 (CANUCS - CAnadian NIRISS Unbiased Cluster Survey)
Center: RA=64.394, Dec=-11.909
Filters: 15 NIRCam bands (6 broad + 9 medium)
Total: ~13.4 GB
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen, Request
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from astroquery.mast import Observations

WORKSPACE = Path.home() / ".jwst-discovery" / "workspaces" / "MACS-J0417"
RAW_DIR = WORKSPACE / "raw"
MAST_DOWNLOAD_URL = "https://mast.stsci.edu/api/v0.1/Download/file"
PROGRAM_ID = "1208"
TARGET = "MACSJ0417.5-1154"
# All 15 available filters
FILTERS = [
    "F090W", "F115W", "F140M", "F150W", "F182M", "F200W", "F210M",
    "F250M", "F277W", "F300M", "F335M", "F356W", "F360M", "F410M", "F444W",
]


def download_file(url: str, dest: Path, label: str, lock, global_state) -> bool:
    """Download a file with progress."""
    try:
        req = Request(url)
        req.add_header("User-Agent", "jwst-discovery-pipeline/0.1")

        with urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 1024

            tmp = dest.with_suffix(".part")
            with open(tmp, "wb") as f:
                last_print = 0
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    with lock:
                        global_state["bytes_done"] += len(chunk)

                    if total > 0 and downloaded - last_print > 20 * 1024 * 1024:
                        pct = 100 * downloaded / total
                        mb = downloaded / 1e6
                        total_mb = total / 1e6
                        with lock:
                            gpct = 100 * global_state["bytes_done"] / global_state["total_bytes"] if global_state["total_bytes"] > 0 else 0
                        print(f"  [{label}] {mb:.0f}/{total_mb:.0f} MB ({pct:.0f}%) | Overall: {gpct:.0f}%", flush=True)
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

    print(f"=== Downloading MACS J0417.5-1154 (CANUCS, Program {PROGRAM_ID}) ===")
    print(f"Workspace: {WORKSPACE}")
    print("15 NIRCam filters (6 broad + 9 medium)")
    print()

    # Query MAST
    print("Querying MAST for observations...", flush=True)
    obs = Observations.query_criteria(
        proposal_id=[PROGRAM_ID],
        obs_collection="JWST",
        instrument_name="NIRCAM/IMAGE",
        dataproduct_type="image",
        target_name=[TARGET],
    )
    print(f"Found {len(obs)} observations")

    if len(obs) == 0:
        print("ERROR: No observations found!")
        sys.exit(1)

    # Get products
    print("Fetching product list...", flush=True)
    products = Observations.get_product_list(obs)
    science = Observations.filter_products(products, productType="SCIENCE", extension="fits")

    # Select i2d mosaics per filter (smallest = most combined)
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

    # Build download tasks
    tasks = []
    for filt in sorted(selected.keys()):
        row = selected[filt]
        fname = str(row["productFilename"])
        uri = f"mast:JWST/product/{fname}"
        url = f"{MAST_DOWNLOAD_URL}?uri={uri}"
        dest = RAW_DIR / fname
        size = int(row["size"]) if row["size"] else 0
        tasks.append({"filter": filt, "filename": fname, "url": url, "dest": dest, "size": size})

    # Check already downloaded
    to_download = []
    manifest = {}
    now_iso = datetime.now(timezone.utc).isoformat()

    for t in tasks:
        if t["dest"].exists() and t["dest"].stat().st_size > 0:
            actual = t["dest"].stat().st_size
            expected = t["size"]
            if expected == 0 or abs(actual - expected) < expected * 0.01:
                print(f"[{t['filter']}] Already downloaded ({actual/1e6:.1f} MB), skipping")
                manifest[f"{PROGRAM_ID}:{t['filter']}"] = {
                    "program_id": PROGRAM_ID,
                    "filter": t["filter"],
                    "filename": t["filename"],
                    "path": str(t["dest"]),
                    "size_mb": round(actual / 1e6, 1),
                    "downloaded_at": now_iso,
                    "target": "MACS-J0417",
                }
                continue
        to_download.append(t)

    if not to_download:
        print("\nAll files already downloaded!")
    else:
        print(f"\nDownloading {len(to_download)} files with 3 parallel threads...")

        lock = threading.Lock()
        global_state = {
            "bytes_done": sum(t["dest"].stat().st_size for t in tasks if t["dest"].exists() and t not in to_download),
            "total_bytes": sum(t["size"] for t in tasks),
        }

        start_time = time.time()

        def do_download(t):
            return t, download_file(t["url"], t["dest"], t["filter"], lock, global_state)

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(do_download, t): t for t in to_download}
            for future in as_completed(futures):
                t, success = future.result()
                if success:
                    actual = t["dest"].stat().st_size
                    manifest[f"{PROGRAM_ID}:{t['filter']}"] = {
                        "program_id": PROGRAM_ID,
                        "filter": t["filter"],
                        "filename": t["filename"],
                        "path": str(t["dest"]),
                        "size_mb": round(actual / 1e6, 1),
                        "downloaded_at": now_iso,
                        "target": "MACS-J0417",
                    }

        elapsed = time.time() - start_time
        print(f"\nDownload time: {elapsed:.0f}s")

    # Write manifest
    manifest_path = WORKSPACE / "downloads.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print("\n=== Download complete ===")
    print(f"Files: {len(manifest)}/{len(selected)}")
    print(f"Manifest: {manifest_path}")
    print("\nTo run pipeline:")
    print('  target_field: "MACS-J0417"')
    print(f'  program_id: "{PROGRAM_ID}"')
    print('  center_ra: 64.394')
    print('  center_dec: -11.909')
    print(f'  filters: {sorted(selected.keys())}')


if __name__ == "__main__":
    main()
