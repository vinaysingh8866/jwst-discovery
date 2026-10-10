#!/usr/bin/env python3
"""Download 4 new CANUCS lensing cluster NIRCam mosaics from MAST HLSP.

Targets:
  1. MACS J0416.1-2403  (z=0.396, HFF cluster)
  2. Abell 370           (z=0.375, HFF cluster, Dragon Arc)
  3. MACS J1149.5+2223   (z=0.543, HFF, Refsdal SN)
  4. MACS J1423.8+2404   (z=0.545)

Data: CANUCS DR1 HLSP (DOI: 10.17909/18nv-np70)
Program: 1208 (CANUCS GTO)
Filters: 8 broadband (F090W-F444W) per cluster core field (CLU)
"""
import sys
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from astroquery.mast import Observations

# ── Cluster definitions ──────────────────────────────────────────────
CLUSTERS = [
    {
        "name": "MACS-J0416",
        "canucs_field": "macs0416-clu",
        "ra": 64.039,
        "dec": -24.067,
        "z": 0.396,
    },
    {
        "name": "Abell-370",
        "canucs_field": "a370-clu",
        "ra": 39.970,
        "dec": -1.577,
        "z": 0.375,
    },
    {
        "name": "MACS-J1149",
        "canucs_field": "macs1149-clu",
        "ra": 177.399,
        "dec": 22.399,
        "z": 0.543,
    },
    {
        "name": "MACS-J1423",
        "canucs_field": "macs1423-clu",
        "ra": 215.949,
        "dec": 24.078,
        "z": 0.545,
    },
]

FILTERS = ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"]
WS_ROOT = Path.home() / ".jwst-discovery" / "workspaces"
MAST_DL = "https://mast.stsci.edu/api/v0.1/Download/file"


def download_file(url, dest, label, lock, state):
    from urllib.request import urlopen, Request
    try:
        req = Request(url)
        req.add_header("User-Agent", "jwst-discovery-pipeline/0.1")
        with urlopen(req, timeout=300) as resp:
            downloaded = 0
            tmp = dest.with_suffix(".part")
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
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
    """Download CANUCS HLSP mosaics for one cluster."""
    name = cluster["name"]
    field = cluster["canucs_field"]
    ws = WS_ROOT / name
    raw_dir = ws / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  {name}  (z={cluster['z']}, CANUCS field: {field})")
    print(f"  RA={cluster['ra']:.3f}, Dec={cluster['dec']:.3f}")
    print(f"  Workspace: {ws}")
    print(f"{'='*60}")

    # Query MAST for CANUCS HLSP products
    print("  Querying MAST for CANUCS HLSP products...", flush=True)
    try:
        obs = Observations.query_criteria(
            provenance_name="CANUCS",
            obs_collection="HLSP",
            instrument_name="NIRCAM",
            target_name=[field],
        )
    except Exception:
        obs = []

    if len(obs) == 0:
        # Fallback: try without target_name filter and match manually
        print("  No HLSP hits with target_name, trying broader query...", flush=True)
        try:
            obs = Observations.query_criteria(
                provenance_name="CANUCS",
                obs_collection="HLSP",
            )
            # Filter to our field
            obs = obs[[field.replace("-clu", "") in str(r.get("target_name", "")).lower()
                        or field in str(r.get("obs_id", "")).lower()
                        for r in obs]]
        except Exception as e:
            print(f"  Query failed: {e}")
            obs = []

    if len(obs) == 0:
        # Last fallback: query the raw program 1208 data
        print("  No HLSP found. Falling back to Program 1208 raw mosaics...", flush=True)
        return download_cluster_raw(cluster)

    print(f"  Found {len(obs)} HLSP observations")

    products = Observations.get_product_list(obs)
    # Filter to science fits
    sci = [p for p in products
           if "_sci.fits" in str(p.get("productFilename", "")).lower()
           and "nircam" in str(p.get("productFilename", "")).lower()]

    if len(sci) == 0:
        # Try .gz
        sci = [p for p in products
               if "_sci.fits.gz" in str(p.get("productFilename", "")).lower()
               and "nircam" in str(p.get("productFilename", "")).lower()]

    print(f"  Found {len(sci)} science FITS files")

    # Match to our desired filters
    selected = {}
    for row in sci:
        fname = str(row["productFilename"]).lower()
        for filt in FILTERS:
            if filt.lower() in fname and filt not in selected:
                selected[filt] = row
                break

    if len(selected) < 4:
        print(f"  Only matched {len(selected)} filters from HLSP. Falling back to raw program data...")
        return download_cluster_raw(cluster)

    print(f"  Matched {len(selected)} filters: {sorted(selected.keys())}")

    # Download
    tasks = []
    for filt in sorted(selected.keys()):
        row = selected[filt]
        fname = str(row["productFilename"])
        uri = str(row.get("dataURI", f"mast:HLSP/product/{fname}"))
        if not uri.startswith("http"):
            url = f"{MAST_DL}?uri={uri}"
        else:
            url = uri
        dest = raw_dir / fname
        size = int(row["size"]) if row.get("size") else 0
        tasks.append({"filter": filt, "url": url, "dest": dest, "size": size, "fname": fname})

    return _run_downloads(tasks, name)


def download_cluster_raw(cluster):
    """Fallback: download i2d mosaics from the raw JWST program 1208."""
    import re
    name = cluster["name"]
    ws = WS_ROOT / name
    raw_dir = ws / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    # Map cluster names to MAST target names
    target_map = {
        "MACS-J0416": "MACSJ0416.1-2403",
        "Abell-370": "ABELL370",
        "MACS-J1149": "MACSJ1149.5+2223",
        "MACS-J1423": "MACSJ1423.8+2404",
    }
    # Try several target name variants
    target_names = [target_map.get(name, name)]
    alt_names = {
        "MACS-J0416": ["MACS0416", "MACSJ0416", "MACS-J0416.1-2403", "MACS-J0416"],
        "Abell-370": ["A370", "ABELL-370", "Abell370", "ABELL370"],
        "MACS-J1149": ["MACS1149", "MACSJ1149", "MACS-J1149.5+2223", "MACS-J1149"],
        "MACS-J1423": ["MACS1423", "MACSJ1423", "MACS-J1423.8+2404", "MACS-J1423"],
    }
    target_names.extend(alt_names.get(name, []))

    obs = None
    for tgt in target_names:
        print(f"  Trying target_name='{tgt}'...", flush=True)
        try:
            result = Observations.query_criteria(
                proposal_id=["1208"],
                obs_collection="JWST",
                instrument_name="NIRCAM/IMAGE",
                dataproduct_type="image",
                target_name=[tgt],
            )
            if len(result) > 0:
                obs = result
                print(f"  Found {len(obs)} observations for target '{tgt}'")
                break
        except Exception:
            continue

    if obs is None or len(obs) == 0:
        # Cone search fallback
        print(f"  Target name search failed. Trying cone search at RA={cluster['ra']}, Dec={cluster['dec']}...", flush=True)
        try:
            from astropy.coordinates import SkyCoord
            import astropy.units as u
            coord = SkyCoord(ra=cluster["ra"] * u.deg, dec=cluster["dec"] * u.deg)
            obs = Observations.query_criteria(
                coordinates=coord,
                radius=0.1 * u.deg,
                proposal_id=["1208"],
                obs_collection="JWST",
                instrument_name="NIRCAM/IMAGE",
                dataproduct_type="image",
            )
            print(f"  Cone search found {len(obs)} observations")
        except Exception as e:
            print(f"  Cone search failed: {e}")
            print(f"  SKIPPING {name}")
            return 0

    if len(obs) == 0:
        print(f"  No observations found for {name}. SKIPPING.")
        return 0

    products = Observations.get_product_list(obs)
    science = Observations.filter_products(products, productType="SCIENCE", extension="fits")

    detector_pattern = re.compile(r"_nrc[ab](?:[1-4]|long)_")
    filter_pattern = re.compile(r"clear-([a-z]\d+[a-z])", re.IGNORECASE)

    selected = {}
    for row in science:
        fname = str(row["productFilename"])
        if "_i2d.fits" not in fname or detector_pattern.search(fname):
            continue
        m = filter_pattern.search(fname)
        if not m:
            # Try direct filter name in filename
            for filt in FILTERS:
                if filt.lower() in fname.lower() and "_i2d.fits" in fname:
                    if filt not in selected:
                        selected[filt] = row
                    break
            continue
        filt = m.group(1).upper()
        if filt not in FILTERS:
            continue
        size = int(row["size"]) if row["size"] else 0
        if filt not in selected or size < int(selected[filt]["size"]):
            selected[filt] = row

    print(f"  Selected {len(selected)} mosaics: {sorted(selected.keys())}")

    tasks = []
    for filt in sorted(selected.keys()):
        row = selected[filt]
        fname = str(row["productFilename"])
        uri = f"mast:JWST/product/{fname}"
        url = f"{MAST_DL}?uri={uri}"
        dest = raw_dir / fname
        size = int(row["size"]) if row["size"] else 0
        tasks.append({"filter": filt, "url": url, "dest": dest, "size": size, "fname": fname})

    return _run_downloads(tasks, name)


def _run_downloads(tasks, name):
    """Download a list of files with parallel threads."""
    to_download = []
    for t in tasks:
        if t["dest"].exists() and t["dest"].stat().st_size > 1000:
            print(f"    [{t['filter']}] Already exists ({t['dest'].stat().st_size/1e6:.1f} MB), skipping")
        else:
            to_download.append(t)

    if not to_download:
        print(f"  All {len(tasks)} files already downloaded for {name}!")
        return len(tasks)

    print(f"  Downloading {len(to_download)} files (3 threads)...")
    lock = threading.Lock()
    state = {"done": 0}
    t0 = time.time()

    def do_dl(t):
        return t, download_file(t["url"], t["dest"], t["filter"], lock, state)

    ok = 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(do_dl, t): t for t in to_download}
        for f in as_completed(futs):
            t, success = f.result()
            if success:
                ok += 1

    elapsed = time.time() - t0
    print(f"  {name}: {ok}/{len(to_download)} downloaded in {elapsed:.0f}s")
    return ok


def main():
    if len(sys.argv) > 1:
        # Allow selecting specific clusters
        names = [a.upper() for a in sys.argv[1:]]
        clusters = [c for c in CLUSTERS if any(n in c["name"].upper() for n in names)]
    else:
        clusters = CLUSTERS

    print("=" * 60)
    print("  CANUCS Cluster Download — 4 New Lensing Fields")
    print("=" * 60)
    print(f"  Clusters: {[c['name'] for c in clusters]}")
    print(f"  Filters:  {FILTERS}")
    print()

    results = {}
    for cluster in clusters:
        n = download_cluster(cluster)
        results[cluster["name"]] = n

    print(f"\n{'='*60}")
    print("  DOWNLOAD SUMMARY")
    print(f"{'='*60}")
    for name, count in results.items():
        status = "OK" if count >= 6 else "PARTIAL" if count > 0 else "FAILED"
        print(f"  {name:20s}: {count} filters [{status}]")

    print("\n  Next steps:")
    for c in clusters:
        print(f"    python run_pipeline_{c['name'].lower().replace('-','_')}.py")


if __name__ == "__main__":
    main()
