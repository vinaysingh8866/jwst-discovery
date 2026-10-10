#!/usr/bin/env python3
"""Download reference catalogs from MAST HLSP (FITS tables) to local parquet.

Handles:
 - JADES HLSP photometric catalogs (GOODS-S DR2, GOODS-N DR3)
 - CANUCS DR1 catalog for MACS-J0417 (discovered via astroquery.mast)

Usage:
    python3 download_mast_catalogs.py              # download missing catalogs
    python3 download_mast_catalogs.py --force       # re-download all
    python3 download_mast_catalogs.py --only jades_hlsp_gs
    python3 download_mast_catalogs.py --field goods_south
"""
import argparse
import logging
import sys
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Ensure sidecar package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sidecar"))  # repo-root/sidecar, or pip install -e sidecar
from jwst_pipeline.catalog_registry import MAST_CATALOGS, get_ref_catalogs_dir

REQUEST_TIMEOUT = 600  # MAST FITS can be large

# RA/Dec column name candidates (case-insensitive search)
RA_CANDIDATES = ["RA", "ra", "RAJ2000", "RA_J2000", "ALPHA_J2000", "RA_ICRS", "RA_deg"]
DEC_CANDIDATES = ["DEC", "dec", "DEJ2000", "DE_J2000", "DELTA_J2000", "DE_ICRS", "DEC_deg", "Dec"]


def _find_column(columns: list[str], candidates: list[str]) -> str | None:
    """Find first matching column name (case-insensitive)."""
    col_lower = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in col_lower:
            return col_lower[cand.lower()]
    return None


def _download_fits(url: str, dest: Path) -> Path:
    """Download a FITS file from URL."""
    logger.info("  Downloading %s ...", url.split("/")[-1])
    req = urllib.request.Request(url, headers={
        "User-Agent": "jwst-discovery-pipeline/0.1",
    })
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        data = resp.read()
    dest.write_bytes(data)
    logger.info("  Downloaded %.1f MB", len(data) / 1e6)
    return dest


def _fits_to_parquet(fits_path: Path, cat: dict, out_path: Path) -> int:
    """Convert a MAST FITS catalog table to standardized parquet.

    Returns number of rows written.
    """
    import polars as pl
    from astropy.io import fits as afits
    from astropy.table import Table

    # List all HDUs and find the one with RA/Dec columns and most rows.
    # JADES HLSP catalogs have multiple HDUs (FILTER_INFO, PHOTOMETRY, FLAG, etc.)
    with afits.open(fits_path) as hdul:
        logger.info("  FITS HDUs: %s", [(i, h.name, type(h).__name__) for i, h in enumerate(hdul)])

    best_table = None
    best_rows = 0
    best_hdu = None

    for hdu_idx in range(10):  # try up to 10 HDUs
        try:
            t = Table.read(fits_path, hdu=hdu_idx)
        except Exception:
            continue
        if len(t) == 0 or len(t.colnames) < 2:
            continue
        # Check for RA/Dec-like columns
        ra_found = _find_column(t.colnames, RA_CANDIDATES)
        dec_found = _find_column(t.colnames, DEC_CANDIDATES)
        if ra_found and dec_found and len(t) > best_rows:
            best_table = t
            best_rows = len(t)
            best_hdu = hdu_idx

    t = best_table
    if t is None or len(t) == 0:
        logger.warning("  No data table with RA/Dec found in FITS file")
        return 0

    logger.info("  Using HDU %d: %d rows, %d columns", best_hdu, len(t), len(t.colnames))

    # Find RA/Dec columns
    ra_col = _find_column(t.colnames, RA_CANDIDATES)
    dec_col = _find_column(t.colnames, DEC_CANDIDATES)
    if not ra_col or not dec_col:
        logger.error("  Cannot find RA/Dec columns in: %s", t.colnames[:20])
        return 0

    logger.info("  RA col: %s, Dec col: %s", ra_col, dec_col)

    # Extract RA/Dec as float64
    ra = np.array(t[ra_col], dtype=np.float64)
    dec = np.array(t[dec_col], dtype=np.float64)

    # Look for photometric redshift
    z_candidates = ["zphot", "EAZY_z_a", "Z_ML", "PHOTOZ", "z_phot", "photo_z", "zbest",
                    "z_best", "z", "redshift", "ZPHOT", "Z_PHOT"]
    z_col = _find_column(t.colnames, z_candidates)
    z_vals = None
    if z_col:
        try:
            z_vals = np.array(t[z_col], dtype=np.float64)
            # Filter sentinel values
            z_vals[z_vals < 0] = np.nan
            z_vals[z_vals > 20] = np.nan
            n_valid = np.sum(np.isfinite(z_vals))
            logger.info("  Redshift col: %s (%d valid)", z_col, n_valid)
        except Exception:
            z_vals = None

    # Also check for a PHOTOZ HDU (JADES DR2/DR3 style)
    if z_vals is None:
        try:
            from astropy.io import fits as afits
            with afits.open(fits_path) as hdul:
                hdu_names = [h.name for h in hdul]
                for pz_name in ["PHOTOZ", "PHOTO_Z", "ZPHOT"]:
                    if pz_name in hdu_names:
                        pz_table = Table.read(fits_path, hdu=pz_name)
                        pz_col = _find_column(pz_table.colnames,
                                              ["EAZY_z_a", "Z_ML", "zphot", "z_phot",
                                               "zbest", "z", "ZPHOT", "z_a"])
                        if pz_col and len(pz_table) == len(t):
                            z_vals = np.array(pz_table[pz_col], dtype=np.float64)
                            z_vals[z_vals < 0] = np.nan
                            z_vals[z_vals > 20] = np.nan
                            logger.info("  Redshift from HDU %s col %s (%d valid)",
                                        pz_name, pz_col, np.sum(np.isfinite(z_vals)))
                            break
        except Exception:
            pass

    # Build DataFrame
    data = {"RAJ2000": ra, "DEJ2000": dec}
    if z_vals is not None:
        data["z"] = z_vals

    df = pl.DataFrame(data)

    # Drop rows with null/NaN coordinates
    df = df.filter(
        pl.col("RAJ2000").is_not_null()
        & pl.col("DEJ2000").is_not_null()
        & pl.col("RAJ2000").is_not_nan()
        & pl.col("DEJ2000").is_not_nan()
    )

    df.write_parquet(out_path)
    return len(df)


def download_mast_catalog(cat: dict, force: bool = False) -> bool:
    """Download a single MAST HLSP catalog. Returns True on success."""
    import polars as pl

    out_dir = get_ref_catalogs_dir()
    out_path = out_dir / f"{cat['name']}.parquet"

    if out_path.exists() and not force:
        existing = pl.read_parquet(out_path)
        logger.info("  SKIP  %-25s  %s already exists (%d rows)",
                     cat["name"], out_path.name, len(existing))
        return True

    url = cat.get("mast_url")
    if not url:
        logger.error("  FAIL  %-25s  No mast_url defined", cat["name"])
        return False

    logger.info("  DOWN  %-25s  %s", cat["name"], cat["label"])

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            fits_path = Path(tmpdir) / url.split("/")[-1]
            _download_fits(url, fits_path)
            n_rows = _fits_to_parquet(fits_path, cat, out_path)

        if n_rows > 0:
            # Print stats
            df = pl.read_parquet(out_path)
            ra = df["RAJ2000"].to_numpy()
            dec = df["DEJ2000"].to_numpy()
            z_info = ""
            if "z" in df.columns:
                z_valid = df["z"].drop_nulls().to_numpy()
                z_valid = z_valid[np.isfinite(z_valid)]
                if len(z_valid) > 0:
                    z_info = f"  z [{np.nanmin(z_valid):.2f}, {np.nanmax(z_valid):.2f}] ({len(z_valid)} with z)"
            logger.info("  OK    %-25s  %d rows  RA [%.2f, %.2f]  Dec [%.2f, %.2f]%s",
                         cat["name"], n_rows,
                         np.nanmin(ra), np.nanmax(ra),
                         np.nanmin(dec), np.nanmax(dec),
                         z_info)
            return True
        else:
            logger.warning("  EMPTY %-25s  no rows", cat["name"])
            return False

    except urllib.error.HTTPError as e:
        logger.error("  FAIL  %-25s  HTTP %d", cat["name"], e.code)
        return False
    except Exception as e:
        logger.error("  FAIL  %-25s  %s", cat["name"], e)
        return False


def main():
    parser = argparse.ArgumentParser(description="Download MAST HLSP reference catalogs")
    parser.add_argument("--force", action="store_true",
                        help="Re-download even if parquet already exists")
    parser.add_argument("--only", type=str, default=None,
                        help="Comma-separated list of catalog names to download")
    parser.add_argument("--field", type=str, default=None,
                        help="Field to download: goods_south, goods_north, macs0417, or 'all'")
    args = parser.parse_args()

    catalogs = MAST_CATALOGS
    if args.field and args.field != "all":
        catalogs = [c for c in MAST_CATALOGS if c.get("field") == args.field]
        if not catalogs:
            logger.error("No MAST catalogs for field: %s", args.field)
            sys.exit(1)
        logger.info("Filtered to field '%s': %d catalogs", args.field, len(catalogs))
    if args.only:
        names = set(args.only.split(","))
        catalogs = [c for c in MAST_CATALOGS if c["name"] in names]
        if not catalogs:
            logger.error("No catalogs matched: %s", args.only)
            sys.exit(1)

    logger.info("Downloading %d MAST catalogs to %s", len(catalogs), get_ref_catalogs_dir())
    logger.info("=" * 70)

    ok = 0
    fail = 0
    skip = 0

    for cat in catalogs:
        out_path = get_ref_catalogs_dir() / f"{cat['name']}.parquet"
        existed = out_path.exists() and not args.force

        success = download_mast_catalog(cat, force=args.force)
        if existed and success:
            skip += 1
        elif success:
            ok += 1
        else:
            fail += 1

        time.sleep(1.0)

    logger.info("=" * 70)
    logger.info("Done: %d downloaded, %d skipped, %d failed", ok, skip, fail)


if __name__ == "__main__":
    main()
