#!/usr/bin/env python3
"""Download major public spectroscopic redshift catalogs for JWST fields.

Saves to ~/.jwst-discovery/reference_catalogs/specz/ as parquet files,
each with standardized columns: ra, dec, z_spec, z_spec_quality, survey.

Usage:
    python3 scripts/download_public_specz.py              # download all
    python3 scripts/download_public_specz.py --force      # re-download all
    python3 scripts/download_public_specz.py --only dja   # specific catalog

Catalogs downloaded:
  1. DJA (DAWN JWST Archive) — 80k+ NIRSpec spectra compiled (Zenodo v4.4)
  2. JADES DR4 — NIRSpec spectroscopy for ~4000 galaxies in GOODS fields
  3. UNCOVER DR4 — NIRSpec PRISM spec-z for ~700 galaxies in Abell 2744
  4. ALT DR1 — NIRCam grism spec-z for 1630 sources in Abell 2744
  5. GLASS-JWST — NIRSpec spec-z from Mascia+2024 (MAST HLSP)
"""
import argparse
import gzip
import io
import logging
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
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
from jwst_pipeline.catalog_registry import get_ref_catalogs_dir

SPECZ_DIR = get_ref_catalogs_dir() / "specz"
REQUEST_TIMEOUT = 300  # seconds

# VizieR TAP endpoint
TAP_ENDPOINT = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync"


def _ensure_dir():
    """Create the specz output directory."""
    SPECZ_DIR.mkdir(parents=True, exist_ok=True)


def _download_url(url: str, timeout: int = REQUEST_TIMEOUT) -> bytes:
    """Download a URL and return raw bytes."""
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={
        "User-Agent": "jwst-discovery-pipeline/1.0 (specz-download)"
    })
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


def _tap_query_csv(adql: str, timeout: int = REQUEST_TIMEOUT) -> str:
    """Execute a VizieR TAP sync query and return CSV text."""
    params = urllib.parse.urlencode({
        "REQUEST": "doQuery",
        "LANG": "ADQL",
        "FORMAT": "csv",
        "QUERY": adql,
    })
    url = f"{TAP_ENDPOINT}?{params}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "jwst-discovery-pipeline/1.0 (specz-download)"
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_csv_to_df(csv_text: str):
    """Parse CSV text (from TAP or direct) into a Polars DataFrame."""
    import polars as pl
    lines = [line.strip("\r") for line in csv_text.strip().split("\n")
             if line.strip() and not line.startswith("#")]
    if len(lines) < 2:
        return pl.DataFrame()
    csv_clean = "\n".join(lines)
    return pl.read_csv(io.StringIO(csv_clean), infer_schema_length=10000,
                       null_values=["", " ", "NaN", "nan", "--", "None"])


def _save_and_report(df, name: str, out_path: Path):
    """Write parquet and log summary."""
    if len(df) == 0:
        logger.warning("  EMPTY %-25s  no rows", name)
        return False
    df.write_parquet(out_path)
    size_kb = out_path.stat().st_size / 1024

    ra = df["ra"].to_numpy()
    dec = df["dec"].to_numpy()
    z = df["z_spec"].drop_nulls().to_numpy()
    z = z[np.isfinite(z)]

    z_info = ""
    if len(z) > 0:
        z_info = f"  z [{np.nanmin(z):.3f}, {np.nanmax(z):.3f}]"

    logger.info("  OK    %-25s  %d sources  RA [%.2f, %.2f]  Dec [%.2f, %.2f]%s  %.1f KB",
                name, len(df),
                np.nanmin(ra), np.nanmax(ra),
                np.nanmin(dec), np.nanmax(dec),
                z_info, size_kb)
    return True


# ============================================================================
# 1. DJA — DAWN JWST Archive NIRSpec merged table v4.4
# ============================================================================
def download_dja(force: bool = False) -> bool:
    """Download DJA v4.4 emission line / redshift catalog from Zenodo.

    ~80k public NIRSpec spectra with visually-inspected redshifts and grades.
    Key columns: ra, dec, z_best, grade (0=DQ, 1=no features, 2=ambiguous, 3=robust)
    """
    import polars as pl

    name = "dja_nirspec_specz"
    out_path = SPECZ_DIR / f"{name}.parquet"
    if out_path.exists() and not force:
        existing = pl.read_parquet(out_path)
        logger.info("  SKIP  %-25s  already exists (%d rows)", name, len(existing))
        return True

    logger.info("  DOWN  %-25s  DJA Zenodo v4.4 (CSV.gz ~133 MB)", name)

    url = "https://zenodo.org/records/15472354/files/dja_msaexp_emission_lines_v4.4.csv.gz?download=1"
    try:
        raw = _download_url(url, timeout=600)
    except Exception as e:
        logger.error("  FAIL  %-25s  %s", name, e)
        return False

    logger.info("         downloaded %.1f MB, decompressing...", len(raw) / 1e6)

    csv_text = gzip.decompress(raw).decode("utf-8", errors="replace")
    del raw

    logger.info("         parsing CSV...")
    df = _parse_csv_to_df(csv_text)
    del csv_text

    if len(df) == 0:
        logger.warning("  EMPTY %-25s  no rows parsed", name)
        return False

    logger.info("         %d total rows, filtering to graded spectra with z_best...", len(df))

    # Keep only rows with a valid z_best and grade >= 2 (solid or robust)
    required = ["ra", "dec", "z_best", "grade"]
    for c in required:
        if c not in df.columns:
            logger.error("  FAIL  %-25s  missing column '%s'. Columns: %s",
                         name, c, df.columns[:20])
            return False

    df = df.with_columns([
        pl.col("ra").cast(pl.Float64, strict=False),
        pl.col("dec").cast(pl.Float64, strict=False),
        pl.col("z_best").cast(pl.Float64, strict=False),
        pl.col("grade").cast(pl.Int32, strict=False),
    ])

    # Filter: valid coords, valid z_best, grade >= 2
    df = df.filter(
        pl.col("ra").is_not_null() &
        pl.col("dec").is_not_null() &
        pl.col("z_best").is_not_null() &
        pl.col("z_best").is_finite() &
        (pl.col("z_best") >= 0) &
        (pl.col("grade") >= 2)
    )

    # Deduplicate: keep highest grade per unique (ra, dec) within 0.3 arcsec
    # DJA has multiple grating observations per source — z_best is already the
    # best estimate, so we just need to deduplicate by unique source.
    # Use the 'file' column root or simply the ra/dec pair rounded.
    # Actually, z_best is already per-unique-source from DJA.
    # Just keep unique rows by (ra, dec, z_best).
    n_before = len(df)
    df = df.unique(subset=["ra", "dec", "z_best"])
    logger.info("         deduplicated %d -> %d unique (ra, dec, z_best)", n_before, len(df))

    # Map grade to quality: 3=secure, 2=solid
    quality_map = {3: "secure", 2: "solid"}
    df = df.with_columns(
        pl.col("grade").replace_strict(quality_map, default="solid").alias("z_spec_quality")
    )

    # Standardize columns
    df = df.select([
        pl.col("ra"),
        pl.col("dec"),
        pl.col("z_best").alias("z_spec"),
        pl.col("z_spec_quality"),
        pl.lit("DJA-NIRSpec-v4.4").alias("survey"),
    ])

    return _save_and_report(df, name, out_path)


# ============================================================================
# 2. JADES DR4 — Combined NIRSpec spectroscopy (GOODS-S + GOODS-N)
# ============================================================================
def download_jades_specz(force: bool = False) -> bool:
    """Download JADES DR4 combined spectroscopic catalog from jades.herts.ac.uk.

    ~4000 galaxies with NIRSpec prism+grating spectroscopy in GOODS fields.
    """
    import polars as pl

    name = "jades_dr4_specz"
    out_path = SPECZ_DIR / f"{name}.parquet"
    if out_path.exists() and not force:
        existing = pl.read_parquet(out_path)
        logger.info("  SKIP  %-25s  already exists (%d rows)", name, len(existing))
        return True

    logger.info("  DOWN  %-25s  JADES DR4 combined FITS (~90 MB)", name)

    url = "https://jades.herts.ac.uk/DR4/Combined_DR4_external_v1.2.1.fits"
    try:
        raw = _download_url(url, timeout=600)
    except Exception as e:
        logger.error("  FAIL  %-25s  %s", name, e)
        return False

    logger.info("         downloaded %.1f MB, parsing FITS...", len(raw) / 1e6)

    # Parse FITS — JADES DR4 Combined has:
    #   HDU 1 (Obs_info): RA_TARG, Dec_TARG, z_Spec, z_Spec_flag, z_PRISM, z_R1000
    from astropy.io import fits
    with io.BytesIO(raw) as buf:
        with fits.open(buf) as hdul:
            # Use HDU 1 (Obs_info) which has RA_TARG and Dec_TARG
            if len(hdul) < 2 or not hasattr(hdul[1], 'columns'):
                logger.error("  FAIL  %-25s  unexpected FITS structure", name)
                return False

            data = hdul[1].data
            col_names = [c.name for c in hdul[1].columns]
            logger.info("         HDU 1 (%s): %d cols, %d rows",
                        hdul[1].name, len(col_names), len(data))

            # JADES DR4 columns: RA_TARG, Dec_TARG, z_Spec, z_Spec_flag
            ra_col = "RA_TARG"
            dec_col = "Dec_TARG"
            z_col = "z_Spec"
            qual_col = "z_Spec_flag"

            for required in [ra_col, dec_col, z_col]:
                if required not in col_names:
                    logger.error("  FAIL  %-25s  missing column '%s'. Available: %s",
                                 name, required, col_names[:30])
                    return False

            logger.info("         RA=%s, Dec=%s, z=%s, qual=%s", ra_col, dec_col, z_col, qual_col)

            ra_arr = np.array(data[ra_col], dtype=np.float64)
            dec_arr = np.array(data[dec_col], dtype=np.float64)
            z_arr = np.array(data[z_col], dtype=np.float64)

            q_arr = None
            if qual_col in col_names:
                try:
                    q_arr = np.array(data[qual_col])
                except Exception:
                    pass

    # Build dataframe
    build_dict = {
        "ra": ra_arr,
        "dec": dec_arr,
        "z_spec": z_arr,
    }
    df = pl.DataFrame(build_dict)

    if q_arr is not None:
        df = df.with_columns(pl.Series("z_spec_quality_raw", q_arr.astype(str)))
    else:
        df = df.with_columns(pl.lit("unknown").alias("z_spec_quality_raw"))

    # Filter valid: z_spec > 0 and finite
    df = df.with_columns([
        pl.col("ra").cast(pl.Float64, strict=False),
        pl.col("dec").cast(pl.Float64, strict=False),
        pl.col("z_spec").cast(pl.Float64, strict=False),
    ])
    df = df.filter(
        pl.col("ra").is_not_null() &
        pl.col("dec").is_not_null() &
        pl.col("z_spec").is_not_null() &
        pl.col("z_spec").is_finite() &
        (pl.col("z_spec") > 0)
    )

    # Deduplicate by Unique_ID would be ideal but RA/Dec/z is sufficient
    n_before = len(df)
    df = df.unique(subset=["ra", "dec", "z_spec"])
    logger.info("         deduplicated %d -> %d unique (ra, dec, z_spec)", n_before, len(df))

    # Map quality flag
    df = df.with_columns(
        pl.col("z_spec_quality_raw").alias("z_spec_quality")
    )
    df = df.select([
        pl.col("ra"),
        pl.col("dec"),
        pl.col("z_spec"),
        pl.col("z_spec_quality"),
        pl.lit("JADES-DR4-NIRSpec").alias("survey"),
    ])

    return _save_and_report(df, name, out_path)


# ============================================================================
# 3. UNCOVER DR4 — NIRSpec PRISM spec-z (Abell 2744)
# ============================================================================
def download_uncover_specz(force: bool = False) -> bool:
    """Download UNCOVER DR4.1.1 spectroscopic redshift catalog from Zenodo.

    ~700 galaxies with NIRSpec PRISM spectroscopy in Abell 2744.
    """
    import polars as pl

    name = "uncover_dr4_specz"
    out_path = SPECZ_DIR / f"{name}.parquet"
    if out_path.exists() and not force:
        existing = pl.read_parquet(out_path)
        logger.info("  SKIP  %-25s  already exists (%d rows)", name, len(existing))
        return True

    logger.info("  DOWN  %-25s  UNCOVER DR4.1.1 Zenodo FITS", name)

    # Zenodo API: must use /content endpoint for actual file download
    url = "https://zenodo.org/api/records/14559840/files/uncover-msa-default_drz-DR4.1.1-zspec.fits/content"
    try:
        raw = _download_url(url, timeout=120)
    except Exception as e:
        logger.error("  FAIL  %-25s  %s", name, e)
        return False

    logger.info("         downloaded %.1f KB, parsing FITS...", len(raw) / 1024)

    from astropy.io import fits
    with io.BytesIO(raw) as buf:
        with fits.open(buf) as hdul:
            # Find the table HDU
            table_hdu = None
            for i, hdu in enumerate(hdul):
                if hasattr(hdu, 'columns') and hdu.columns is not None and len(hdu.columns) > 0:
                    table_hdu = hdu
                    logger.info("         using HDU %d: %s (%d cols, %d rows)",
                                i, hdu.name, len(hdu.columns), len(hdu.data))
                    break
            if table_hdu is None:
                logger.error("  FAIL  %-25s  no table HDU found", name)
                return False

            data = table_hdu.data
            col_names = [c.name for c in table_hdu.columns]
            logger.info("         columns: %s", col_names[:30])

            # Find columns — be careful to separate z vs quality columns.
            # UNCOVER DR4 has: ra, dec, z_spec, flag_zspec_qual
            ra_col = dec_col = z_col = qual_col = None

            # First pass: identify quality/flag columns (to exclude from z search)
            flag_cols = set()
            for c in col_names:
                cl = c.lower()
                if 'flag' in cl or 'qual' in cl or 'grade' in cl:
                    flag_cols.add(c)

            # Find RA/Dec
            for c in col_names:
                cl = c.lower()
                if cl in ('ra', 'raj2000', 'ra_spec', 'ra_msa'):
                    ra_col = c
                elif cl in ('dec', 'dej2000', 'dec_spec', 'dec_msa', 'decl'):
                    dec_col = c

            # Find z column (exclude flag columns!)
            for c in col_names:
                if c in flag_cols:
                    continue
                cl = c.lower()
                if cl in ('z_spec', 'zspec', 'z', 'redshift'):
                    z_col = c
                    break
            if z_col is None:
                for c in col_names:
                    if c in flag_cols:
                        continue
                    cl = c.lower()
                    if cl.startswith('z') and ('spec' in cl or cl == 'z'):
                        z_col = c
                        break

            # Find quality column
            for c in col_names:
                cl = c.lower()
                if 'flag' in cl and 'qual' in cl:
                    qual_col = c
                    break
            if qual_col is None:
                for c in col_names:
                    cl = c.lower()
                    if cl.startswith('flag') and 'zspec' in cl:
                        qual_col = c
                        break
            if qual_col is None:
                for c in flag_cols:
                    qual_col = c
                    break

            logger.info("         ra=%s, dec=%s, z=%s, qual=%s", ra_col, dec_col, z_col, qual_col)

            if not all([ra_col, dec_col, z_col]):
                logger.error("  FAIL  %-25s  missing required columns. Available: %s",
                             name, col_names)
                return False

            ra_arr = np.array(data[ra_col], dtype=np.float64)
            dec_arr = np.array(data[dec_col], dtype=np.float64)
            z_arr = np.array(data[z_col], dtype=np.float64)

            q_arr = None
            if qual_col:
                try:
                    q_arr = np.array(data[qual_col])
                except Exception:
                    pass

    build_dict = {"ra": ra_arr, "dec": dec_arr, "z_spec": z_arr}
    df = pl.DataFrame(build_dict)

    if q_arr is not None:
        df = df.with_columns(pl.Series("z_spec_quality_raw", q_arr.astype(str)))
        # UNCOVER recommends flag_zspec_qual=3 (secure) or 2 (solid)
        df = df.with_columns(
            pl.when(pl.col("z_spec_quality_raw") == "3").then(pl.lit("secure"))
            .when(pl.col("z_spec_quality_raw") == "2").then(pl.lit("solid"))
            .when(pl.col("z_spec_quality_raw") == "1").then(pl.lit("tentative"))
            .otherwise(pl.lit("unknown"))
            .alias("z_spec_quality")
        )
    else:
        df = df.with_columns(pl.lit("unknown").alias("z_spec_quality"))

    df = df.with_columns([
        pl.col("z_spec").cast(pl.Float64, strict=False),
    ])
    df = df.filter(
        pl.col("ra").is_not_null() &
        pl.col("dec").is_not_null() &
        pl.col("z_spec").is_not_null() &
        pl.col("z_spec").is_finite() &
        (pl.col("z_spec") > 0)
    )

    df = df.select([
        pl.col("ra"),
        pl.col("dec"),
        pl.col("z_spec"),
        pl.col("z_spec_quality"),
        pl.lit("UNCOVER-DR4-NIRSpec").alias("survey"),
    ])

    return _save_and_report(df, name, out_path)


# ============================================================================
# 4. ALT DR1 — All the Little Things (Abell 2744 grism spectroscopy)
# ============================================================================
def download_alt_specz(force: bool = False) -> bool:
    """Download ALT DR1 spectroscopic catalog from Zenodo.

    1630 sources at z=0.2-8.5 from NIRCam grism spectroscopy of Abell 2744.
    """
    import polars as pl

    name = "alt_dr1_specz"
    out_path = SPECZ_DIR / f"{name}.parquet"
    if out_path.exists() and not force:
        existing = pl.read_parquet(out_path)
        logger.info("  SKIP  %-25s  already exists (%d rows)", name, len(existing))
        return True

    logger.info("  DOWN  %-25s  ALT DR1 Zenodo FITS", name)

    url = "https://zenodo.org/records/13871850/files/ALT_DR1_public.fits?download=1"
    try:
        raw = _download_url(url, timeout=120)
    except Exception as e:
        logger.error("  FAIL  %-25s  %s", name, e)
        return False

    logger.info("         downloaded %.1f KB, parsing FITS...", len(raw) / 1024)

    from astropy.io import fits
    with io.BytesIO(raw) as buf:
        with fits.open(buf) as hdul:
            table_hdu = None
            for i, hdu in enumerate(hdul):
                if hasattr(hdu, 'columns') and hdu.columns is not None and len(hdu.columns) > 0:
                    table_hdu = hdu
                    logger.info("         using HDU %d: %s (%d cols, %d rows)",
                                i, hdu.name, len(hdu.columns), len(hdu.data))
                    break
            if table_hdu is None:
                logger.error("  FAIL  %-25s  no table HDU found", name)
                return False

            data = table_hdu.data
            col_names = [c.name for c in table_hdu.columns]
            logger.info("         columns: %s", col_names)

            # Find columns
            ra_col = dec_col = z_col = None
            for c in col_names:
                cl = c.lower()
                if cl in ('ra', 'raj2000', 'ra_icrs'):
                    ra_col = c
                elif cl in ('dec', 'dej2000', 'de_icrs', 'decl'):
                    dec_col = c
                elif cl in ('z', 'redshift', 'z_spec', 'zspec', 'z_grism'):
                    z_col = c

            # Broader search
            if z_col is None:
                for c in col_names:
                    if 'z' in c.lower() and len(c) <= 10:
                        z_col = c
                        break

            logger.info("         ra=%s, dec=%s, z=%s", ra_col, dec_col, z_col)

            if not all([ra_col, dec_col, z_col]):
                logger.error("  FAIL  %-25s  missing columns. Available: %s", name, col_names)
                return False

            ra_arr = np.array(data[ra_col], dtype=np.float64)
            dec_arr = np.array(data[dec_col], dtype=np.float64)
            z_arr = np.array(data[z_col], dtype=np.float64)

    build_dict = {"ra": ra_arr, "dec": dec_arr, "z_spec": z_arr}
    df = pl.DataFrame(build_dict)

    # ALT grism spectroscopy — R~1600, ~60 km/s precision
    df = df.with_columns(pl.lit("grism").alias("z_spec_quality"))

    df = df.filter(
        pl.col("ra").is_not_null() &
        pl.col("dec").is_not_null() &
        pl.col("z_spec").is_not_null() &
        pl.col("z_spec").is_finite() &
        (pl.col("z_spec") > 0)
    )

    df = df.select([
        pl.col("ra"),
        pl.col("dec"),
        pl.col("z_spec"),
        pl.col("z_spec_quality"),
        pl.lit("ALT-DR1-Grism").alias("survey"),
    ])

    return _save_and_report(df, name, out_path)


# ============================================================================
# 5. GLASS-JWST NIRSpec (Mascia+2024) — from MAST HLSP
# ============================================================================
def download_glass_specz(force: bool = False) -> bool:
    """Download GLASS-JWST NIRSpec spectroscopic catalog from MAST.

    245 unique sources with NIRSpec spectroscopy in Abell 2744.
    Mascia+2024 (A&A 690, A2): 206 robust redshifts from z=0 to z~10.
    """
    import polars as pl

    name = "glass_nirspec_specz"
    out_path = SPECZ_DIR / f"{name}.parquet"
    if out_path.exists() and not force:
        existing = pl.read_parquet(out_path)
        logger.info("  SKIP  %-25s  already exists (%d rows)", name, len(existing))
        return True

    logger.info("  DOWN  %-25s  GLASS-JWST MAST HLSP", name)

    # GLASS HLSP catalog file from MAST
    # The catalog is a FITS file with MSA ID, RA, Dec, redshift, quality flag
    url = "https://archive.stsci.edu/hlsps/glass-jwst/catalogs/hlsp_glass-jwst_jwst_nirspec_abell2744_multi_v002_cat.fits"
    try:
        raw = _download_url(url, timeout=120)
    except Exception as e:
        logger.warning("         primary MAST URL failed: %s", e)
        # Try alternative URL pattern
        alt_urls = [
            "https://archive.stsci.edu/hlsps/glass-jwst/catalogs/hlsp_glass-jwst_jwst_nirspec_abell2744_multi_v001_cat.fits",
            "https://archive.stsci.edu/hlsps/glass-jwst/hlsp_glass-jwst_jwst_nirspec_abell2744_multi_v002_cat.fits",
            "https://archive.stsci.edu/hlsps/glass-jwst/hlsp_glass-jwst_jwst_nirspec_abell2744_multi_v001_cat.fits",
        ]
        raw = None
        for alt in alt_urls:
            try:
                logger.info("         trying %s ...", alt.split("/")[-1])
                raw = _download_url(alt, timeout=120)
                break
            except Exception:
                continue

        if raw is None:
            logger.warning("         MAST HLSP not found, trying VizieR V/159 for JADES NIRSpec as fallback...")
            # GLASS may not have a standalone VizieR table.
            # Fall back to trying a VizieR query for GLASS spec-z if available
            try:
                adql = """SELECT "RAJ2000", "DEJ2000", "zspec"
                          FROM "V/159/gsprism"
                          WHERE "zspec" > 0"""
                csv_text = _tap_query_csv(adql)
                df = _parse_csv_to_df(csv_text)
                if len(df) > 0:
                    rename = {}
                    for c in df.columns:
                        if c.lower() in ('raj2000',): rename[c] = 'ra'
                        elif c.lower() in ('dej2000',): rename[c] = 'dec'
                        elif c.lower() in ('zspec',): rename[c] = 'z_spec'
                    df = df.rename(rename)
                    df = df.with_columns([
                        pl.col("ra").cast(pl.Float64, strict=False),
                        pl.col("dec").cast(pl.Float64, strict=False),
                        pl.col("z_spec").cast(pl.Float64, strict=False),
                    ])
                    df = df.filter(
                        pl.col("z_spec").is_not_null() & pl.col("z_spec").is_finite() & (pl.col("z_spec") > 0)
                    )
                    df = df.with_columns([
                        pl.lit("spec").alias("z_spec_quality"),
                        pl.lit("JADES-NIRSpec-Prism").alias("survey"),
                    ])
                    # Note: this is JADES prism, not GLASS — label accordingly
                    df = df.select(["ra", "dec", "z_spec", "z_spec_quality", "survey"])
                    name = "jades_nirspec_prism_specz"
                    out_path = SPECZ_DIR / f"{name}.parquet"
                    return _save_and_report(df, name, out_path)
            except Exception as e2:
                logger.error("  FAIL  %-25s  all download attempts failed: %s", name, e2)
                return False

    logger.info("         downloaded %.1f KB, parsing FITS...", len(raw) / 1024)

    from astropy.io import fits
    with io.BytesIO(raw) as buf:
        with fits.open(buf) as hdul:
            table_hdu = None
            for i, hdu in enumerate(hdul):
                if hasattr(hdu, 'columns') and hdu.columns is not None and len(hdu.columns) > 0:
                    table_hdu = hdu
                    logger.info("         using HDU %d: %s (%d cols, %d rows)",
                                i, hdu.name, len(hdu.columns), len(hdu.data))
                    break
            if table_hdu is None:
                logger.error("  FAIL  %-25s  no table HDU found", name)
                return False

            data = table_hdu.data
            col_names = [c.name for c in table_hdu.columns]
            logger.info("         columns: %s", col_names)

            # Find columns
            ra_col = dec_col = z_col = qual_col = None
            for c in col_names:
                cl = c.lower()
                if cl in ('ra', 'raj2000', 'ra_icrs'):
                    ra_col = c
                elif cl in ('dec', 'dej2000', 'de_icrs', 'decl'):
                    dec_col = c
                elif 'z' in cl and ('spec' in cl or cl in ('z', 'redshift', 'zfit')):
                    z_col = c
                elif 'flag' in cl or 'qual' in cl or 'accuracy' in cl:
                    qual_col = c

            if z_col is None:
                for c in col_names:
                    if c.lower() in ('z', 'redshift', 'zspec'):
                        z_col = c
                        break

            logger.info("         ra=%s, dec=%s, z=%s, qual=%s", ra_col, dec_col, z_col, qual_col)

            if not all([ra_col, dec_col, z_col]):
                logger.error("  FAIL  %-25s  missing columns. Available: %s", name, col_names)
                return False

            ra_arr = np.array(data[ra_col], dtype=np.float64)
            dec_arr = np.array(data[dec_col], dtype=np.float64)
            z_arr = np.array(data[z_col], dtype=np.float64)

            q_arr = None
            if qual_col:
                try:
                    q_arr = np.array(data[qual_col])
                except Exception:
                    pass

    build_dict = {"ra": ra_arr, "dec": dec_arr, "z_spec": z_arr}
    df = pl.DataFrame(build_dict)

    if q_arr is not None:
        df = df.with_columns(pl.Series("z_spec_quality", q_arr.astype(str)))
    else:
        df = df.with_columns(pl.lit("spec").alias("z_spec_quality"))

    df = df.with_columns([
        pl.col("z_spec").cast(pl.Float64, strict=False),
    ])
    df = df.filter(
        pl.col("ra").is_not_null() &
        pl.col("dec").is_not_null() &
        pl.col("z_spec").is_not_null() &
        pl.col("z_spec").is_finite() &
        (pl.col("z_spec") > 0)
    )

    df = df.select([
        pl.col("ra"),
        pl.col("dec"),
        pl.col("z_spec"),
        pl.col("z_spec_quality"),
        pl.lit("GLASS-JWST-NIRSpec").alias("survey"),
    ])

    return _save_and_report(df, name, out_path)


# ============================================================================
# 6. UNCOVER photometric catalog spec-z column (VizieR J/ApJS/270/7)
# ============================================================================
def download_uncover_vizier_specz(force: bool = False) -> bool:
    """Query VizieR for UNCOVER DR2 catalog sources that have spec-z available.

    The UNCOVER photometric catalog (Price+2024) has a zUse column that contains
    spec-z where available. We query for sources where the zUse type indicates
    spectroscopic redshift.
    """
    import polars as pl

    name = "uncover_vizier_specz"
    out_path = SPECZ_DIR / f"{name}.parquet"
    if out_path.exists() and not force:
        existing = pl.read_parquet(out_path)
        logger.info("  SKIP  %-25s  already exists (%d rows)", name, len(existing))
        return True

    logger.info("  DOWN  %-25s  VizieR J/ApJS/270/7 (UNCOVER DR2)", name)

    # Query for sources with spectroscopic z available.
    # The UNCOVER catalog (J/ApJS/270/7) has:
    #   zUse = adopted redshift, f_zsp = flag for spec-z source,
    #   zsp = spectroscopic z where available, zph = photometric z
    # We want only sources with actual spec-z (zsp > 0).
    adql = """SELECT "RAJ2000", "DEJ2000", "zsp", "f_zsp", "zUse"
              FROM "J/ApJS/270/7/catalog"
              WHERE "zsp" > 0"""

    try:
        csv_text = _tap_query_csv(adql)
    except Exception as e:
        logger.error("  FAIL  %-25s  %s", name, e)
        return False

    df = _parse_csv_to_df(csv_text)
    if len(df) == 0:
        logger.warning("  EMPTY %-25s  no rows returned", name)
        return False

    # Rename columns
    rename = {}
    for c in df.columns:
        cl = c.lower()
        if cl in ('raj2000',): rename[c] = 'ra'
        elif cl in ('dej2000',): rename[c] = 'dec'
        elif cl in ('zsp',): rename[c] = 'z_spec'
        elif cl in ('f_zsp',): rename[c] = 'z_spec_quality'
    df = df.rename(rename)

    df = df.with_columns([
        pl.col("ra").cast(pl.Float64, strict=False),
        pl.col("dec").cast(pl.Float64, strict=False),
        pl.col("z_spec").cast(pl.Float64, strict=False),
    ])
    df = df.filter(
        pl.col("ra").is_not_null() &
        pl.col("dec").is_not_null() &
        pl.col("z_spec").is_not_null() &
        pl.col("z_spec").is_finite() &
        (pl.col("z_spec") > 0)
    )

    if "z_spec_quality" in df.columns:
        df = df.with_columns(
            pl.col("z_spec_quality").cast(pl.Utf8, strict=False)
        )
    else:
        df = df.with_columns(pl.lit("spec").alias("z_spec_quality"))

    # Drop zUse if present (we only need spec-z)
    if "zUse" in df.columns:
        df = df.drop("zUse")

    df = df.select([
        pl.col("ra"),
        pl.col("dec"),
        pl.col("z_spec"),
        pl.col("z_spec_quality"),
        pl.lit("UNCOVER-DR2-VizieR-specz").alias("survey"),
    ])

    return _save_and_report(df, name, out_path)


# ============================================================================
# Main
# ============================================================================

ALL_CATALOGS = {
    "dja": ("DJA NIRSpec v4.4 (all fields)", download_dja),
    "jades": ("JADES DR4 spectroscopy (GOODS-S/N)", download_jades_specz),
    "uncover": ("UNCOVER DR4 spec-z (Abell-2744)", download_uncover_specz),
    "alt": ("ALT DR1 grism spec-z (Abell-2744)", download_alt_specz),
    "glass": ("GLASS-JWST NIRSpec (Abell-2744)", download_glass_specz),
    "uncover_vizier": ("UNCOVER VizieR photoz+specz (Abell-2744)", download_uncover_vizier_specz),
}


def main():
    parser = argparse.ArgumentParser(description="Download public spectroscopic redshift catalogs")
    parser.add_argument("--force", action="store_true",
                        help="Re-download even if parquet already exists")
    parser.add_argument("--only", type=str, default=None,
                        help="Comma-separated list of catalog keys: " + ", ".join(ALL_CATALOGS.keys()))
    args = parser.parse_args()

    _ensure_dir()

    keys = list(ALL_CATALOGS.keys())
    if args.only:
        keys = [k.strip() for k in args.only.split(",")]
        for k in keys:
            if k not in ALL_CATALOGS:
                logger.error("Unknown catalog: %s. Available: %s", k, list(ALL_CATALOGS.keys()))
                sys.exit(1)

    logger.info("=" * 76)
    logger.info("Downloading %d spectroscopic redshift catalogs to %s", len(keys), SPECZ_DIR)
    logger.info("=" * 76)

    ok = 0
    fail = 0

    for key in keys:
        label, func = ALL_CATALOGS[key]
        logger.info("")
        logger.info("--- %s ---", label)

        # Check skip condition inside each function
        try:
            success = func(force=args.force)
        except Exception as e:
            logger.error("  FAIL  %-25s  unhandled: %s", key, e)
            import traceback
            traceback.print_exc()
            success = False

        if success:
            ok += 1
        else:
            fail += 1

        time.sleep(1.0)

    logger.info("")
    logger.info("=" * 76)
    logger.info("Summary: %d succeeded, %d failed", ok, fail)
    logger.info("=" * 76)

    # Print final listing
    logger.info("")
    logger.info("Parquet files in %s:", SPECZ_DIR)
    import polars as pl
    total_sources = 0
    for p in sorted(SPECZ_DIR.glob("*.parquet")):
        df = pl.read_parquet(p)
        total_sources += len(df)
        z = df["z_spec"].drop_nulls().to_numpy()
        z = z[np.isfinite(z)]
        z_range = f"z=[{z.min():.3f}, {z.max():.3f}]" if len(z) > 0 else "no z"
        logger.info("  %-35s  %6d sources  %s  %.1f KB",
                     p.name, len(df), z_range, p.stat().st_size / 1024)
    logger.info("  TOTAL: %d sources across %d files",
                total_sources, len(list(SPECZ_DIR.glob("*.parquet"))))


if __name__ == "__main__":
    main()
