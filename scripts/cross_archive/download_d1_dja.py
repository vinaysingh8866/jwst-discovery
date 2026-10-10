"""
D1 — DAWN JWST Archive (DJA) deep photometric catalogs.

DJA grizli-v2 bucket doesn't allow public LIST, but individual files are
fetchable via HTTPS. Try a list of known DJA catalog URLs.

Known patterns:
    https://s3.amazonaws.com/grizli-v2/JwstMosaics/v7/{field}-grizli-v{ver}-fix_phot_apcorr.fits
    https://s3.amazonaws.com/grizli-v2/JwstMosaics/v7/{field}-grizli-v{ver}-fix.eazypy.zout.fits
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import boto3
from botocore import UNSIGNED
from botocore.config import Config

warnings.filterwarnings("ignore")

OUT_DIR = Path(os.path.expanduser("~/.jwst-discovery/reference_catalogs/dja_v7"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Field name → DJA mosaic prefix to look for
DJA_FIELDS = [
    "abell2744clu",       # UNCOVER
    "abell370",
    "abells1063",
    "macs0416",
    "macs1149",
    "bullet",
    "elgordo",
    "macs0647",
    "ceers-full",         # CEERS
    "primer-cosmos",
    "primer-uds",
    "ngdeep",
    "jades-gds-deep",
    "jades-gds-medium-deep",
    "uncover",
    "smacs0723",
    "rxj2129",
    "macs1423",
    "spt0615",            # Cosmic Gems
    "cosmos-web",
]


def get_s3():
    return boto3.client(
        "s3",
        config=Config(signature_version=UNSIGNED, max_pool_connections=8,
                      retries={"max_attempts": 10, "mode": "adaptive"}),
    )


def list_dja_objects(client, prefix: str = "JwstMosaics/v7/"):
    """List the grizli-v7 bucket and filter to catalog files (small)."""
    bucket = "grizli-v2"
    found = []
    paginator = client.get_paginator("list_objects_v2")
    try:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                fn = key.rsplit("/", 1)[-1]
                # Filter to catalog/photometry/eazy/redshift outputs
                if any(s in fn.lower() for s in (
                    "phot.fits", "phot.cat", ".cat.fits", "_eazy.fits",
                    "fits.gz", "_zout.fits", "_zphot.fits",
                    "-fix.fits", "matched.fits",
                )):
                    found.append({"key": key, "size": obj["Size"], "filename": fn})
    except Exception as e:
        print(f"  list error: {e}")
    return found


def download(client, key: str, local: Path) -> bool:
    if local.exists() and local.stat().st_size > 0:
        return True
    local.parent.mkdir(parents=True, exist_ok=True)
    try:
        client.download_file("grizli-v2", key, str(local))
        return True
    except Exception as e:
        print(f"      FAIL: {e}")
        return False


def try_https(url: str, local: Path) -> bool:
    """Try downloading a known DJA URL via HTTPS."""
    if local.exists() and local.stat().st_size > 0:
        return True
    import requests
    try:
        with requests.get(url, stream=True, timeout=30) as r:
            if r.status_code != 200:
                return False
            total_size = int(r.headers.get("content-length", 0))
            if total_size == 0:
                return False
            local.parent.mkdir(parents=True, exist_ok=True)
            with local.open("wb") as f:
                for chunk in r.iter_content(1 << 16):
                    if chunk:
                        f.write(chunk)
        return local.stat().st_size > 0
    except Exception:
        return False


def main() -> None:
    # Direct URLs proven to work (extracted from the project's catalog_registry.py)
    # plus additional candidates we want to try for fields that don't have one yet.
    base = "https://s3.amazonaws.com/grizli-v2/JwstMosaics/v7"

    # Combine known-good with new field combinations to try
    all_urls = [
        # KNOWN GOOD (extracted from catalog_registry.py):
        f"{base}/plck-g165p67-v7.4_phot.fits",
        f"{base}/sunrise-grizli-v7.0-fix_phot_apcorr.fits",
        f"{base}/ceers-full-grizli-v7.4_phot.fits",
        f"{base}/ngdeep-grizli-v7.2_phot.fits",
        f"{base}/rxj2129-grizli-v7.0-fix_phot_apcorr.fits",
        f"{base}/macs0647-grizli-v7.0-fix_phot_apcorr.fits",
        f"{base}/elgordo-grizli-v7.0-fix_phot_apcorr.fits",
        f"{base}/abell1689-grizli-v7.4_phot.fits",
        f"{base}/clg-1212p2733-v7.4_phot.fits",
        f"{base}/plck-g191p62-v7.4_phot.fits",
        f"{base}/abells1063-grizli-v7.5_phot.fits",
        # NEW CANDIDATES — try to find ones we don't have yet:
        # GOODS-S deep (the field with our 49 ALMA-dark candidates) — try every plausible name
        f"{base}/gds-grizli-v7.0-fix_phot_apcorr.fits",
        f"{base}/gds-grizli-v7.2_phot.fits",
        f"{base}/gds-grizli-v7.4_phot.fits",
        f"{base}/gds-grizli-v7.5_phot.fits",
        f"{base}/gdsfp-grizli-v7.4_phot.fits",
        f"{base}/gdsfn-grizli-v7.4_phot.fits",
        f"{base}/jades-gds-deep-grizli-v7.0-fix_phot_apcorr.fits",
        f"{base}/jades-gds-grizli-v7.4_phot.fits",
        f"{base}/jades-gds-grizli-v7.5_phot.fits",
        f"{base}/jades-gs-grizli-v7.4_phot.fits",
        f"{base}/jades-deep-grizli-v7.4_phot.fits",
        f"{base}/jades-gds-deep-grizli-v7.4_phot.fits",
        f"{base}/jades-gds-deep-grizli-v7.5_phot.fits",
        f"{base}/jades-gds-medium-grizli-v7.4_phot.fits",
        f"{base}/jades-gds-medium-deep-grizli-v7.5_phot.fits",
        f"{base}/goods-s-grizli-v7.4_phot.fits",
        # NGDEEP variations
        f"{base}/ngdeep-grizli-v7.0-fix_phot_apcorr.fits",
        f"{base}/ngdeep-grizli-v7.4_phot.fits",
        f"{base}/ngdeep-grizli-v7.5_phot.fits",
        # UDS / PRIMER
        f"{base}/primer-uds-grizli-v7.4_phot.fits",
        f"{base}/primer-uds-grizli-v7.5_phot.fits",
        f"{base}/uds-grizli-v7.4_phot.fits",
        f"{base}/uds-grizli-v7.5_phot.fits",
        f"{base}/sxds-grizli-v7.4_phot.fits",
        # PRIMER COSMOS
        f"{base}/primer-cosmos-grizli-v7.4_phot.fits",
        f"{base}/primer-cosmos-grizli-v7.5_phot.fits",
        f"{base}/cosmos-grizli-v7.4_phot.fits",
        f"{base}/cosmos-web-grizli-v7.4_phot.fits",
        f"{base}/cosmos-web-grizli-v7.5_phot.fits",
        # Cluster fields not yet in our registry
        f"{base}/abell2744clu-grizli-v7.0-fix_phot_apcorr.fits",
        f"{base}/abell2744clu-grizli-v7.4_phot.fits",
        f"{base}/abell2744clu-grizli-v7.5_phot.fits",
        f"{base}/abell2744-grizli-v7.4_phot.fits",
        f"{base}/uncover-grizli-v7.5_phot.fits",
        f"{base}/uncover-grizli-v7.4_phot.fits",
        f"{base}/abell370-grizli-v7.5_phot.fits",
        f"{base}/abell370-grizli-v7.4_phot.fits",
        f"{base}/macs0416-grizli-v7.4_phot.fits",
        f"{base}/macs0416-grizli-v7.5_phot.fits",
        f"{base}/macs1149-grizli-v7.4_phot.fits",
        f"{base}/macs1149-grizli-v7.5_phot.fits",
        f"{base}/macs1423-grizli-v7.4_phot.fits",
        f"{base}/macs1423-grizli-v7.5_phot.fits",
        f"{base}/smacs0723-grizli-v7.4_phot.fits",
        f"{base}/smacs0723-grizli-v7.5_phot.fits",
        f"{base}/bullet-grizli-v7.4_phot.fits",
        f"{base}/ms0451-grizli-v7.4_phot.fits",
        f"{base}/ms0451-grizli-v7.5_phot.fits",
        # PEARLS NEP
        f"{base}/nep-grizli-v7.4_phot.fits",
        f"{base}/pearls-nep-grizli-v7.4_phot.fits",
        f"{base}/spt0615-grizli-v7.4_phot.fits",
        f"{base}/cosmic-gems-grizli-v7.4_phot.fits",
        # eazypy zout (photo-z output)
        f"{base}/jades-gds-deep-grizli-v7.4.eazypy.zout.fits",
        f"{base}/ngdeep-grizli-v7.4.eazypy.zout.fits",
        f"{base}/ngdeep-grizli-v7.2.eazypy.zout.fits",
        f"{base}/ceers-full-grizli-v7.4.eazypy.zout.fits",
    ]

    print(f"Trying {len(all_urls)} DJA URLs via HTTPS...\n")
    n_ok = 0
    for url in all_urls:
        fn = url.rsplit("/", 1)[-1]
        local = OUT_DIR / fn
        if local.exists() and local.stat().st_size > 0:
            print(f"  cached: {fn}  ({local.stat().st_size/1e6:.0f} MB)")
            n_ok += 1
            continue
        print(f"  {fn}...", end=" ")
        ok = try_https(url, local)
        if ok:
            sz = local.stat().st_size
            print(f"OK ({sz/1e6:.0f} MB)")
            n_ok += 1
        else:
            print("404")

    print(f"\nSuccessfully downloaded: {n_ok} / {len(all_urls)}")


if __name__ == "__main__":
    main()
