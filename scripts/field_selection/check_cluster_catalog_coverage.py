#!/usr/bin/env python3
"""
Deep catalog check for JWST-observed galaxy clusters.
Checks each cluster against several catalog sources:
  - Broader VizieR search (single-term, no JWST filter)
  - MAST HLSP catalog-type detection
  - Known catalog lookup (UNCOVER, GLASS, PEARLS, CANUCS, etc.)
"""

import time
import re
import requests
from collections import defaultdict

# ---- Known JWST photometric catalogs by cluster ----
# Catalogs known from the literature
KNOWN_CATALOGS = {
    "A2744": [
        "UNCOVER (Bezanson+2022, NIRCam 7-band photometry, ~61k sources)",
        "GLASS-JWST (Treu+2022, NIRCam photometry)",
        "ASTRODEEP-JWST (Merlin+2022, NIRCam+HST)",
        "Paris-NIRCam (Mahler+2023)",
    ],
    "SMACS0723": [
        "grizli (Brammer, photometric catalog)",
        "Amrutha+2023 (photo-z)",
    ],
    "MACS0416": [
        "PEARLS (Windhorst+2023, but partial coverage)",
        "Paris-Frontier (Mahler+2023, lensing model, not full photometric catalog?)",
    ],
    "A370": [],
    "MACS1149": [],
    "AS1063": [],
    "MACS0717": [],
    "ElGordo": [
        "Diego+2023 (strong lensing model, some photometry)",
    ],
}


# ---- Cluster list ----
CLUSTERS = [
    {"name": "Abell 370",                    "short": "A370",      "ra": 39.9690,  "dec": -1.5767,  "z": 0.375, "n_filt": 25, "n_l3": 25, "exp_hrs": 216.3,
     "proposals": "1208,12581,12583,2514,2883,3362,3538,5324,5890,6882,7345,9371",
     "note": "Original HFF cluster, 25 NIRCam filters"},
    {"name": "MACS-J0416.1-2403",            "short": "MACS0416", "ra": 64.0381,  "dec": -24.0658, "z": 0.396, "n_filt": 23, "n_l3": 23, "exp_hrs": 357.1,
     "proposals": "1176,1208,12581,2514,2883,3362,3538,5890,6434,6626,6882,9969",
     "note": "HFF cluster, Mothra transient, 357 hrs exposure"},
    {"name": "MACS-J1149.5+2223",            "short": "MACS1149", "ra": 177.3987, "dec": 22.3985,  "z": 0.544, "n_filt": 21, "n_l3": 21, "exp_hrs": 197.4,
     "proposals": "1176,1199,1208,12581,2883,3362,4552,5890,6882",
     "note": "Lensed SN Refsdal, HFF cluster"},
    {"name": "Abell S1063 / RXC J2248.7-4431","short": "AS1063",  "ra": 342.1832, "dec": -44.5309, "z": 0.348, "n_filt": 15, "n_l3": 15, "exp_hrs": 263.6,
     "proposals": "10273,12581,1840,3293,5398,6882",
     "note": "HFF/GLIMPSE cluster, 264 hrs exposure"},
    {"name": "Abell 1689",                   "short": "A1689",     "ra": 197.8729, "dec": -1.3408,  "z": 0.183, "n_filt": 11, "n_l3": 8,  "exp_hrs": 25.7,
     "proposals": "12581,1840,5782,6882",
     "note": "Famous lensing cluster"},
    {"name": "MACS-J0717.5+3745",            "short": "MACS0717", "ra": 109.3833, "dec": 37.7564,  "z": 0.548, "n_filt": 10, "n_l3": 10, "exp_hrs": 13.8,
     "proposals": "12581,6882",
     "note": "HFF cluster, massive merger"},
    {"name": "MS0451-03",                    "short": "MS0451",    "ra": 73.5453,  "dec": -3.0145,  "z": 0.539, "n_filt": 10, "n_l3": 10, "exp_hrs": 33.2,
     "proposals": "12581,5058,6882",
     "note": "CANUCS target"},
    {"name": "El Gordo / ACT-CL J0102-4915", "short": "ElGordo",  "ra": 15.7220,  "dec": -49.2553, "z": 0.870, "n_filt": 10, "n_l3": 8,  "exp_hrs": 6.7,
     "proposals": "1176,12581,6882",
     "note": "Most massive distant cluster"},
    {"name": "RXJ2129+0005",                 "short": "RXJ2129",  "ra": 322.4165, "dec": 0.0892,   "z": 0.235, "n_filt": 10, "n_l3": 10, "exp_hrs": 9.2,
     "proposals": "12581,2767,6882",
     "note": "JWST DD-2767 program"},
    {"name": "SPT-CL J0615-5746",            "short": "SPT0615",  "ra": 93.9654,  "dec": -57.7800, "z": 0.972, "n_filt": 10, "n_l3": 10, "exp_hrs": 9.1,
     "proposals": "12581,4212,6882",
     "note": "RELICS, highest-z RELICS cluster"},
    {"name": "WHL0137-08 (Sunrise Arc)",     "short": "WHL0137",  "ra": 24.3539,  "dec": -8.4597,  "z": 0.566, "n_filt": 10, "n_l3": 10, "exp_hrs": 9.6,
     "proposals": "12581,2282,6882",
     "note": "Earendel, most distant star"},
    {"name": "MACS-J0647.7+7015",           "short": "MACS0647",  "ra": 101.9584, "dec": 70.2483,  "z": 0.591, "n_filt": 7,  "n_l3": 7,  "exp_hrs": 4.7,
     "proposals": "1433",
     "note": "CANUCS target, triply-lensed galaxy"},
]


def vizier_search_broad(search_term):
    """Broad VizieR TAP search for any table mentioning this term."""
    tap_url = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync"
    query = f"SELECT table_name, description FROM TAP_SCHEMA.tables WHERE LOWER(description) LIKE '%{search_term.lower()}%'"
    try:
        r = requests.get(tap_url, params={
            "REQUEST": "doQuery", "LANG": "ADQL",
            "FORMAT": "json", "QUERY": query.strip(),
        }, timeout=45)
        if r.status_code == 200:
            data = r.json()
            if "data" in data:
                return [{"table": row[0], "desc": row[1]} for row in data["data"]]
    except Exception:
        pass
    return []


def check_mast_hlsp_catalogs(ra, dec, radius_deg=0.15):
    """Check MAST for HLSP with catalog dataproduct_type."""
    try:
        from astroquery.mast import Observations
        import astropy.units as u
        from astropy.coordinates import SkyCoord

        coord = SkyCoord(ra=ra, dec=dec, unit="deg")
        obs_table = Observations.query_criteria(
            coordinates=coord,
            radius=radius_deg * u.deg,
            obs_collection="HLSP",
        )
        if obs_table is None or len(obs_table) == 0:
            return {}

        hlsp = defaultdict(lambda: {"types": set(), "count": 0, "obs_ids": set()})
        for row in obs_table:
            proj = str(row.get("project", ""))
            dtype = str(row.get("dataproduct_type", ""))
            obs_id = str(row.get("obs_id", ""))
            if proj:
                hlsp[proj]["types"].add(dtype)
                hlsp[proj]["count"] += 1
                hlsp[proj]["obs_ids"].add(obs_id)

        return {k: {"types": sorted(v["types"]),
                     "count": v["count"],
                     "has_catalog": "catalog" in " ".join(v["types"]).lower(),
                     "sample_ids": sorted(v["obs_ids"])[:3]}
                for k, v in hlsp.items()}

    except Exception as e:
        print(f"    HLSP error: {e}")
        return {}


def main():
    print("=" * 90)
    print("DEEP CATALOG CHECK FOR JWST GALAXY CLUSTERS")
    print("=" * 90)

    # -----------------------------------------------------------------------
    # Step 1: VizieR broad search per cluster
    # -----------------------------------------------------------------------
    print("\n" + "=" * 90)
    print("STEP 1: VizieR broad search for each cluster")
    print("=" * 90)

    for cl in CLUSTERS:
        short = cl["short"]
        name = cl["name"]
        print(f"\n  --- {name} ({short}) ---")

        # Generate search terms
        terms = set()
        if "MACS" in name:
            m = re.search(r'MACS[- ]?J?(\d{4}[\.\d]*[+-]\d+)', name)
            if m:
                terms.add(f"macsj{m.group(1).replace('.', '').replace('+', '%2B').replace('-', '')[:8]}")
            m2 = re.search(r'MACS[- ]?J?(\d{4})', name)
            if m2:
                terms.add(f"macs j{m2.group(1)}")
                terms.add(f"macsj{m2.group(1)}")
        if "Abell" in name:
            m = re.search(r'Abell\s*(\S?\d+)', name)
            if m:
                terms.add(f"abell {m.group(1).lower()}")
        if short.lower() not in [t.lower().replace(" ", "") for t in terms]:
            terms.add(short.lower())
        if "Gordo" in name:
            terms.add("el gordo")
        if "SPT" in name:
            terms.add("spt-cl j0615")
            terms.add("spt0615")
        if "WHL" in name:
            terms.add("whl0137")
        if "RXJ" in short:
            terms.add("rxj2129")
        if "RXC" in name:
            terms.add("rxc j2248")

        all_viz = []
        for term in sorted(terms):
            print(f"    Searching VizieR for '{term}'...")
            results = vizier_search_broad(term)
            if results:
                # Filter for relevant ones (photometry, catalog, JWST, redshift)
                relevant = []
                for r in results:
                    desc_low = (r["desc"] or "").lower()
                    if any(kw in desc_low for kw in ["photom", "catalog", "redshift", "photo-z", "jwst", "nircam", "niriss", "webb", "source"]):
                        relevant.append(r)
                if relevant:
                    print(f"      Found {len(relevant)} relevant catalogs (of {len(results)} total)")
                    for r in relevant[:5]:
                        jwst_marker = " [JWST]" if any(kw in (r["desc"] or "").lower() for kw in ["jwst", "nircam", "niriss", "webb"]) else ""
                        print(f"        {r['table']}: {(r['desc'] or '')[:100]}{jwst_marker}")
                    all_viz.extend(relevant)
                else:
                    print(f"      {len(results)} tables found, none relevant to photometry/catalogs")
            else:
                print("      No results")
            time.sleep(0.5)

        # Deduplicate
        seen = set()
        unique_viz = []
        for v in all_viz:
            if v["table"] not in seen:
                seen.add(v["table"])
                unique_viz.append(v)

        cl["vizier_all"] = unique_viz
        cl["vizier_jwst"] = [v for v in unique_viz if any(kw in (v["desc"] or "").lower() for kw in ["jwst", "nircam", "niriss", "webb"])]

    # -----------------------------------------------------------------------
    # Step 2: MAST HLSP catalog check
    # -----------------------------------------------------------------------
    print("\n" + "=" * 90)
    print("STEP 2: MAST HLSP catalog check")
    print("=" * 90)

    for cl in CLUSTERS:
        print(f"\n  --- {cl['name']} ---")
        hlsp = check_mast_hlsp_catalogs(cl["ra"], cl["dec"])
        cl["hlsp"] = hlsp

        # Separate HLSP projects with catalogs vs imaging only
        catalog_hlsp = {k: v for k, v in hlsp.items() if v["has_catalog"]}
        imaging_hlsp = {k: v for k, v in hlsp.items() if not v["has_catalog"]}

        if catalog_hlsp:
            print("    HLSP with CATALOGS:")
            for proj, info in sorted(catalog_hlsp.items()):
                print(f"      {proj}: {info['count']} products, types={info['types']}")
        if imaging_hlsp:
            print(f"    HLSP imaging only: {', '.join(sorted(imaging_hlsp.keys()))}")
        if not hlsp:
            print("    No HLSP products")

        time.sleep(0.5)

    # -----------------------------------------------------------------------
    # Step 3: Check for specific known JWST survey coverage
    # -----------------------------------------------------------------------
    print("\n" + "=" * 90)
    print("STEP 3: Known JWST survey program coverage")
    print("=" * 90)

    # Key JWST survey programs and their target fields
    JWST_SURVEYS = {
        "UNCOVER (GO-2561)": {"clusters": ["Abell 2744"], "type": "deep photometric catalog"},
        "GLASS-JWST (ERS-1324)": {"clusters": ["Abell 2744"], "type": "photometric catalog"},
        "CANUCS (GO-1208)": {"clusters": ["MACS-J0416.1-2403", "Abell 370", "MACS-J1149.5+2223", "MACS-J0647.7+7015", "MS0451-03"], "type": "photometric catalog (private?)"},
        "PEARLS (GTO-1176)": {"clusters": ["MACS-J0416.1-2403", "El Gordo", "MACS-J1149.5+2223"], "type": "imaging + some photometry"},
        "COSMOS-Web (GO-1727)": {"clusters": [], "type": "field survey, not cluster"},
        "MegaScience (GO-6882)": {"clusters": ["many RELICS clusters"], "type": "imaging, GO Cycle 3"},
        "GLIMPSE (GO-1840)": {"clusters": ["Abell S1063", "Abell 1689"], "type": "NIRISS grism + imaging"},
        "DD-2767": {"clusters": ["RXJ2129+0005"], "type": "DD program, SN follow-up?"},
    }

    for cl in CLUSTERS:
        surveys = []
        for survey, info in JWST_SURVEYS.items():
            # Check if cluster name or short name matches
            for target in info["clusters"]:
                if (cl["short"].lower() in target.lower().replace("-", "").replace(" ", "")
                    or target.lower() in cl["name"].lower()):
                    surveys.append(f"{survey}: {info['type']}")
                    break
            # Check proposals
            survey_pid = re.search(r'\d{4,5}', survey)
            if survey_pid and survey_pid.group() in cl["proposals"]:
                s = f"{survey}: {info['type']}"
                if s not in surveys:
                    surveys.append(s)

        cl["jwst_surveys"] = surveys

        if surveys:
            print(f"  {cl['short']}: {'; '.join(surveys)}")
        else:
            print(f"  {cl['short']}: No known major JWST survey")

    # -----------------------------------------------------------------------
    # Final Analysis
    # -----------------------------------------------------------------------
    print("\n" + "=" * 90)
    print("FINAL ANALYSIS: CATALOG GAPS")
    print("=" * 90)

    print(f"\n{'#':<4} {'Cluster':<40} {'z':>5} {'Filt':>5} {'Exp':>7} {'VizJWST':>8} {'VizAll':>7} {'HLSP-cat':>9} {'Surveys':<30}")
    print("-" * 125)

    for i, cl in enumerate(CLUSTERS, 1):
        n_viz_jwst = len(cl["vizier_jwst"])
        n_viz_all = len(cl["vizier_all"])
        hlsp_cats = [k for k, v in cl["hlsp"].items() if v["has_catalog"]]
        n_hlsp_cat = len(hlsp_cats)
        surveys_short = "; ".join(s.split(":")[0] for s in cl["jwst_surveys"][:3]) if cl["jwst_surveys"] else "-"

        print(f"{i:<4} {cl['name']:<40} {cl['z']:>5.3f} {cl['n_filt']:>5} {cl['exp_hrs']:>7.1f} "
              f"{n_viz_jwst:>8} {n_viz_all:>7} {n_hlsp_cat:>9} {surveys_short:<30}")

    # Determine gaps
    print("\n" + "=" * 90)
    print("TIER 1: PRIME TARGETS (many filters, deep data, NO comprehensive JWST catalog)")
    print("=" * 90)

    tier1 = []
    tier2 = []
    tier3 = []

    for cl in CLUSTERS:
        n_viz_jwst = len(cl["vizier_jwst"])
        hlsp_cats = [k for k, v in cl["hlsp"].items() if v["has_catalog"]]
        has_canucs = any("CANUCS" in s for s in cl["jwst_surveys"])
        has_uncover = any("UNCOVER" in s or "GLASS" in s for s in cl["jwst_surveys"])

        # Exclude clusters with known catalogs
        if has_uncover or cl["short"] in ["A2744", "SMACS0723"]:
            cl["status"] = "ALREADY HAS CATALOG (control)"
            continue

        if has_canucs:
            # CANUCS has photometry but may not be fully public
            cl["status"] = "CANUCS COVERAGE (check if public catalog exists)"
            tier2.append(cl)
        elif n_viz_jwst > 0 or len(hlsp_cats) > 0:
            cl["status"] = "PARTIAL COVERAGE"
            tier2.append(cl)
        else:
            cl["status"] = "NO JWST PHOTOMETRIC CATALOG"
            if cl["n_filt"] >= 10:
                tier1.append(cl)
            else:
                tier3.append(cl)

    for cl in tier1:
        print(f"\n  *** {cl['name']} ***")
        print(f"      z = {cl['z']}")
        print(f"      NIRCam filters: {cl['n_filt']} (Level-3: {cl['n_l3']})")
        print(f"      Total exposure: {cl['exp_hrs']:.1f} hrs")
        print(f"      Proposals: {cl['proposals']}")
        print(f"      VizieR catalogs (any): {len(cl['vizier_all'])}")
        if cl["vizier_all"]:
            for v in cl["vizier_all"][:3]:
                print(f"        {v['table']}: {(v['desc'] or '')[:80]}")
        print(f"      HLSP projects: {list(cl['hlsp'].keys()) if cl['hlsp'] else 'None'}")
        print(f"      Known surveys: {cl['jwst_surveys'] or 'None'}")
        print(f"      Note: {cl['note']}")

    if tier2:
        print("\n" + "=" * 90)
        print("TIER 2: PARTIAL COVERAGE (may have CANUCS or partial catalog)")
        print("=" * 90)
        for cl in tier2:
            print(f"\n  {cl['name']} ({cl['n_filt']} filters, z={cl['z']})")
            print(f"      Status: {cl['status']}")
            print(f"      Known surveys: {cl['jwst_surveys']}")

    if tier3:
        print("\n" + "=" * 90)
        print("TIER 3: SECONDARY TARGETS (fewer filters)")
        print("=" * 90)
        for cl in tier3:
            print(f"  {cl['name']} ({cl['n_filt']} filters, z={cl['z']}): {cl['status']}")


if __name__ == "__main__":
    main()
