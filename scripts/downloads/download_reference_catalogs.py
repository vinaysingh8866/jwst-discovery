#!/usr/bin/env python3
"""Download all reference catalogs from VizieR TAP to local parquet files.

Usage:
    python3 download_reference_catalogs.py              # download missing catalogs
    python3 download_reference_catalogs.py --force       # re-download all
    python3 download_reference_catalogs.py --only jades_dr2_gs,chandra_7ms
"""
import argparse
import io
import logging
import sys
from pathlib import Path
import time
import urllib.request
import urllib.parse
import urllib.error
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import polars as pl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Ensure sidecar package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sidecar"))  # repo-root/sidecar, or pip install -e sidecar
from jwst_pipeline.catalog_registry import LOCAL_CATALOGS, get_catalogs_for_field, get_ref_catalogs_dir

TAP_ENDPOINT = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync"
MAX_ROWS = 200_000  # VizieR TAP row limit per query (use -out.max for larger)
REQUEST_TIMEOUT = 180  # seconds per HTTP request


def _quote(col: str) -> str:
    """Quote an ADQL column name."""
    return f'"{col}"'


def _build_adql(cat: dict) -> str:
    """Build the ADQL SELECT query for a catalog."""
    cols = [_quote(cat["ra_col"]), _quote(cat["dec_col"])]
    if cat.get("z_col"):
        cols.append(_quote(cat["z_col"]))
    if cat.get("extra_cols"):
        for vizier_name in cat["extra_cols"]:
            cols.append(_quote(vizier_name))

    select = ", ".join(cols)
    table = _quote(cat["tap_table"])
    query = f"SELECT {select} FROM {table}"
    if cat.get("where_clause"):
        query += f" WHERE {cat['where_clause']}"
    return query


def _tap_query(adql: str, timeout: int = REQUEST_TIMEOUT) -> str:
    """Execute a TAP sync query and return the response body as text."""
    params = urllib.parse.urlencode({
        "REQUEST": "doQuery",
        "LANG": "ADQL",
        "FORMAT": "csv",
        "QUERY": adql,
    })
    url = f"{TAP_ENDPOINT}?{params}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_csv(csv_text: str, cat: dict) -> "pl.DataFrame":
    """Parse TAP CSV response into a Polars DataFrame with standardized columns."""
    import polars as pl

    # Skip comment lines (start with #) and empty lines
    lines = [line for line in csv_text.strip().split("\n")
             if line and not line.startswith("#")]
    if len(lines) < 2:
        return pl.DataFrame()

    csv_clean = "\n".join(lines)
    df = pl.read_csv(io.StringIO(csv_clean), infer_schema_length=10000,
                     null_values=["", " ", "NaN", "nan"])

    if len(df) == 0:
        return df

    # Build rename map: original col → standardized name
    rename_map = {}
    ra_col = cat["ra_col"]
    dec_col = cat["dec_col"]

    for actual in df.columns:
        if actual.lower() == ra_col.lower() and actual != "RAJ2000":
            rename_map[actual] = "RAJ2000"
        elif actual.lower() == dec_col.lower() and actual != "DEJ2000":
            rename_map[actual] = "DEJ2000"

    z_col = cat.get("z_col")
    if z_col:
        for actual in df.columns:
            if actual.lower() == z_col.lower() and actual != "z":
                rename_map[actual] = "z"
                break

    if cat.get("extra_cols"):
        for vizier_name, output_name in cat["extra_cols"].items():
            for actual in df.columns:
                if actual.lower() == vizier_name.lower() and actual != output_name:
                    rename_map[actual] = output_name
                    break

    if rename_map:
        df = df.rename(rename_map)

    # Ensure RA/Dec are float64
    for col in ["RAJ2000", "DEJ2000"]:
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))

    if "z" in df.columns:
        df = df.with_columns(pl.col("z").cast(pl.Float64, strict=False))
        # GOODS-VIMOS uses 9.9999 as sentinel for "no redshift"
        if cat["name"] == "goods_vimos":
            df = df.with_columns(
                pl.when(pl.col("z") > 9.0).then(None).otherwise(pl.col("z")).alias("z")
            )

    # Drop rows with null RA or Dec
    df = df.filter(pl.col("RAJ2000").is_not_null() & pl.col("DEJ2000").is_not_null())

    # Keep only standardized columns
    keep = ["RAJ2000", "DEJ2000"]
    if "z" in df.columns:
        keep.append("z")
    if cat.get("extra_cols"):
        for output_name in cat["extra_cols"].values():
            if output_name in df.columns:
                keep.append(output_name)
    df = df.select([c for c in keep if c in df.columns])

    return df


def download_catalog(cat: dict, force: bool = False) -> bool:
    """Download a single catalog. Returns True on success."""
    import polars as pl

    out_dir = get_ref_catalogs_dir()
    out_path = out_dir / f"{cat['name']}.parquet"

    if out_path.exists() and not force:
        size = out_path.stat().st_size
        existing = pl.read_parquet(out_path)
        logger.info("  SKIP  %-20s  %s already exists (%d rows, %.1f KB)",
                     cat["name"], out_path.name, len(existing), size / 1024)
        return True

    logger.info("  DOWN  %-20s  %s", cat["name"], cat["tap_table"])

    # VizieR TAP: use -out.max=unlimited approach via simple SELECT
    # For large catalogs, VizieR returns up to ~200k rows by default
    adql = _build_adql(cat)

    try:
        logger.info("         querying...")
        csv_text = _tap_query(adql)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:500]
        except Exception:
            pass
        logger.error("  FAIL  %-20s  HTTP %d: %s", cat["name"], e.code, body[:200])
        return False
    except Exception as e:
        logger.error("  FAIL  %-20s  %s", cat["name"], e)
        return False

    df = _parse_csv(csv_text, cat)
    n_rows = len(df)

    if n_rows == 0:
        logger.warning("  EMPTY %-20s  no rows returned", cat["name"])
        return False

    df.write_parquet(out_path)
    size = out_path.stat().st_size

    ra = df["RAJ2000"].to_numpy()
    dec = df["DEJ2000"].to_numpy()
    z_info = ""
    if "z" in df.columns:
        z_valid = df["z"].drop_nulls().to_numpy()
        z_valid = z_valid[np.isfinite(z_valid)]
        if len(z_valid) > 0:
            z_info = f"  z [{np.nanmin(z_valid):.2f}, {np.nanmax(z_valid):.2f}] ({len(z_valid)} with z)"

    logger.info("  OK    %-20s  %d rows  RA [%.2f, %.2f]  Dec [%.2f, %.2f]%s  %.1f KB",
                cat["name"], n_rows,
                np.nanmin(ra), np.nanmax(ra),
                np.nanmin(dec), np.nanmax(dec),
                z_info, size / 1024)
    return True


def main():
    parser = argparse.ArgumentParser(description="Download VizieR reference catalogs")
    parser.add_argument("--force", action="store_true",
                        help="Re-download even if parquet already exists")
    parser.add_argument("--only", type=str, default=None,
                        help="Comma-separated list of catalog names to download")
    parser.add_argument("--field", type=str, default=None,
                        help="Field to download: goods_south, abell2744, macs0417, smacs0723, or 'all'")
    args = parser.parse_args()

    catalogs = LOCAL_CATALOGS
    if args.field and args.field != "all":
        catalogs = [c for c in get_catalogs_for_field(args.field) if "tap_table" in c]
        if not catalogs:
            logger.error("No VizieR catalogs for field: %s", args.field)
            sys.exit(1)
        logger.info("Filtered to field '%s': %d VizieR catalogs", args.field, len(catalogs))
    if args.only:
        names = set(args.only.split(","))
        catalogs = [c for c in LOCAL_CATALOGS if c["name"] in names]
        if not catalogs:
            logger.error("No catalogs matched: %s", args.only)
            sys.exit(1)

    logger.info("Downloading %d catalogs to %s", len(catalogs), get_ref_catalogs_dir())
    logger.info("=" * 70)

    ok = 0
    fail = 0
    skip = 0

    for cat in catalogs:
        out_path = get_ref_catalogs_dir() / f"{cat['name']}.parquet"
        existed = out_path.exists() and not args.force

        success = download_catalog(cat, force=args.force)
        if existed and success:
            skip += 1
        elif success:
            ok += 1
        else:
            fail += 1

        # Be polite between catalogs
        if not existed:
            time.sleep(2.0)

    logger.info("=" * 70)
    logger.info("Done: %d downloaded, %d skipped, %d failed", ok, skip, fail)


if __name__ == "__main__":
    main()
