"""
D4 — Spitzer IRAC + MIPS catalog cross-match for our cluster fields.

Spitzer ended in 2020. SEIP (Spitzer Enhanced Imaging Products) hosts the
post-cryo legacy catalogs. We pull source-detected catalogs (small) for our
field positions via IRSA TAP.

For full imaging mosaics (much larger), we'd query SEIP via IRSA's S3 bucket
or pull through the IRSA finder chart API. Catalogs first.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import polars as pl
from astropy import units as u
from astropy.coordinates import SkyCoord

warnings.filterwarnings("ignore")

OUT_DIR = Path(os.path.expanduser("~/.jwst-discovery/cross_archives/spitzer"))
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


def query_seip_source(coord: SkyCoord, radius_arcmin: float):
    """Query SEIP source list via IRSA TAP."""
    from astroquery.ipac.irsa import Irsa
    Irsa.ROW_LIMIT = -1
    print("    SEIP source list...", end=" ")
    try:
        result = Irsa.query_region(coord, catalog="slphotdr4", radius=radius_arcmin * u.arcmin)
        if result is None or len(result) == 0:
            print("0 sources")
            return None
        print(f"{len(result)} sources")
        return result.to_pandas()
    except Exception as e:
        print(f"failed: {e}")
        return None


def query_swire(coord: SkyCoord, radius_arcmin: float):
    """SWIRE catalog (Spitzer Wide-area InfraRed Extragalactic) for some fields."""
    from astroquery.ipac.irsa import Irsa
    Irsa.ROW_LIMIT = -1
    print("    SWIRE...", end=" ")
    try:
        # SWIRE final source catalog
        result = Irsa.query_region(coord, catalog="swire_lhf_cat_f05", radius=radius_arcmin * u.arcmin)
        if result is None or len(result) == 0:
            print("0 sources")
            return None
        print(f"{len(result)} sources")
        return result.to_pandas()
    except Exception as e:
        print(f"failed: {e}")
        return None


def query_iraccdfs(coord: SkyCoord, radius_arcmin: float):
    """Cross-match against the standard IRAC CDF-S deep catalogs (Ashby+2013)."""
    from astroquery.vizier import Vizier
    Vizier.ROW_LIMIT = -1
    print("    Ashby+2013 IRAC...", end=" ")
    try:
        # SHELA = Spitzer-HETDEX Exploratory Large Area
        result = Vizier(catalog="J/ApJ/769/80/table2", row_limit=-1).query_region(
            coord, radius=radius_arcmin * u.arcmin
        )
        if not result or len(result) == 0:
            print("0 sources")
            return None
        print(f"{len(result[0])} sources")
        return result[0].to_pandas()
    except Exception as e:
        print(f"failed: {e}")
        return None


def main() -> None:
    for field, (ra, dec, radius) in FIELDS.items():
        print(f"\n=== {field} ===")
        coord = SkyCoord(ra, dec, unit="deg")

        for name, fn in [
            ("seip", query_seip_source),
            ("swire", query_swire),
            ("ashby_irac", query_iraccdfs),
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
