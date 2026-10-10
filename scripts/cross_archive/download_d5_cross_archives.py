"""
D5 — Gaia DR3 + Chandra CSC + radio cross-archive catalogs.

For each cluster/deep field we have, query:
- Gaia DR3 (proper motion, parallax) — foreground star id + brown dwarfs
- Chandra Source Catalog (CSC) — X-ray AGN detections
- FIRST radio survey — radio-loud AGN
- 2MASS PSC — bright NIR sources
- AllWISE — mid-IR all-sky catalog

Outputs:
    ~/.jwst-discovery/cross_archives/{field}_{archive}.parquet
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import polars as pl
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.table import Table

warnings.filterwarnings("ignore")

OUT_DIR = Path(os.path.expanduser("~/.jwst-discovery/cross_archives"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

FIELDS = {
    "Abell-2744":     (3.5772, -30.3762, 5.0),
    "Abell-370":      (39.9716, -1.5773, 5.0),
    "MACS-J0416":     (64.0381, -24.0728, 5.0),
    "MACS-J1149":     (177.3987, 22.3979, 5.0),
    "Abell-S1063":    (342.1839, -44.5326, 5.0),
    "JADES-GOODS-S":  (53.1505, -27.7909, 10.0),
    "NGDEEP":         (53.1608, -27.7833, 10.0),
    "PRIMER-UDS":     (34.3500,  -5.2000, 10.0),
    "CEERS-EGS":      (215.1620, 53.0513, 10.0),
    "Bullet-Cluster": (104.6143, -55.9482, 5.0),
    "PEARLS-NEP":     (260.6837, 65.7989, 10.0),
}


def query_gaia(coord: SkyCoord, radius_arcmin: float):
    from astroquery.gaia import Gaia
    Gaia.ROW_LIMIT = 50000
    print("    Gaia DR3...", end=" ")
    try:
        # Use cone search via TAP
        adql = f"""
        SELECT source_id, ra, dec, parallax, parallax_error,
               pmra, pmdec, pmra_error, pmdec_error,
               phot_g_mean_mag, phot_bp_mean_mag, phot_rp_mean_mag,
               ruwe
        FROM gaiadr3.gaia_source
        WHERE 1=CONTAINS(POINT('ICRS', ra, dec),
                         CIRCLE('ICRS', {coord.ra.deg}, {coord.dec.deg}, {radius_arcmin/60.0}))
        """
        job = Gaia.launch_job_async(adql)
        result = job.get_results()
        print(f"{len(result)} stars")
        return Table(result).to_pandas() if hasattr(result, "to_pandas") else None
    except Exception as e:
        print(f"failed: {e}")
        return None


def query_chandra(coord: SkyCoord, radius_arcmin: float):
    """Chandra Source Catalog 2.1 via VizieR."""
    from astroquery.vizier import Vizier
    Vizier.ROW_LIMIT = -1
    print("    Chandra CSC...", end=" ")
    try:
        # CSC 2.1 master source table on VizieR
        result = Vizier(catalog="IX/57/csc2master", row_limit=-1).query_region(
            coord, radius=radius_arcmin * u.arcmin
        )
        if not result or len(result) == 0:
            print("0 sources")
            return None
        tbl = result[0]
        print(f"{len(tbl)} sources")
        return tbl.to_pandas() if hasattr(tbl, "to_pandas") else None
    except Exception as e:
        print(f"failed: {e}")
        return None


def query_first_radio(coord: SkyCoord, radius_arcmin: float):
    """FIRST radio survey (1.4 GHz) via VizieR."""
    from astroquery.vizier import Vizier
    Vizier.ROW_LIMIT = -1
    print("    FIRST radio...", end=" ")
    try:
        result = Vizier(catalog="VIII/92/first14", row_limit=-1).query_region(
            coord, radius=radius_arcmin * u.arcmin
        )
        if not result or len(result) == 0:
            print("0 sources")
            return None
        tbl = result[0]
        print(f"{len(tbl)} sources")
        return tbl.to_pandas() if hasattr(tbl, "to_pandas") else None
    except Exception as e:
        print(f"failed: {e}")
        return None


def query_allwise(coord: SkyCoord, radius_arcmin: float):
    """AllWISE all-sky catalog via VizieR."""
    from astroquery.vizier import Vizier
    Vizier.ROW_LIMIT = 50000
    print("    AllWISE...", end=" ")
    try:
        result = Vizier(catalog="II/328/allwise", row_limit=50000).query_region(
            coord, radius=radius_arcmin * u.arcmin
        )
        if not result or len(result) == 0:
            print("0 sources")
            return None
        tbl = result[0]
        print(f"{len(tbl)} sources")
        return tbl.to_pandas() if hasattr(tbl, "to_pandas") else None
    except Exception as e:
        print(f"failed: {e}")
        return None


def query_2mass(coord: SkyCoord, radius_arcmin: float):
    from astroquery.vizier import Vizier
    Vizier.ROW_LIMIT = 50000
    print("    2MASS PSC...", end=" ")
    try:
        result = Vizier(catalog="II/246/out", row_limit=50000).query_region(
            coord, radius=radius_arcmin * u.arcmin
        )
        if not result or len(result) == 0:
            print("0 sources")
            return None
        tbl = result[0]
        print(f"{len(tbl)} sources")
        return tbl.to_pandas() if hasattr(tbl, "to_pandas") else None
    except Exception as e:
        print(f"failed: {e}")
        return None


def main() -> None:
    for field, (ra, dec, radius) in FIELDS.items():
        print(f"\n=== {field} ({ra:.4f}, {dec:.4f}) r={radius}' ===")
        coord = SkyCoord(ra, dec, unit="deg")

        for name, fn in [
            ("gaia_dr3", query_gaia),
            ("chandra_csc", query_chandra),
            ("first_radio", query_first_radio),
            ("allwise", query_allwise),
            ("2mass", query_2mass),
        ]:
            out = OUT_DIR / f"{field}_{name}.parquet"
            if out.exists():
                print(f"    {name}: cached")
                continue
            df = fn(coord, radius)
            if df is None or len(df) == 0:
                continue
            try:
                pl.from_pandas(df).write_parquet(out)
            except Exception as e:
                print(f"    {name}: parquet save failed ({e}); writing CSV")
                df.to_csv(str(out).replace(".parquet", ".csv"), index=False)


if __name__ == "__main__":
    main()
