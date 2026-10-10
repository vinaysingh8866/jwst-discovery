"""
D7 — Euclid Q1 + Roman early data overlap regions.

Euclid Q1 release (March 2025) has imaging in:
    VIS (visible 530-900 nm), NISP-Y (1.0 μm), NISP-J (1.25 μm), NISP-H (1.65 μm)

Q1 covers:
    EDF-N (Euclid Deep Field North): ~28 deg² at RA=270, Dec=66 → near PEARLS-NEP!
    EDF-F (Fornax): ~11 deg² at RA=53, Dec=-28 → overlaps GOODS-S/JADES/NGDEEP!
    EDF-S (South): ~22 deg² at RA=61, Dec=-49

Catalogs are at the IRSA Euclid TAP service.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import polars as pl
from astropy import units as u
from astropy.coordinates import SkyCoord

warnings.filterwarnings("ignore")

OUT_DIR = Path(os.path.expanduser("~/.jwst-discovery/cross_archives/euclid"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

FIELDS = {
    "JADES-GOODS-S":  (53.1505, -27.7909, 10.0),  # in EDF-F
    "NGDEEP":         (53.1608, -27.7833, 10.0),
    "PEARLS-NEP":     (260.6837, 65.7989, 10.0),  # in EDF-N
    "PRIMER-UDS":     (34.3500,  -5.2000, 10.0),  # nearby
}


def query_euclid_q1(coord: SkyCoord, radius_arcmin: float):
    """Query Euclid Q1 MER catalog via IRSA."""
    from astroquery.ipac.irsa import Irsa
    Irsa.ROW_LIMIT = -1
    print("    Euclid Q1 MER...", end=" ")
    try:
        # Q1 catalog name
        result = Irsa.query_region(coord, catalog="euclid_q1_mer_catalogue", radius=radius_arcmin * u.arcmin)
        if result is None or len(result) == 0:
            print("0 sources")
            return None
        print(f"{len(result)} sources")
        return result.to_pandas()
    except Exception as e:
        print(f"failed: {e}")
        return None


def query_euclid_morph(coord: SkyCoord, radius_arcmin: float):
    from astroquery.ipac.irsa import Irsa
    Irsa.ROW_LIMIT = -1
    print("    Euclid Q1 MER morph...", end=" ")
    try:
        result = Irsa.query_region(coord, catalog="euclid_q1_mer_morphology", radius=radius_arcmin * u.arcmin)
        if result is None or len(result) == 0:
            print("0 sources")
            return None
        print(f"{len(result)} sources")
        return result.to_pandas()
    except Exception as e:
        print(f"failed: {e}")
        return None


def query_euclid_phz(coord: SkyCoord, radius_arcmin: float):
    """Euclid Q1 photometric redshifts."""
    from astroquery.ipac.irsa import Irsa
    Irsa.ROW_LIMIT = -1
    print("    Euclid Q1 PHZ...", end=" ")
    try:
        result = Irsa.query_region(coord, catalog="euclid_q1_phz_photo_z", radius=radius_arcmin * u.arcmin)
        if result is None or len(result) == 0:
            print("0 sources")
            return None
        print(f"{len(result)} sources")
        return result.to_pandas()
    except Exception as e:
        print(f"failed: {e}")
        return None


def main() -> None:
    for field, (ra, dec, radius) in FIELDS.items():
        print(f"\n=== {field} ({ra:.4f}, {dec:.4f}) ===")
        coord = SkyCoord(ra, dec, unit="deg")
        for name, fn in [
            ("euclid_q1_mer", query_euclid_q1),
            ("euclid_q1_morph", query_euclid_morph),
            ("euclid_q1_phz", query_euclid_phz),
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
                df.to_csv(str(out).replace(".parquet", ".csv"), index=False)
                print(f"    saved as csv ({e})")


if __name__ == "__main__":
    main()
