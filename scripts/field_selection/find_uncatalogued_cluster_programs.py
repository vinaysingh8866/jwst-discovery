#!/usr/bin/env python3
"""
Find JWST NIRCam imaging programs targeting GALAXY CLUSTERS (not stellar clusters)
that have no published photometric catalog found by this check.

Filters out stellar clusters, galactic plane surveys, calibration
targets, and nearby galaxies. Focuses on massive galaxy clusters useful for
lensing/high-z science.
"""

import warnings
warnings.filterwarnings("ignore")

from astroquery.mast import Observations
import numpy as np
from collections import defaultdict
import re

# ── Programs already in the field registry (fields.py) ──
EXCLUDE_PROGRAMS = {
    "1176",   # PEARLS (El Gordo, PLCK G165)
    "2561",   # UNCOVER (Abell 2744)
    "1210",   # JADES
    "1180",   # JADES
    "1208",   # CANUCS
    "1433",   # CANUCS
    "2736",   # ERO (SMACS 0723)
    "4598",   # Bullet Cluster
    "4212",   # Cosmic Gems
}

# ── Programs with well-known published catalogs ──
WELL_CATALOGED_PROGRAMS = {
    "4111",   # ALT / MegaScience - Abell 2744 (Suess et al. medium bands, already has UNCOVER)
    "1324",   # GLASS - Abell 2744 (Treu, extensive publications)
    "2756",   # Abell 2744 follow-up (Chen)
    "2883",   # Abell 370 + Abell 2744 (Sun) - HFF legacy fields
}

# ── Galaxy cluster name patterns (actual extragalactic clusters) ──
GALAXY_CLUSTER_PATTERNS = [
    r"\bAbell\s?\d+\b", r"\bA\d{3,4}\b", r"\bACO[-\s]?\d+\b",
    r"\bMACS\s?J?\d{4}", r"\bRXC?\s?J?\d{4}", r"\bSPT[-\s]?J?\d{4}",
    r"\bACT[-\s]?CL", r"\bPLCK\s?G?\d{3}", r"\bPSZ\d?\s?G?\d{3}",
    r"\bSMACS\s?J?\d{4}", r"\bCL\s?J?\d{4}", r"\bZwCl\s?\d{4}",
    r"\b1E\s?\d{4}", r"\bMS\s?\d{4}", r"\bRCS\s?\d{4}",
    r"\bWHL\s?J?\d+", r"\bXLSSC\s?\d+",
    r"\bEl\s?Gordo\b", r"\bBullet\s?Cluster\b",
]
GALAXY_CLUSTER_RE = re.compile("|".join(GALAXY_CLUSTER_PATTERNS), re.IGNORECASE)

# ── Title keywords that suggest galaxy cluster / lensing science ──
LENSING_TITLE_KEYWORDS = [
    r"galaxy\s+cluster", r"cluster\s+of\s+galaxies",
    r"strong\s+lens", r"gravitational\s+lens", r"lensing\s+cluster",
    r"cluster\s+lens", r"behind\s+cluster", r"lensed\s+galaxy",
    r"cluster\s+field", r"BCG", r"brightest\s+cluster\s+galaxy",
    r"intracluster\s+light", r"\bICL\b",
    r"Frontier\s+Field", r"\bHFF\b", r"RELICS",
    r"high.?redshift.*cluster", r"proto.?cluster",
]
LENSING_TITLE_RE = re.compile("|".join(LENSING_TITLE_KEYWORDS), re.IGNORECASE)

# ── False positive patterns (stellar clusters, galactic, calibration) ──
FALSE_POSITIVE_PATTERNS = [
    r"Westerlund", r"Trapezium", r"protoplanetary\s+disk",
    r"Orion", r"protostar", r"IMF\b", r"brown\s+dwarf",
    r"Galactic\s+(Center|Plane|Bulge)",
    r"LMC|SMC|Magellanic",
    r"star.?form", r"YSO", r"T\s+Tauri",
    r"calibrat", r"flat\s+field", r"zero\s+point", r"PSF\s+check",
    r"photometric\s+standard", r"flux\s+standard",
    r"Titan|Jupiter|Saturn|Neptune|Uranus|Mars",
    r"NGC\s?\d{3,4}", r"M\s?\d{2,3}\b",  # nearby galaxies
    r"PHANGS|FEAST|JWST\s+resolved",  # resolved galaxy surveys
    r"supernova\s+remnant", r"SN\s+\d{4}", r"nova\b",
    r"exoplanet|transit|eclipse|coronagraph",
    r"Inner\s+Galaxy", r"Galactic.*Survey",
    r"Roman.*Survey", r"Certum",
]
FALSE_POSITIVE_RE = re.compile("|".join(FALSE_POSITIVE_PATTERNS), re.IGNORECASE)

# Known galaxy cluster targets by name (manual additions from known lensing clusters)
KNOWN_GALAXY_CLUSTERS = {
    "A383", "A2813", "A3192", "A370", "A1689", "A1063",
    "ACO-S-1063", "ACO-1689",
    "ABELL370", "ABELL2744",
    "MACSJ0717+3745", "MACSJ0429-0253", "MACSJ2129-0741",
    "MACSJ0035-2015", "MACSJ2214-1359", "MACSJ0308+2645",
    "MACSJ1149+2223",
    "MACSJ0416.1-2403",
    "MS2137-2353",
    "SPTJ2325-41",
    "RXCJ0032+1808", "RXCJ0232-4420",
    "RXJ2129",
    "SMACSJ2031-4036",
    "WHL0137-08",  # Sunrise Arc cluster
    "PLCKG171-40.7",
    "XLSSC122BCG",
    "CLG-0451-03",
    "SPIDERWEB",
}


def is_galaxy_cluster(target_name, obs_title):
    """Determine if this is a genuine galaxy cluster target."""
    combined = f"{target_name} {obs_title}"

    # Check false positives first
    if FALSE_POSITIVE_RE.search(combined):
        return False

    # Check target name against known clusters
    target_clean = target_name.strip().upper().replace(" ", "")
    for known in KNOWN_GALAXY_CLUSTERS:
        if known.upper().replace(" ", "") in target_clean:
            return True

    # Check target name against galaxy cluster patterns
    if GALAXY_CLUSTER_RE.search(target_name):
        return True

    # Check title for lensing/cluster science keywords
    if LENSING_TITLE_RE.search(obs_title):
        return True

    return False


def check_arxiv_catalogs(target_name):
    """
    Quick heuristic: flag targets that are likely well-cataloged.
    Returns True if the cluster probably has existing catalogs.
    """
    WELL_STUDIED = [
        "ABELL2744", "ABELL-2744", "A2744",     # UNCOVER, GLASS, HFF
        "ABELL370", "ABELL-370", "A370",         # HFF legacy
        "MACS0416", "MACSJ0416",                 # HFF legacy
        "MACS1149", "MACSJ1149",                 # HFF legacy
        "SMACS0723", "SMACS-0723",               # ERO
        "SGAS1226", "SGAS1723",                  # Rigby et al.
    ]
    target_upper = target_name.upper().replace(" ", "").replace("-", "")
    for ws in WELL_STUDIED:
        if ws.replace("-", "") in target_upper:
            return True
    return False


def main():
    print("=" * 100)
    print("JWST NIRCam galaxy-cluster programs without a published photometric catalog")
    print("=" * 100)

    # ── Step 1: Query MAST ──
    print("\n[1/5] Querying MAST for JWST NIRCam/IMAGE Level 3 observations...")
    obs = Observations.query_criteria(
        obs_collection="JWST",
        instrument_name="NIRCAM/IMAGE",
        dataproduct_type="image",
        intentType="science",
        calib_level=3,
    )
    print(f"      Found {len(obs)} Level 3 NIRCam observations total")

    # ── Step 2: Group by proposal_id + target_name ──
    print("\n[2/5] Grouping by program and target...")
    programs = defaultdict(lambda: {
        "targets": defaultdict(lambda: {
            "filters": set(),
            "ra": [],
            "dec": [],
            "dates": [],
        }),
        "pi": "",
        "title": "",
    })

    for row in obs:
        pid = str(row["proposal_id"]).strip()
        target = str(row["target_name"]).strip()
        filt = str(row["filters"]).strip().upper()
        ra = float(row["s_ra"])
        dec = float(row["s_dec"])
        date_str = str(row["t_min"])
        pi = str(row["proposal_pi"]).strip() if "proposal_pi" in obs.colnames else ""
        title = str(row["obs_title"]).strip() if "obs_title" in obs.colnames else ""

        programs[pid]["targets"][target]["filters"].add(filt)
        programs[pid]["targets"][target]["ra"].append(ra)
        programs[pid]["targets"][target]["dec"].append(dec)
        programs[pid]["targets"][target]["dates"].append(date_str)
        if pi:
            programs[pid]["pi"] = pi
        if title:
            programs[pid]["title"] = title

    print(f"      Found {len(programs)} unique programs")

    # ── Step 3: Filter for galaxy clusters with >= 4 broadband filters ──
    print("\n[3/5] Filtering for galaxy cluster targets with >= 4 filters...")

    # Define broadband filters (useful for photometric catalogs / photo-z)
    BROADBAND = {"F070W", "F090W", "F115W", "F150W", "F200W",
                 "F277W", "F356W", "F410M", "F444W",
                 "F140M", "F162M", "F182M", "F210M",
                 "F250M", "F300M", "F335M", "F360M",
                 "F430M", "F460M", "F480M"}

    candidates = []
    for pid, pinfo in programs.items():
        if pid in EXCLUDE_PROGRAMS or pid in WELL_CATALOGED_PROGRAMS:
            continue

        for target, tinfo in pinfo["targets"].items():
            # Count broadband filters (strip pupil combos like F150W2;F162M -> F162M)
            clean_filters = set()
            for f in tinfo["filters"]:
                # Handle dual-wheel combos like "F150W2;F162M" -> take the science filter
                if ";" in f:
                    parts = f.split(";")
                    # Usually the second part is the science filter for medium/narrow bands
                    for p in parts:
                        p = p.strip()
                        if p in BROADBAND:
                            clean_filters.add(p)
                else:
                    if f in BROADBAND:
                        clean_filters.add(f)

            n_broadband = len(clean_filters)
            if n_broadband < 4:
                continue

            # Check if galaxy cluster
            if not is_galaxy_cluster(target, pinfo["title"]):
                continue

            mean_ra = np.mean(tinfo["ra"])
            mean_dec = np.mean(tinfo["dec"])

            # Parse date
            from astropy.time import Time
            try:
                mjds = [float(d) for d in tinfo["dates"] if d and d != "None"]
                earliest_mjd = min(mjds) if mjds else None
                earliest_date = Time(earliest_mjd, format="mjd").iso[:10] if earliest_mjd else "unknown"
            except Exception:
                earliest_date = "unknown"

            # Skip if not yet observed (future dates)
            if earliest_date != "unknown" and earliest_date > "2026-03-27":
                continue

            has_catalog = check_arxiv_catalogs(target)

            candidates.append({
                "pid": pid,
                "pi": pinfo["pi"],
                "title": pinfo["title"],
                "target": target,
                "n_broadband": n_broadband,
                "all_filters": sorted(tinfo["filters"]),
                "broadband_filters": sorted(clean_filters),
                "ra": mean_ra,
                "dec": mean_dec,
                "date": earliest_date,
                "has_known_catalog": has_catalog,
            })

    # Sort: uncataloged first, then by broadband filter count
    candidates.sort(key=lambda x: (x["has_known_catalog"], -x["n_broadband"], x["pid"]))

    # ── Print Results ──
    uncataloged = [c for c in candidates if not c["has_known_catalog"]]
    cataloged = [c for c in candidates if c["has_known_catalog"]]

    print(f"\n      Total galaxy cluster targets with >= 4 broadband filters: {len(candidates)}")
    print(f"      Likely UNCATALOGED (prime targets): {len(uncataloged)}")
    print(f"      Already well-cataloged: {len(cataloged)}")

    print("\n" + "=" * 100)
    print("  PRIME TARGETS: Galaxy Clusters with Deep NIRCam Data & NO Known Published Catalog")
    print("=" * 100)

    for i, c in enumerate(uncataloged, 1):
        sw_filters = [f for f in c["broadband_filters"] if int(re.search(r"\d+", f).group()) < 250]
        lw_filters = [f for f in c["broadband_filters"] if int(re.search(r"\d+", f).group()) >= 250]

        print(f"\n  [{i:2d}] PID {c['pid']}  |  {c['n_broadband']} broadband filters  |  PI: {c['pi']}")
        print(f"       Target:     {c['target']}")
        print(f"       Title:      {c['title'][:90]}")
        print(f"       SW filters: {', '.join(sw_filters)}")
        print(f"       LW filters: {', '.join(lw_filters)}")
        print(f"       All optics: {', '.join(c['all_filters'])}")
        print(f"       RA/Dec:     {c['ra']:.5f}, {c['dec']:.5f}")
        print(f"       Obs date:   {c['date']}")

    # ── Compact summary table ──
    print("\n" + "=" * 100)
    print("  COMPACT SUMMARY (sorted by broadband filter count)")
    print("=" * 100)
    print(f"  {'#':>3s}  {'PID':>6s}  {'BB':>3s}  {'Tot':>3s}  {'Date':>10s}  {'RA':>10s}  {'Dec':>10s}  {'Target':<30s}  {'PI':<25s}")
    print(f"  {'---':>3s}  {'------':>6s}  {'---':>3s}  {'---':>3s}  {'----------':>10s}  {'----------':>10s}  {'----------':>10s}  {'------------------------------':<30s}  {'-------------------------':<25s}")
    for i, c in enumerate(uncataloged, 1):
        print(f"  {i:3d}  {c['pid']:>6s}  {c['n_broadband']:>3d}  {len(c['all_filters']):>3d}  {c['date']:>10s}  {c['ra']:10.5f}  {c['dec']:10.5f}  {c['target'][:30]:<30s}  {c['pi'][:25]:<25s}")

    # ── Group by program ──
    print("\n" + "=" * 100)
    print("  PROGRAMS GROUPED (unique programs for download scripts)")
    print("=" * 100)

    by_program = defaultdict(list)
    for c in uncataloged:
        by_program[c["pid"]].append(c)

    for pid in sorted(by_program, key=lambda p: max(c["n_broadband"] for c in by_program[p]), reverse=True):
        entries = by_program[pid]
        max_bb = max(c["n_broadband"] for c in entries)
        targets = [c["target"] for c in entries]
        pi = entries[0]["pi"]
        title = entries[0]["title"][:80]
        dates = sorted(set(c["date"] for c in entries))

        print(f"\n  PID {pid} ({max_bb} BB filters) | PI: {pi}")
        print(f"    Title: {title}")
        print(f"    Targets ({len(targets)}): {', '.join(targets[:5])}")
        print(f"    Dates: {', '.join(dates[:3])}")

    print(f"\n\nTotal: {len(by_program)} unique uncataloged cluster programs found")
    print(f"Total: {len(uncataloged)} unique cluster-target combinations")
    print("\nDone!")


if __name__ == "__main__":
    main()
