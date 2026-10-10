"""Reusable download functions for reference catalogs and HLSP mosaics.

Extracted from the standalone download_*.py scripts to be callable from
the sidecar __main__.py with progress callbacks for UI integration.
"""
import io
import logging
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

import numpy as np

from jwst_pipeline.catalog_registry import (
    HLSP_MOSAICS,
    get_catalogs_for_field,
    get_ref_catalogs_dir,
)

logger = logging.getLogger(__name__)

TAP_ENDPOINT = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync"
REQUEST_TIMEOUT = 180
MAST_TIMEOUT = 600

# RA/Dec column name candidates (case-insensitive search)
_RA_CANDIDATES = ["RA", "ra", "RAJ2000", "RA_J2000", "ALPHA_J2000", "RA_ICRS", "RA_deg"]
_DEC_CANDIDATES = ["DEC", "dec", "DEJ2000", "DE_J2000", "DELTA_J2000", "DE_ICRS", "DEC_deg", "Dec"]


# ---------------------------------------------------------------------------
# VizieR TAP catalog downloads
# ---------------------------------------------------------------------------

def _quote(col: str) -> str:
    return f'"{col}"'


def _build_adql(cat: dict) -> str:
    cols = [_quote(cat["ra_col"]), _quote(cat["dec_col"])]
    if cat.get("z_col"):
        cols.append(_quote(cat["z_col"]))
    select = ", ".join(cols)
    table = _quote(cat["tap_table"])
    query = f"SELECT {select} FROM {table}"
    if cat.get("where_clause"):
        query += f" WHERE {cat['where_clause']}"
    return query


def _tap_query(adql: str) -> str:
    params = urllib.parse.urlencode({
        "REQUEST": "doQuery",
        "LANG": "ADQL",
        "FORMAT": "csv",
        "QUERY": adql,
    })
    url = f"{TAP_ENDPOINT}?{params}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_csv(csv_text: str, cat: dict):
    import polars as pl

    lines = [line for line in csv_text.strip().split("\n")
             if line and not line.startswith("#")]
    if len(lines) < 2:
        return pl.DataFrame()

    csv_clean = "\n".join(lines)
    df = pl.read_csv(io.StringIO(csv_clean), infer_schema_length=10000,
                     null_values=["", " ", "NaN", "nan"])
    if len(df) == 0:
        return df

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

    if rename_map:
        df = df.rename(rename_map)

    for col in ["RAJ2000", "DEJ2000"]:
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))

    if "z" in df.columns:
        df = df.with_columns(pl.col("z").cast(pl.Float64, strict=False))
        if cat["name"] == "goods_vimos":
            df = df.with_columns(
                pl.when(pl.col("z") > 9.0).then(None).otherwise(pl.col("z")).alias("z")
            )

    df = df.filter(pl.col("RAJ2000").is_not_null() & pl.col("DEJ2000").is_not_null())

    keep = ["RAJ2000", "DEJ2000"]
    if "z" in df.columns:
        keep.append("z")
    df = df.select([c for c in keep if c in df.columns])

    return df


def download_vizier_catalog(cat: dict, force: bool = False,
                            progress_fn: Callable | None = None) -> dict:
    """Download a single VizieR TAP catalog. Returns status dict."""
    import polars as pl

    out_dir = get_ref_catalogs_dir()
    out_path = out_dir / f"{cat['name']}.parquet"

    if out_path.exists() and not force:
        existing = pl.read_parquet(out_path)
        return {"name": cat["name"], "status": "skip", "rows": len(existing)}

    if progress_fn:
        progress_fn(f"Querying VizieR: {cat['label']}...")

    adql = _build_adql(cat)
    try:
        csv_text = _tap_query(adql)
    except urllib.error.HTTPError as e:
        return {"name": cat["name"], "status": "fail", "rows": 0,
                "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"name": cat["name"], "status": "fail", "rows": 0,
                "error": str(e)}

    df = _parse_csv(csv_text, cat)
    if len(df) == 0:
        return {"name": cat["name"], "status": "fail", "rows": 0,
                "error": "No rows returned"}

    df.write_parquet(out_path)
    return {"name": cat["name"], "status": "ok", "rows": len(df)}


# ---------------------------------------------------------------------------
# MAST HLSP catalog downloads
# ---------------------------------------------------------------------------

def _find_column(columns: list[str], candidates: list[str]) -> str | None:
    col_lower = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in col_lower:
            return col_lower[cand.lower()]
    return None


def _download_fits_file(url: str, dest: Path) -> Path:
    req = urllib.request.Request(url, headers={
        "User-Agent": "jwst-discovery-pipeline/0.1",
    })
    with urllib.request.urlopen(req, timeout=MAST_TIMEOUT) as resp:
        data = resp.read()
    dest.write_bytes(data)
    return dest


def _fits_to_parquet(fits_path: Path, out_path: Path) -> int:
    import polars as pl
    from astropy.io import fits as afits
    from astropy.table import Table

    best_table = None
    best_rows = 0

    for hdu_idx in range(10):
        try:
            t = Table.read(fits_path, hdu=hdu_idx)
        except Exception:
            continue
        if len(t) == 0 or len(t.colnames) < 2:
            continue
        ra_found = _find_column(t.colnames, _RA_CANDIDATES)
        dec_found = _find_column(t.colnames, _DEC_CANDIDATES)
        if ra_found and dec_found and len(t) > best_rows:
            best_table = t
            best_rows = len(t)

    t = best_table
    if t is None or len(t) == 0:
        return 0

    ra_col = _find_column(t.colnames, _RA_CANDIDATES)
    dec_col = _find_column(t.colnames, _DEC_CANDIDATES)
    if not ra_col or not dec_col:
        return 0

    ra = np.array(t[ra_col], dtype=np.float64)
    dec = np.array(t[dec_col], dtype=np.float64)

    z_candidates = ["zphot", "EAZY_z_a", "Z_ML", "PHOTOZ", "z_phot", "photo_z",
                    "zbest", "z_best", "z", "redshift", "ZPHOT", "Z_PHOT"]
    z_col_name = _find_column(t.colnames, z_candidates)
    z_vals = None
    if z_col_name:
        try:
            z_vals = np.array(t[z_col_name], dtype=np.float64)
            z_vals[z_vals < 0] = np.nan
            z_vals[z_vals > 20] = np.nan
        except Exception:
            z_vals = None

    if z_vals is None:
        try:
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
                            break
        except Exception:
            pass

    data = {"RAJ2000": ra, "DEJ2000": dec}
    if z_vals is not None:
        data["z"] = z_vals

    df = pl.DataFrame(data)
    df = df.filter(
        pl.col("RAJ2000").is_not_null() & pl.col("DEJ2000").is_not_null()
        & pl.col("RAJ2000").is_not_nan() & pl.col("DEJ2000").is_not_nan()
    )
    df.write_parquet(out_path)
    return len(df)


def download_mast_catalog(cat: dict, force: bool = False,
                          progress_fn: Callable | None = None) -> dict:
    """Download a single MAST HLSP catalog. Returns status dict."""
    import polars as pl

    out_dir = get_ref_catalogs_dir()
    out_path = out_dir / f"{cat['name']}.parquet"

    if out_path.exists() and not force:
        existing = pl.read_parquet(out_path)
        return {"name": cat["name"], "status": "skip", "rows": len(existing)}

    url = cat.get("mast_url")
    if not url:
        return {"name": cat["name"], "status": "fail", "rows": 0,
                "error": "No mast_url defined"}

    if progress_fn:
        progress_fn(f"Downloading MAST: {cat['label']}...")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            fits_path = Path(tmpdir) / url.split("/")[-1]
            _download_fits_file(url, fits_path)
            n_rows = _fits_to_parquet(fits_path, out_path)

        if n_rows > 0:
            return {"name": cat["name"], "status": "ok", "rows": n_rows}
        else:
            return {"name": cat["name"], "status": "fail", "rows": 0,
                    "error": "No rows parsed from FITS"}
    except Exception as e:
        return {"name": cat["name"], "status": "fail", "rows": 0,
                "error": str(e)}


# ---------------------------------------------------------------------------
# Status checking
# ---------------------------------------------------------------------------

def check_reference_catalogs(field_name: str) -> list[dict]:
    """Check which reference catalogs exist for a field. Returns per-catalog status."""
    import polars as pl

    catalogs = get_catalogs_for_field(field_name)
    out_dir = get_ref_catalogs_dir()
    results = []

    for cat in catalogs:
        parquet = out_dir / f"{cat['name']}.parquet"
        if parquet.exists():
            try:
                df = pl.read_parquet(parquet)
                results.append({
                    "name": cat["name"],
                    "label": cat.get("label", cat["name"]),
                    "status": "downloaded",
                    "rows": len(df),
                    "size_kb": round(parquet.stat().st_size / 1024, 1),
                })
            except Exception:
                results.append({
                    "name": cat["name"],
                    "label": cat.get("label", cat["name"]),
                    "status": "corrupt",
                    "rows": 0,
                    "size_kb": 0,
                })
        else:
            results.append({
                "name": cat["name"],
                "label": cat.get("label", cat["name"]),
                "status": "missing",
                "rows": 0,
                "size_kb": 0,
            })

    return results


def check_hlsp_mosaics(field_name: str, workspace: str) -> list[dict]:
    """Check which HLSP mosaics exist in the workspace raw/ dir."""
    hlsp = HLSP_MOSAICS.get(field_name)
    if not hlsp:
        return []

    raw_dir = Path(workspace) / "raw"
    results = []

    for filt in hlsp["filters"]:
        # Check for any FITS file matching this filter in raw/
        found = False
        size_mb = 0.0
        if raw_dir.exists():
            for fits_file in raw_dir.glob("*.fits"):
                if filt.lower() in fits_file.name.lower():
                    found = True
                    size_mb = round(fits_file.stat().st_size / 1e6, 1)
                    break

        results.append({
            "filter": filt,
            "status": "downloaded" if found else "missing",
            "size_mb": size_mb,
        })

    return results


# ---------------------------------------------------------------------------
# HLSP Mosaic downloads
# ---------------------------------------------------------------------------

def download_hlsp_mosaic(url: str, dest: Path, filter_name: str,
                         progress_fn: Callable | None = None,
                         cancel_event=None) -> dict:
    """Chunked HTTP download with byte-level progress. Returns status dict."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "jwst-discovery-pipeline/0.1",
        })
        with urllib.request.urlopen(req, timeout=MAST_TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            dest.parent.mkdir(parents=True, exist_ok=True)

            downloaded = 0
            chunk_size = 1024 * 1024  # 1 MB chunks
            with open(dest, "wb") as f:
                while True:
                    if cancel_event and cancel_event.is_set():
                        return {"filter": filter_name, "status": "cancelled", "size_mb": 0}

                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    if progress_fn:
                        progress_fn(filter_name, "downloading", downloaded, total)

        size_mb = round(downloaded / 1e6, 1)
        if progress_fn:
            progress_fn(filter_name, "done", downloaded, total)
        return {"filter": filter_name, "status": "ok", "size_mb": size_mb}

    except Exception as e:
        return {"filter": filter_name, "status": "fail", "size_mb": 0,
                "error": str(e)}
