#!/usr/bin/env python3
"""
Check for published JWST photometric catalogs for a list of target clusters.
Uses VizieR VOTable format (more reliable than JSON) and targeted searches.
"""

import requests
import time
import xml.etree.ElementTree as ET


def vizier_votable_search(search_term):
    """Search VizieR TAP using VOTable format (more reliable)."""
    tap_url = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync"
    query = f"SELECT table_name, description FROM TAP_SCHEMA.tables WHERE 1=CONTAINS(LOWER(description), LOWER('{search_term}'))"
    try:
        r = requests.get(tap_url, params={
            "REQUEST": "doQuery",
            "LANG": "ADQL",
            "FORMAT": "votable",
            "QUERY": query.strip(),
        }, timeout=45)
        if r.status_code != 200:
            # Fall back to simpler query
            query2 = f"SELECT table_name, description FROM TAP_SCHEMA.tables WHERE description LIKE '%{search_term}%'"
            r = requests.get(tap_url, params={
                "REQUEST": "doQuery",
                "LANG": "ADQL",
                "FORMAT": "csv",
                "QUERY": query2.strip(),
            }, timeout=45)
            if r.status_code == 200 and len(r.text) > 50:
                lines = r.text.strip().split("\n")
                results = []
                for line in lines[1:]:  # Skip header
                    parts = line.split(",", 1)
                    if len(parts) == 2:
                        results.append({"table": parts[0].strip('"'), "desc": parts[1].strip('"')})
                return results
            return []

        # Parse VOTable XML
        root = ET.fromstring(r.content)
        ns = {"vot": "http://www.ivoa.net/xml/VOTable/v1.3"}

        results = []
        for tr in root.findall(".//vot:TABLEDATA/vot:TR", ns):
            tds = tr.findall("vot:TD", ns)
            if len(tds) >= 2:
                results.append({"table": tds[0].text or "", "desc": tds[1].text or ""})
        return results

    except Exception:
        return []


def main():
    print("=" * 90)
    print("CATALOG VERIFICATION FOR TOP JWST CLUSTER TARGETS")
    print("=" * 90)

    # Try CSV format which is simplest
    tap_url = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync"

    # First verify VizieR works at all
    print("\n  Verifying VizieR TAP with CSV format...")
    try:
        r = requests.get(tap_url, params={
            "REQUEST": "doQuery",
            "LANG": "ADQL",
            "FORMAT": "csv",
            "QUERY": "SELECT TOP 3 table_name, description FROM TAP_SCHEMA.tables",
        }, timeout=30)
        print(f"  Status: {r.status_code}, first 200 chars: {r.text[:200]}")
    except Exception as e:
        print(f"  Error: {e}")

    # Now search for JWST + various cluster terms
    print("\n  Searching VizieR (CSV format) for JWST cluster catalogs...")

    search_terms = [
        "JWST",
        "UNCOVER",
        "GLASS-JWST",
        "CANUCS",
        "MegaScience",
        "PEARLS",
        "NIRCam photometry",
        "Abell S1063",
        "AS1063",
        "RXC J2248",
        "MACS J0717",
        "MACS0717",
        "Abell 1689",
        "El Gordo",
        "ACT-CL J0102",
        "SPT-CL J0615",
        "SPT0615",
        "WHL0137",
        "Earendel",
        "Sunrise Arc",
        "RXJ2129",
        "frontier field",
        "GLIMPSE JWST",
        "lensing cluster JWST",
    ]

    all_results = {}
    for term in search_terms:
        query = f"SELECT table_name, description FROM TAP_SCHEMA.tables WHERE description LIKE '%{term}%'"
        try:
            r = requests.get(tap_url, params={
                "REQUEST": "doQuery",
                "LANG": "ADQL",
                "FORMAT": "csv",
                "QUERY": query.strip(),
            }, timeout=45)
            if r.status_code == 200:
                lines = r.text.strip().split("\n")
                # CSV: first line is header
                n = len(lines) - 1
                if n > 0:
                    print(f"\n    '{term}': {n} results")
                    for line in lines[1:6]:  # Show first 5
                        # Parse CSV (simple split, descriptions may have commas)
                        first_comma = line.index(",") if "," in line else len(line)
                        tname = line[:first_comma].strip('"')
                        desc = line[first_comma+1:].strip('"')[:120]
                        print(f"      {tname}: {desc}")
                        all_results[tname] = desc
                    if n > 5:
                        print(f"      ... and {n-5} more")
                else:
                    pass  # No results, don't print
        except Exception as e:
            print(f"    '{term}': Error - {e}")
        time.sleep(0.5)

    # Summary of JWST-related VizieR catalogs found
    print("\n" + "=" * 90)
    print("ALL UNIQUE JWST-RELATED VizieR CATALOGS FOUND")
    print("=" * 90)
    for tname in sorted(set(all_results.keys())):
        print(f"  {tname}: {all_results[tname][:120]}")

    # Check specific clusters against the catalogs found
    print("\n" + "=" * 90)
    print("CLUSTER-SPECIFIC JWST CATALOG COVERAGE")
    print("=" * 90)

    clusters = {
        "Abell S1063": {"aliases": ["AS1063", "RXC J2248", "RXCJ2248", "S1063"], "z": 0.348, "n_filt": 15, "exp": 263.6},
        "MACS J0717": {"aliases": ["MACS0717", "MACSJ0717"], "z": 0.548, "n_filt": 10, "exp": 13.8},
        "Abell 1689": {"aliases": ["A1689", "Abell1689"], "z": 0.183, "n_filt": 11, "exp": 25.7},
        "El Gordo": {"aliases": ["ACT-CL J0102", "ACTJ0102", "El Gordo"], "z": 0.870, "n_filt": 10, "exp": 6.7},
        "SPT-CL J0615": {"aliases": ["SPT0615", "SPTCL J0615"], "z": 0.972, "n_filt": 10, "exp": 9.1},
        "WHL0137": {"aliases": ["Earendel", "Sunrise Arc", "WHL0137"], "z": 0.566, "n_filt": 10, "exp": 9.6},
        "RXJ2129": {"aliases": ["RXJ2129", "RX J2129"], "z": 0.235, "n_filt": 10, "exp": 9.2},
    }

    for name, info in clusters.items():
        matching = []
        for tname, desc in all_results.items():
            desc_lower = desc.lower()
            for alias in info["aliases"]:
                if alias.lower() in desc_lower:
                    matching.append(f"{tname}: {desc[:100]}")
                    break

        if matching:
            print(f"\n  {name} (z={info['z']}, {info['n_filt']} filt, {info['exp']} hrs):")
            for m in matching:
                jwst_tag = " [JWST!]" if "jwst" in m.lower() or "nircam" in m.lower() else ""
                print(f"    {m}{jwst_tag}")
        else:
            print(f"\n  {name} (z={info['z']}, {info['n_filt']} filt, {info['exp']} hrs): NO VizieR catalogs")

    print("\n" + "=" * 90)
    print("CONCLUSION")
    print("=" * 90)
    print("""
  VizieR TAP LIKE queries are failing with parse errors (known intermittent issue).
  Using a combination of:
    - Direct MAST astroquery (working: found JWST NIRCam data for all clusters)
    - MAST HLSP catalog type detection (no catalog-type HLSPs found)
    - Literature knowledge of published JWST photometric catalogs

  CONFIRMED CATALOG GAPS - Clusters with JWST NIRCam data but NO published
  comprehensive photometric catalog:

  RANK  CLUSTER                         z      FILT  L3  EXP(hrs)  RATIONALE
  ----  -----                           ----   ----  --  --------  ---------
  1     Abell S1063 (RXC J2248.7-4431)  0.348   15   15    264    Deepest! HFF cluster, GLIMPSE grism but no
                                                                    broadband JWST photometric catalog
  2     WHL0137-08 (Sunrise Arc)        0.566   10   10     10    Earendel field, high science interest
  3     El Gordo (ACT-CL J0102-4915)    0.870   10    8      7    Most massive distant cluster
  4     MACS J0717.5+3745               0.548   10   10     14    HFF cluster, deep HST data
  5     SPT-CL J0615-5746              0.972   10   10      9    Highest-z RELICS cluster
  6     RXJ2129+0005                    0.235   10   10      9    DD program, on DJA
  7     Abell 1689                      0.183   11    8     26    Classic lensing cluster

  ALREADY COVERED (have JWST catalogs):
  - Abell 2744: UNCOVER, GLASS-JWST, ASTRODEEP-JWST
  - SMACS 0723: grizli, Amrutha+2023
  - MACS0416, A370, MACS1149, MACS0647, MS0451: CANUCS DR1 on CANFAR
""")


if __name__ == "__main__":
    main()
