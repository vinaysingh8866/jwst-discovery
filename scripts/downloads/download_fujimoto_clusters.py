#!/usr/bin/env python3
"""Download JWST NIRCam i2d mosaics for PID-6882 lensing clusters (PI: Fujimoto).

6 lensing clusters with uniform 8-band NIRCam imaging.
Most have ZERO published photometric catalogs — highest discovery potential.

Clusters:
  1. MS 0451.6-0305     (z=0.54)   RA=73.54, Dec=-3.00
  2. RX J2129.7+0005    (z=0.234)  RA=322.42, Dec=+0.09
  3. RXC J0600.1-2007   (z=0.46)   RA=90.06, Dec=-20.12
  4. MACS J0329.7-0211  (z=0.45)   RA=52.42, Dec=-2.20
  5. RXC J0032.1+1808   (z=0.32)   RA=8.04, Dec=+18.14
  6. RXC J2211.7-0350   (z=0.40)   RA=332.94, Dec=-3.83

Total download: ~30-50 GB
"""
import sys
import re
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from astroquery.mast import Observations
from astropy.coordinates import SkyCoord
import astropy.units as u

PROGRAM_ID = "6882"
FILTERS = ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"]
WS_ROOT = Path.home() / ".jwst-discovery" / "workspaces"

CLUSTERS = [
    {
        "name": "MS-0451",
        "full_name": "MS 0451.6-0305",
        "ra": 73.545,
        "dec": -3.014,
        "z": 0.54,
    },
    {
        "name": "RXJ-2129",
        "full_name": "RX J2129.7+0005",
        "ra": 322.417,
        "dec": 0.089,
        "z": 0.234,
    },
    {
        "name": "RXC-J0600",
        "full_name": "RXC J0600.1-2007",
        "ra": 90.064,
        "dec": -20.121,
        "z": 0.46,
    },
    {
        "name": "MACS-J0329",
        "full_name": "MACS J0329.7-0211",
        "ra": 52.423,
        "dec": -2.196,
        "z": 0.45,
    },
    {
        "name": "RXC-J0032",
        "full_name": "RXC J0032.1+1808",
        "ra": 8.040,
        "dec": 18.140,
        "z": 0.32,
    },
    {
        "name": "RXC-J2211",
        "full_name": "RXC J2211.7-0350",
        "ra": 332.940,
        "dec": -3.833,
        "z": 0.40,
    },
]


def download_file(url, dest, label, lock, state):
    from urllib.request import urlopen, Request
    try:
        req = Request(url)
        req.add_header("User-Agent", "jwst-discovery-pipeline/0.1")
        with urlopen(req, timeout=600) as resp:
            tmp = dest.with_suffix(".part")
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    with lock:
                        state["done"] += len(chunk)
            tmp.rename(dest)
            mb = dest.stat().st_size / 1e6
            print(f"    [{label}] Done: {mb:.1f} MB", flush=True)
            return True
    except Exception as e:
        print(f"    [{label}] FAILED: {e}", flush=True)
        dest.with_suffix(".part").unlink(missing_ok=True)
        return False


def download_cluster(cluster):
    name = cluster["name"]
    ws = WS_ROOT / name
    raw_dir = ws / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  {cluster['full_name']}  (z={cluster['z']})")
    print(f"  RA={cluster['ra']:.3f}, Dec={cluster['dec']:.3f}")
    print(f"  Workspace: {ws}")
    print(f"{'='*60}")

    coord = SkyCoord(ra=cluster["ra"], dec=cluster["dec"], unit="deg")

    # Try PID 6882 first, then also check for other programs at same location
    program_ids = [PROGRAM_ID]
    # Some clusters also have data from other programs
    extra_pids = {
        "MS-0451": ["5058"],       # Furtak lensed stars
        "RXJ-2129": ["2767"],      # Kelly SN DDT
        "RXC-J0600": ["1567", "4573"],  # Fujimoto/other
        "MACS-J0329": ["5594"],
        "RXC-J0032": ["5594"],
        "RXC-J2211": ["5594"],
    }
    program_ids.extend(extra_pids.get(name, []))

    all_obs = []
    for pid in program_ids:
        try:
            obs = Observations.query_criteria(
                coordinates=coord,
                radius=5 * u.arcmin,
                obs_collection="JWST",
                instrument_name="NIRCAM/IMAGE",
                proposal_id=pid,
            )
            if len(obs) > 0:
                print(f"  GO-{pid}: {len(obs)} observations")
                all_obs.append(obs)
        except Exception as e:
            print(f"  GO-{pid}: query failed ({e})")

    if not all_obs:
        print(f"  No observations found for {name}. SKIPPING.")
        return 0

    from astropy.table import vstack
    obs = vstack(all_obs) if len(all_obs) > 1 else all_obs[0]
    print(f"  Total: {len(obs)} observations")

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
                if filt not in candidates or size > int(candidates[filt]["size"]):
                    candidates[filt] = row
                break

    print(f"  Selected {len(candidates)} mosaics: {sorted(candidates.keys())}")

    tasks = []
    for filt in sorted(candidates.keys()):
        row = candidates[filt]
        fname = str(row["productFilename"])
        uri = f"mast:JWST/product/{fname}"
        url = f"https://mast.stsci.edu/api/v0.1/Download/file?uri={uri}"
        dest = raw_dir / fname
        size = int(row["size"]) if row["size"] else 0
        tasks.append({"filter": filt, "url": url, "dest": dest, "size": size, "fname": fname})

    to_download = []
    for t in tasks:
        if t["dest"].exists() and t["dest"].stat().st_size > 1000:
            print(f"    [{t['filter']}] Cached ({t['dest'].stat().st_size/1e6:.1f} MB)")
        else:
            to_download.append(t)

    if not to_download:
        print(f"  All {len(tasks)} files cached for {name}!")
        return len(tasks)

    print(f"  Downloading {len(to_download)} files (3 threads)...")
    lock = threading.Lock()
    state = {"done": 0}

    ok = 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(download_file, t["url"], t["dest"], t["filter"], lock, state): t
                for t in to_download}
        for f in as_completed(futs):
            success = f.result()
            if success:
                ok += 1

    print(f"  {name}: {ok}/{len(to_download)} downloaded")
    return ok + (len(tasks) - len(to_download))


def main():
    if len(sys.argv) > 1 and sys.argv[1] != "all":
        names = [a.upper().replace("_", "-") for a in sys.argv[1:]]
        clusters = [c for c in CLUSTERS if any(n in c["name"].upper() for n in names)]
    else:
        clusters = CLUSTERS

    print("=" * 60)
    print("  Fujimoto PID-6882 Cluster Download — 6 New Lensing Fields")
    print("=" * 60)
    print(f"  Clusters: {[c['name'] for c in clusters]}")
    print(f"  Filters:  {FILTERS}")
    print()

    results = {}
    for cluster in clusters:
        n = download_cluster(cluster)
        results[cluster["name"]] = n
        time.sleep(2)

    print(f"\n{'='*60}")
    print("  DOWNLOAD SUMMARY")
    print(f"{'='*60}")
    for name, count in results.items():
        status = "OK" if count >= 6 else "PARTIAL" if count > 0 else "FAILED"
        print(f"  {name:20s}: {count} filters [{status}]")


if __name__ == "__main__":
    main()
