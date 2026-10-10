"""Catalog registry — single source of truth for all reference catalogs.

Each catalog definition drives downloading, cross-matching, output columns,
novelty flags, and reporting.  Adding a new catalog = one entry here + re-run
the download script.

Catalogs are tagged with a ``field`` key that ties them to a known sky region.
The ``detect_field`` helper auto-detects the field from workspace RA/Dec, and
``get_catalogs_for_field`` returns only matching catalogs.
"""
import math
from pathlib import Path

# ---------------------------------------------------------------------------
# Known sky fields — used by detect_field()
# ---------------------------------------------------------------------------
KNOWN_FIELDS = {
    "goods_south": {
        "label": "GOODS-South / JADES",
        "ra_center": 53.126,
        "dec_center": -27.802,
        "match_radius_deg": 5.0,
    },
    "abell2744": {
        "label": "Abell 2744 / Pandora's Cluster",
        "ra_center": 3.586,
        "dec_center": -30.400,
        "match_radius_deg": 5.0,
    },
    "macs0417": {
        "label": "MACS J0417.5-1154",
        "ra_center": 64.394,
        "dec_center": -11.909,
        "match_radius_deg": 5.0,
    },
    "smacs0723": {
        "label": "SMACS J0723.3-7327",
        "ra_center": 110.838,
        "dec_center": -73.453,
        "match_radius_deg": 5.0,
    },
    "goods_north": {
        "label": "GOODS-North / JADES",
        "ra_center": 189.229,
        "dec_center": 62.238,
        "match_radius_deg": 5.0,
    },
    "elgordo": {
        "label": "El Gordo / ACT-CL J0102-4915",
        "ra_center": 15.7208,
        "dec_center": -49.2553,
        "match_radius_deg": 5.0,
    },
    "plckg165": {
        "label": "PLCK G165.7+67.0",
        "ra_center": 171.8125,
        "dec_center": 42.4753,
        "match_radius_deg": 5.0,
    },
    "bullet": {
        "label": "Bullet Cluster (1E 0657-56)",
        "ra_center": 104.6235,
        "dec_center": -55.9469,
        "match_radius_deg": 5.0,
    },
    "sunrise_arc": {
        "label": "WHL J0137-08 (Sunrise Arc / Earendel)",
        "ra_center": 24.3542,
        "dec_center": -8.4556,
        "match_radius_deg": 5.0,
    },
    "abell_s1063": {
        "label": "Abell S1063 (RXC J2248.7-4431)",
        "ra_center": 342.1833,
        "dec_center": -44.5308,
        "match_radius_deg": 5.0,
    },
    "macsj0647": {
        "label": "MACS J0647.7+7015 (CANUCS)",
        "ra_center": 101.9583,
        "dec_center": 70.2500,
        "match_radius_deg": 5.0,
    },
    "plckg191": {
        "label": "PLCK G191.24+62.04 (PEARLS)",
        "ra_center": 161.18,
        "dec_center": 33.85,
        "match_radius_deg": 5.0,
    },
    "clgj1212": {
        "label": "CLG J1212+2733 (PEARLS)",
        "ra_center": 183.08,
        "dec_center": 27.55,
        "match_radius_deg": 5.0,
    },
    "abell1689": {
        "label": "Abell 1689",
        "ra_center": 197.8729,
        "dec_center": -1.3386,
        "match_radius_deg": 5.0,
    },
    "macs0416": {
        "label": "MACS J0416.1-2403 (HFF/CANUCS)",
        "ra_center": 64.039,
        "dec_center": -24.067,
        "match_radius_deg": 5.0,
    },
    "abell370": {
        "label": "Abell 370 (HFF/CANUCS)",
        "ra_center": 39.970,
        "dec_center": -1.577,
        "match_radius_deg": 5.0,
    },
    "macs1149": {
        "label": "MACS J1149.5+2223 (HFF/CANUCS)",
        "ra_center": 177.399,
        "dec_center": 22.399,
        "match_radius_deg": 5.0,
    },
    "macs1423": {
        "label": "MACS J1423.8+2404 (CANUCS)",
        "ra_center": 215.949,
        "dec_center": 24.078,
        "match_radius_deg": 5.0,
    },
    "cosmic_gems": {
        "label": "Cosmic Gems Arc / SPT-CL J0615-5746",
        "ra_center": 93.9667,
        "dec_center": -57.7806,
        "match_radius_deg": 5.0,
    },
    # --- New fields (Phase 10) ---
    "ngdeep": {
        "label": "NGDEEP (HUDF-Par2)",
        "ra_center": 53.1625,
        "dec_center": -27.7914,
        "match_radius_deg": 5.0,
    },
    "ceers_egs": {
        "label": "CEERS / Extended Groth Strip",
        "ra_center": 214.900,
        "dec_center": 52.900,
        "match_radius_deg": 5.0,
    },
    "primer_uds": {
        "label": "PRIMER UDS",
        "ra_center": 34.400,
        "dec_center": -5.150,
        "match_radius_deg": 5.0,
    },
    "dragon_arc": {
        "label": "Dragon Arc / SDSS J1110+6459",
        "ra_center": 167.633,
        "dec_center": 64.990,
        "match_radius_deg": 5.0,
    },
    "ms0451": {
        "label": "MS 0451.6-0305 (Fujimoto PID-6882)",
        "ra_center": 73.545,
        "dec_center": -3.014,
        "match_radius_deg": 5.0,
    },
    "rxj2129": {
        "label": "RX J2129.7+0005 (Fujimoto PID-6882)",
        "ra_center": 322.417,
        "dec_center": 0.089,
        "match_radius_deg": 5.0,
    },
    "rxc_j0600": {
        "label": "RXC J0600.1-2007 (Fujimoto PID-6882)",
        "ra_center": 90.064,
        "dec_center": -20.121,
        "match_radius_deg": 5.0,
    },
    "macs_j0329": {
        "label": "MACS J0329.7-0211 (Fujimoto PID-6882)",
        "ra_center": 52.423,
        "dec_center": -2.196,
        "match_radius_deg": 5.0,
    },
    "rxc_j0032": {
        "label": "RXC J0032.1+1808 (Fujimoto PID-6882)",
        "ra_center": 8.040,
        "dec_center": 18.140,
        "match_radius_deg": 5.0,
    },
    "rxc_j2211": {
        "label": "RXC J2211.7-0350 (Fujimoto PID-6882)",
        "ra_center": 332.940,
        "dec_center": -3.833,
        "match_radius_deg": 5.0,
    },
    "pearls_nep": {
        "label": "PEARLS NEP Time-Domain Field",
        "ra_center": 261.470,
        "dec_center": 66.380,
        "match_radius_deg": 5.0,
    },
}

# ---------------------------------------------------------------------------
# API catalogs (queried live — unique interfaces, extra metadata)
# ---------------------------------------------------------------------------
API_CATALOGS = ["ned", "sdss", "gaia", "simbad", "hsc", "hcv", "ps1",
                "chandra", "catwise", "xmm", "vlass", "spitzer", "erosita"]

# ---------------------------------------------------------------------------
# Local catalogs (downloaded from VizieR TAP → parquet)
#
# tap_table must be the FULL subtable name (e.g. "J/ApJS/207/24/catalog",
# not just "J/ApJS/207/24").  Use TAP_SCHEMA.tables to discover these.
#
# Every entry has a "field" key matching a KNOWN_FIELDS key.
# ---------------------------------------------------------------------------
LOCAL_CATALOGS = [
    # =================================================================
    # GOODS-South / JADES
    # =================================================================
    # --- HST deep-field photometric ---
    {
        "name": "candels_gs",
        "label": "CANDELS",
        "tap_table": "J/ApJS/207/24/catalog",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": None,  # photometry-only catalog, no redshift column
        "z_type": None,
        "field": "goods_south",
    },
    {
        "name": "hst3d",
        "label": "3D-HST",
        "tap_table": "J/ApJS/214/24/3dhstall",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zpk",  # photometric z from EAZY (zsp also available)
        "z_type": "photo",
        "field": "goods_south",
    },
    # --- Spectroscopic (already downloaded as parquet) ---
    {
        "name": "vandels_cdfs",
        "label": "VANDELS",
        "tap_table": "J/A+A/647/A150/cdfs",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zsp",
        "z_type": "spec",
        "field": "goods_south",
    },
    {
        "name": "muse_udf",
        "label": "MUSE-UDF",
        "tap_table": "J/A+A/608/A2/combined",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zMuse",
        "z_type": "spec",
        "field": "goods_south",
    },
    # --- JADES (same field — should match most of our sources) ---
    {
        "name": "jades_dr2_gs",
        "label": "JADES-DR2",
        "tap_table": "V/159/gssample",
        "ra_col": "RA_ICRS",
        "dec_col": "DE_ICRS",
        "z_col": "zphot",
        "z_type": "photo",
        "field": "goods_south",
    },
    {
        "name": "jades_nirspec",
        "label": "JADES-NIRSpec",
        "tap_table": "V/159/gsprism",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zspec",
        "z_type": "spec",
        "field": "goods_south",
    },
    # --- Deep photometric ---
    {
        "name": "astrodeep_jwst_gs",
        "label": "ASTRODEEP-JWST",
        "tap_table": "J/A+A/691/A240/jadesgsp",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zphot",
        "z_type": "photo",
        "field": "goods_south",
    },
    {
        "name": "hlf_goods_s",
        "label": "HLF",
        "tap_table": "J/ApJS/244/16/catv21",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zsp",  # spectroscopic z (only column with z)
        "z_type": "spec",
        "field": "goods_south",
    },
    # --- X-ray ---
    {
        "name": "chandra_7ms",
        "label": "Chandra-7Ms",
        "tap_table": "J/ApJS/228/2/sources",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zF",  # adopted redshift (best of spec/phot)
        "z_type": "spec",
        "field": "goods_south",
    },
    # --- More spectroscopic ---
    {
        "name": "muse_hudf_dr2",
        "label": "MUSE-HUDF-DR2",
        "tap_table": "J/A+A/670/A4/dr2-main",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "field": "goods_south",
    },
    {
        "name": "goods_vimos",
        "label": "GOODS-VIMOS",
        "tap_table": "J/A+A/512/A12/catalogM",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",  # sentinel 9.9999 = no redshift — filter in download
        "z_type": "spec",
        "field": "goods_south",
    },
    {
        "name": "vuds_ecdfs",
        "label": "VUDS",
        "tap_table": "J/A+A/600/A110/ecdfs",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zspec",
        "z_type": "spec",
        "field": "goods_south",
    },
    {
        "name": "muse_wide",
        "label": "MUSE-Wide",
        "tap_table": "J/A+A/606/A12/mw1-24m",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "field": "goods_south",
    },
    # --- Photometric redshifts ---
    {
        "name": "zfourge_cdfs",
        "label": "ZFOURGE",
        "tap_table": "J/ApJ/830/51/zf_cdfs",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zspec",  # spec-z where available (photo-z in separate table)
        "z_type": "spec",
        "field": "goods_south",
    },
    {
        "name": "hsu_ecdfs",
        "label": "Hsu-ECDFS",
        "tap_table": "J/ApJ/796/60/table9",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zbest",  # best of spec/phot
        "z_type": "photo",
        "field": "goods_south",
    },
    {
        "name": "astrodeep_gs43",
        "label": "ASTRODEEP-GS43",
        "tap_table": "J/A+A/649/A22/gs43phot",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": None,  # redshifts in gs43phys (separate table, join by ID)
        "z_type": None,
        "field": "goods_south",
    },
    # Note: GOODS-ALMA (J/A+A/658/A43) is NOT available in VizieR TAP.

    # =================================================================
    # Abell 2744 / Pandora's Cluster
    # =================================================================
    {
        "name": "uncover_a2744",
        "label": "UNCOVER-DR2",
        "tap_table": "J/ApJS/270/7/catalog",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zUse",
        "z_type": "photo",
        "field": "abell2744",
        "extra_cols": {"mu": "mu"},
    },
    {
        "name": "astrodeep_a2744",
        "label": "ASTRODEEP-A2744",
        "tap_table": "J/A+A/691/A240/a2744o",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": None,
        "z_type": None,
        "field": "abell2744",
    },
    {
        "name": "paris_nircam_a2744",
        "label": "Paris-NIRCam-A2744",
        "tap_table": "J/ApJ/952/20/totiso",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": None,
        "z_type": None,
        "field": "abell2744",
    },
    {
        "name": "hff_a2744",
        "label": "HFF-DeepSpace-A2744",
        "tap_table": "J/ApJS/235/14/clugal",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zspec",
        "z_type": "spec",
        "field": "abell2744",
        "where_clause": "\"Cl\" = 'A2744-clu'",
    },
    {
        "name": "astrodeep_a2744_pz",
        "label": "ASTRODEEP-A2744-photoz",
        "tap_table": "J/A+A/691/A240/a2744p",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zphot",
        "z_type": "photo",
        "field": "abell2744",
    },
    {
        "name": "niriss_a2744",
        "label": "Watson-NIRISS-A2744",
        "tap_table": "J/A+A/699/A225/catalog",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zNIRISS",
        "z_type": "spec",
        "field": "abell2744",
    },
    {
        "name": "nedkova_a2744",
        "label": "Nedkova-A2744",
        "tap_table": "J/MNRAS/506/928/a2744clu",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": None,
        "z_type": None,
        "field": "abell2744",
    },
    {
        "name": "richard_muse_a2744",
        "label": "Richard-MUSE-A2744",
        "tap_table": "J/A+A/646/A83/a2744",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "extra_cols": {"mu": "mu"},
        "field": "abell2744",
    },
    {
        "name": "raney_a2744",
        "label": "Raney-A2744",
        "tap_table": "J/MNRAS/492/503/tablea1",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "field": "abell2744",
    },
    {
        "name": "braglia_a2744",
        "label": "Braglia-VLT-A2744",
        "tap_table": "J/A+A/500/947/catalog",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "field": "abell2744",
        "where_clause": "\"A\" = 2744",
    },

    # =================================================================
    # MACS J0417.5-1154
    # =================================================================
    {
        "name": "relics_macs0417",
        "label": "RELICS-MACS0417",
        "tap_table": "J/ApJ/884/85/catalogs",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zb",
        "z_type": "photo",
        "field": "macs0417",
        "where_clause": "\"Name\" LIKE '%0417%'",
    },

    # =================================================================
    # SMACS J0723.3-7327
    # =================================================================
    {
        "name": "relics_smacs0723",
        "label": "RELICS-SMACS0723",
        "tap_table": "J/ApJ/884/85/catalogs",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zb",
        "z_type": "photo",
        "field": "smacs0723",
        "where_clause": "\"Name\" LIKE '%0723%'",
    },
    {
        "name": "amrutha_smacs0723",
        "label": "Amrutha-SMACS0723",
        "tap_table": "J/A+A/699/A105/table1",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "field": "smacs0723",
    },
    {
        "name": "caminha_smacs0723",
        "label": "Caminha-MUSE-SMACS0723",
        "tap_table": "J/A+A/666/L9/muse",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "field": "smacs0723",
        "extra_cols": {"Ampli": "mu"},
    },

    # =================================================================
    # El Gordo / ACT-CL J0102-4915
    # =================================================================
    {
        "name": "relics_elgordo",
        "label": "RELICS-ElGordo",
        "tap_table": "J/ApJ/884/85/catalogs",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zb",
        "z_type": "photo",
        "field": "elgordo",
        "where_clause": "\"Name\" LIKE '%0102%'",
    },
    {
        "name": "caminha_elgordo",
        "label": "Caminha-MUSE-ElGordo",
        "tap_table": "J/A+A/678/A3/catalog",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zsp",
        "z_type": "spec",
        "field": "elgordo",
    },
    {
        "name": "frye_elgordo",
        "label": "Frye-JWST-Lensing-ElGordo",
        "tap_table": "J/ApJ/952/81/table2",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zsp",
        "z_type": "spec",
        "field": "elgordo",
        "extra_cols": {"mu": "mu"},
    },
    {
        "name": "sifon_elgordo",
        "label": "Sifon-ACT-ElGordo",
        "tap_table": "J/ApJ/772/25/table8",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "field": "elgordo",
        "where_clause": "\"ACT-CL\" = 'J0102-4915'",
    },

    # =================================================================
    # GOODS-North / JADES
    # =================================================================
    {
        "name": "hst3d_gn",
        "label": "3D-HST-GN",
        "tap_table": "J/ApJS/214/24/3dhstall",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zpk",
        "z_type": "photo",
        "field": "goods_north",
        "where_clause": "\"DEJ2000\" > 60",
    },
    {
        "name": "uvcandels_gn",
        "label": "UVCANDELS-GN",
        "tap_table": "J/ApJS/275/17/gn-cig",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zphot",
        "z_type": "photo",
        "field": "goods_north",
    },
    # --- ASTRODEEP-JWST GOODS-N (Merlin+ 2024) ---
    {
        "name": "astrodeep_jwst_gn",
        "label": "ASTRODEEP-JWST-GN",
        "tap_table": "J/A+A/691/A240/jadesgnp",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zphot",
        "z_type": "photo",
        "field": "goods_north",
    },
    # --- CANDELS/SHARDS GOODS-N (Barro+ 2019) ---
    {
        "name": "candels_shards_gn",
        "label": "CANDELS-SHARDS-GN",
        "tap_table": "J/ApJS/243/22/phot1",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "field": "goods_north",
    },
    # --- 3D-HST Grism redshifts (Momcheva+ 2016) ---
    {
        "name": "hst3d_grism_gn",
        "label": "3D-HST-Grism-GN",
        "tap_table": "J/ApJS/225/27/3dhst_sp",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zbest",
        "z_type": "grism",
        "field": "goods_north",
        "where_clause": "\"DEJ2000\" > 60",
    },
    # --- GOODS-Herschel North (Elbaz+ 2011) ---
    {
        "name": "goods_herschel_gn",
        "label": "GOODS-Herschel-GN",
        "tap_table": "J/A+A/533/A119/ghn",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "field": "goods_north",
    },

]


# ---------------------------------------------------------------------------
# MAST HLSP catalogs (downloaded as FITS → parquet)
#
# These use a separate download path (download_mast_catalogs.py) but end up
# in the same reference_catalogs/ dir as VizieR parquets.  The crossmatch
# stage just needs {name}.parquet to exist — it doesn't care about origin.
# ---------------------------------------------------------------------------
MAST_CATALOGS = [
    {
        "name": "jades_hlsp_gs",
        "label": "JADES-HLSP-Phot-GS",
        "mast_url": "https://archive.stsci.edu/hlsps/jades/dr2/goods-s/catalogs/hlsp_jades_jwst_nircam_goods-s-deep_photometry_v2.0_catalog.fits",
        "field": "goods_south",
    },
    {
        "name": "jades_hlsp_gn",
        "label": "JADES-HLSP-Phot-GN",
        "mast_url": "https://archive.stsci.edu/hlsps/jades/dr3/goods-n/catalogs/hlsp_jades_jwst_nircam_goods-n_photometry_v1.0_catalog.fits",
        "field": "goods_north",
    },
    {
        "name": "canucs_macs0417_clu",
        "label": "CANUCS-DR1-MACS0417-CLU",
        "mast_url": "https://ws-cadc.canfar.net/vault/files/canucs/CANUCS_DR1/catalogs/hlsp_canucs_jwst-hst_multi_macs0417-clu_multi_v1_photometry-cat.fits",
        "field": "macs0417",
    },
    {
        "name": "canucs_macs0417_ncf",
        "label": "CANUCS-DR1-MACS0417-NCF",
        "mast_url": "https://ws-cadc.canfar.net/vault/files/canucs/CANUCS_DR1/catalogs/hlsp_canucs_jwst-hst_multi_macs0417-ncf_multi_v1_photometry-cat.fits",
        "field": "macs0417",
    },
    {
        "name": "grizli_smacs0723",
        "label": "Grizli-SMACS0723-Phot",
        "mast_url": "https://zenodo.org/api/records/6874301/files/smacs0723_phot_apcorr.fits/content",
        "field": "smacs0723",
    },
    {
        "name": "noirot_smacs0723",
        "label": "Noirot-NIRISS-SMACS0723",
        "mast_url": "https://niriss.github.io/smacs0723/redshift_catalog.txt",
        "field": "smacs0723",
    },
    # --- DJA grizli photometry for PLCK G165 (unpublished pipeline product) ---
    {
        "name": "dja_plckg165",
        "label": "DJA-grizli-PLCKG165",
        "mast_url": "https://s3.amazonaws.com/grizli-v2/JwstMosaics/v7/plck-g165p67-v7.4_phot.fits",
        "field": "plckg165",
    },
    # --- DJA grizli photometry for WHL0137 / Sunrise Arc ---
    {
        "name": "dja_whl0137",
        "label": "DJA-grizli-Sunrise",
        "mast_url": "https://s3.amazonaws.com/grizli-v2/JwstMosaics/v7/sunrise-grizli-v7.0-fix_phot_apcorr.fits",
        "field": "sunrise_arc",
    },
    # --- CANUCS DR1 catalogs for 4 new clusters ---
    {
        "name": "canucs_macs0416_clu",
        "label": "CANUCS-DR1-MACS0416-CLU",
        "mast_url": "https://ws-cadc.canfar.net/vault/files/canucs/CANUCS_DR1/catalogs/hlsp_canucs_jwst-hst_multi_macs0416-clu_multi_v1_photometry-cat.fits",
        "field": "macs0416",
    },
    {
        "name": "canucs_macs0416_ncf",
        "label": "CANUCS-DR1-MACS0416-NCF",
        "mast_url": "https://ws-cadc.canfar.net/vault/files/canucs/CANUCS_DR1/catalogs/hlsp_canucs_jwst-hst_multi_macs0416-ncf_multi_v1_photometry-cat.fits",
        "field": "macs0416",
    },
    {
        "name": "canucs_a370_clu",
        "label": "CANUCS-DR1-A370-CLU",
        "mast_url": "https://ws-cadc.canfar.net/vault/files/canucs/CANUCS_DR1/catalogs/hlsp_canucs_jwst-hst_multi_a370-clu_multi_v1_photometry-cat.fits",
        "field": "abell370",
    },
    {
        "name": "canucs_a370_ncf",
        "label": "CANUCS-DR1-A370-NCF",
        "mast_url": "https://ws-cadc.canfar.net/vault/files/canucs/CANUCS_DR1/catalogs/hlsp_canucs_jwst-hst_multi_a370-ncf_multi_v1_photometry-cat.fits",
        "field": "abell370",
    },
    {
        "name": "canucs_macs1149_clu",
        "label": "CANUCS-DR1-MACS1149-CLU",
        "mast_url": "https://ws-cadc.canfar.net/vault/files/canucs/CANUCS_DR1/catalogs/hlsp_canucs_jwst-hst_multi_macs1149-clu_multi_v1_photometry-cat.fits",
        "field": "macs1149",
    },
    {
        "name": "canucs_macs1149_ncf",
        "label": "CANUCS-DR1-MACS1149-NCF",
        "mast_url": "https://ws-cadc.canfar.net/vault/files/canucs/CANUCS_DR1/catalogs/hlsp_canucs_jwst-hst_multi_macs1149-ncf_multi_v1_photometry-cat.fits",
        "field": "macs1149",
    },
    {
        "name": "canucs_macs1423_clu",
        "label": "CANUCS-DR1-MACS1423-CLU",
        "mast_url": "https://ws-cadc.canfar.net/vault/files/canucs/CANUCS_DR1/catalogs/hlsp_canucs_jwst-hst_multi_macs1423-clu_multi_v1_photometry-cat.fits",
        "field": "macs1423",
    },
    {
        "name": "canucs_macs1423_ncf",
        "label": "CANUCS-DR1-MACS1423-NCF",
        "mast_url": "https://ws-cadc.canfar.net/vault/files/canucs/CANUCS_DR1/catalogs/hlsp_canucs_jwst-hst_multi_macs1423-ncf_multi_v1_photometry-cat.fits",
        "field": "macs1423",
    },
    # --- DJA grizli v7 photometric catalogs (new fields) ---
    {
        "name": "dja_ceers",
        "label": "DJA-grizli-CEERS",
        "mast_url": "https://s3.amazonaws.com/grizli-v2/JwstMosaics/v7/ceers-full-grizli-v7.4_phot.fits",
        "field": "ceers_egs",
    },
    {
        "name": "dja_ngdeep",
        "label": "DJA-grizli-NGDEEP",
        "mast_url": "https://s3.amazonaws.com/grizli-v2/JwstMosaics/v7/ngdeep-grizli-v7.2_phot.fits",
        "field": "ngdeep",
    },
    {
        "name": "dja_rxj2129",
        "label": "DJA-grizli-RXJ2129",
        "mast_url": "https://s3.amazonaws.com/grizli-v2/JwstMosaics/v7/rxj2129-grizli-v7.0-fix_phot_apcorr.fits",
        "field": "rxj2129",
    },
    # --- DJA grizli v7 photometric catalogs ---
    {
        "name": "dja_macs0647",
        "label": "DJA-grizli-MACS0647",
        "mast_url": "https://s3.amazonaws.com/grizli-v2/JwstMosaics/v7/macs0647-grizli-v7.0-fix_phot_apcorr.fits",
        "field": "macsj0647",
    },
    {
        "name": "dja_elgordo",
        "label": "DJA-grizli-ElGordo",
        "mast_url": "https://s3.amazonaws.com/grizli-v2/JwstMosaics/v7/elgordo-grizli-v7.0-fix_phot_apcorr.fits",
        "field": "elgordo",
    },
    {
        "name": "dja_abell1689",
        "label": "DJA-grizli-Abell1689",
        "mast_url": "https://s3.amazonaws.com/grizli-v2/JwstMosaics/v7/abell1689-grizli-v7.4_phot.fits",
        "field": "abell1689",
    },
    {
        "name": "dja_clgj1212",
        "label": "DJA-grizli-CLGJ1212",
        "mast_url": "https://s3.amazonaws.com/grizli-v2/JwstMosaics/v7/clg-1212p2733-v7.4_phot.fits",
        "field": "clgj1212",
    },
    {
        "name": "dja_plckg191",
        "label": "DJA-grizli-PLCKG191",
        "mast_url": "https://s3.amazonaws.com/grizli-v2/JwstMosaics/v7/plck-g191p62-v7.4_phot.fits",
        "field": "plckg191",
    },
    # --- DJA grizli v7.5 for Abell S1063 ---
    {
        "name": "dja_abells1063",
        "label": "DJA-grizli-AbellS1063",
        "mast_url": "https://s3.amazonaws.com/grizli-v2/JwstMosaics/v7/abells1063-grizli-v7.5_phot.fits",
        "field": "abell_s1063",
    },
]

# ---------------------------------------------------------------------------
# Additional LOCAL catalogs for NEW fields (Sunrise Arc, Abell S1063, MACS J0647)
# ---------------------------------------------------------------------------
LOCAL_CATALOGS += [
    # =================================================================
    # Sunrise Arc / WHL J0137-08
    # =================================================================
    # RELICS HST photometry (Coe+2019, covers all 41 RELICS clusters)
    {
        "name": "relics_whl0137",
        "label": "RELICS-HST",
        "tap_table": "J/ApJ/884/85/catalogs",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zb",
        "z_type": "photo",
        "where_clause": "\"Name\" LIKE '%0137%'",
        "field": "sunrise_arc",
    },
    # RELICS high-z candidates (Salmon+2020)
    {
        "name": "relics_hz_whl0137",
        "label": "RELICS-HighZ",
        "tap_table": "J/ApJ/889/189/table234",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zAvg",
        "z_type": "photo",
        "where_clause": "\"Cl\" LIKE '%0137%'",
        "field": "sunrise_arc",
    },
    # Diego+2023 strong lensing constraints for WHL0137
    {
        "name": "diego_sl_whl0137",
        "label": "Diego-SL-2023",
        "tap_table": "J/A+A/681/A124/tableb1",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zMUSE",
        "z_type": "spec",
        "field": "sunrise_arc",
    },

    # =================================================================
    # Abell S1063 (RXC J2248.7-4431)
    # =================================================================
    # HFF 6-cluster photometric catalog — cluster field (Shipley+2018)
    {
        "name": "hff_a1063_clu",
        "label": "HFF-Shipley-CLU",
        "tap_table": "J/ApJS/235/14/clugal",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zspec",
        "z_type": "spec",
        "where_clause": "\"Cl\" LIKE '%1063%'",
        "field": "abell_s1063",
    },
    # HFF 6-cluster photometric catalog — parallel field (Shipley+2018)
    {
        "name": "hff_a1063_par",
        "label": "HFF-Shipley-PAR",
        "tap_table": "J/ApJS/235/14/pargal",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zspec",
        "z_type": "spec",
        "where_clause": "\"Par\" LIKE '%1063%'",
        "field": "abell_s1063",
    },
    # Abell S1063 dedicated photometric catalog (Nedkova+2021)
    {
        "name": "nedkova_a1063_clu",
        "label": "Nedkova-2021-CLU",
        "tap_table": "J/MNRAS/506/928/a1063clu",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": None,
        "z_type": None,
        "field": "abell_s1063",
    },
    # Abell S1063 parallel field (Nedkova+2021)
    {
        "name": "nedkova_a1063_par",
        "label": "Nedkova-2021-PAR",
        "tap_table": "J/MNRAS/506/928/a1063par",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": None,
        "z_type": None,
        "field": "abell_s1063",
    },
    # Note: Abell S1063 is NOT in RELICS (it's an HFF cluster only)
    # Bouwens+2022 z=6-8 candidates in HFF clusters
    {
        "name": "bouwens_hz_a1063",
        "label": "Bouwens-HighZ-2022",
        "tap_table": "J/ApJ/927/81/table2",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": None,
        "z_type": None,
        "field": "abell_s1063",
    },
    # Lensed images — multiple image systems (Cerny+2018)
    {
        "name": "lensing_a1063",
        "label": "Lensing-MultiImg",
        "tap_table": "J/ApJ/855/4/multimg",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zsp",
        "z_type": "spec",
        "where_clause": "\"Cl\" LIKE '%1063%'",
        "field": "abell_s1063",
    },

    # =================================================================
    # MACS J0647.7+7015
    # =================================================================
    # Dedicated MACS J0647 photometric redshift catalog (Molino+2017)
    {
        "name": "molino_macs0647",
        "label": "Molino-PhotoZ-2017",
        "tap_table": "J/MNRAS/470/95/macs0647",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zb1",
        "z_type": "photo",
        "field": "macsj0647",
    },
    # CLASH galaxy properties (Connor+2017) — covers MACS J0647
    {
        "name": "clash_macs0647",
        "label": "CLASH-Connor-2017",
        "tap_table": "J/ApJ/848/37/table7",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "Zb",
        "z_type": "photo",
        "where_clause": "\"Cluster\" LIKE '%0647%'",
        "field": "macsj0647",
    },
    # Note: MACS J0647 is NOT in RELICS (it's a CLASH/CANUCS target)

    # =================================================================
    # MACS J0416.1-2403 (HFF + CANUCS)
    # =================================================================
    {
        "name": "hff_macs0416_clu",
        "label": "HFF-Shipley-CLU",
        "tap_table": "J/ApJS/235/14/clugal",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zspec",
        "z_type": "spec",
        "where_clause": "\"Cl\" = 'M0416-clu'",
        "field": "macs0416",
    },
    {
        "name": "hff_macs0416_par",
        "label": "HFF-Shipley-PAR",
        "tap_table": "J/ApJS/235/14/pargal",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zspec",
        "z_type": "spec",
        "where_clause": "\"Par\" LIKE '%0416%'",
        "field": "macs0416",
    },
    {
        "name": "nedkova_macs0416_clu",
        "label": "Nedkova-2021-CLU",
        "tap_table": "J/MNRAS/506/928/m0416clu",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": None,
        "z_type": None,
        "field": "macs0416",
    },
    {
        "name": "richard_muse_macs0416",
        "label": "Richard-MUSE-MACS0416",
        "tap_table": "J/A+A/646/A83/m0416",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "extra_cols": {"mu": "mu"},
        "field": "macs0416",
    },
    {
        "name": "caminha_macs0416",
        "label": "Caminha-MUSE-MACS0416",
        "tap_table": "J/A+A/632/A36/tablea2",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "field": "macs0416",
    },

    # =================================================================
    # Abell 370 (HFF + CANUCS)
    # =================================================================
    {
        "name": "hff_abell370_clu",
        "label": "HFF-Shipley-CLU",
        "tap_table": "J/ApJS/235/14/clugal",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zspec",
        "z_type": "spec",
        "where_clause": "\"Cl\" = 'A370-clu'",
        "field": "abell370",
    },
    {
        "name": "hff_abell370_par",
        "label": "HFF-Shipley-PAR",
        "tap_table": "J/ApJS/235/14/pargal",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zspec",
        "z_type": "spec",
        "where_clause": "\"Par\" LIKE '%370%'",
        "field": "abell370",
    },
    {
        "name": "nedkova_abell370_clu",
        "label": "Nedkova-2021-CLU",
        "tap_table": "J/MNRAS/506/928/a370clu",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": None,
        "z_type": None,
        "field": "abell370",
    },
    {
        "name": "richard_muse_abell370",
        "label": "Richard-MUSE-A370",
        "tap_table": "J/A+A/646/A83/a370",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "extra_cols": {"mu": "mu"},
        "field": "abell370",
    },
    {
        "name": "lagattuta_abell370",
        "label": "Lagattuta-MUSE-A370",
        "tap_table": "J/MNRAS/485/3738/tablea1",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "field": "abell370",
    },

    # =================================================================
    # MACS J1149.5+2223 (HFF + CANUCS)
    # =================================================================
    {
        "name": "hff_macs1149_clu",
        "label": "HFF-Shipley-CLU",
        "tap_table": "J/ApJS/235/14/clugal",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zspec",
        "z_type": "spec",
        "where_clause": "\"Cl\" = 'M1149-clu'",
        "field": "macs1149",
    },
    {
        "name": "hff_macs1149_par",
        "label": "HFF-Shipley-PAR",
        "tap_table": "J/ApJS/235/14/pargal",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zspec",
        "z_type": "spec",
        "where_clause": "\"Par\" LIKE '%1149%'",
        "field": "macs1149",
    },
    {
        "name": "nedkova_macs1149_clu",
        "label": "Nedkova-2021-CLU",
        "tap_table": "J/MNRAS/506/928/m1149clu",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": None,
        "z_type": None,
        "field": "macs1149",
    },
    {
        "name": "richard_muse_macs1149",
        "label": "Richard-MUSE-MACS1149",
        "tap_table": "J/A+A/646/A83/m1149",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "extra_cols": {"mu": "mu"},
        "field": "macs1149",
    },

    # =================================================================
    # MACS J1423.8+2404 (CANUCS — less studied, no HFF)
    # =================================================================
    # No HFF or RELICS coverage — mostly unexplored

    # =================================================================
    # Bullet Cluster (1E 0657-56) — no VizieR TAP catalogs available
    # Only DJA grizli (when available) and live API crossmatch
    # =================================================================

    # =================================================================
    # Abell 1689 — no VizieR TAP catalogs available
    # Only DJA grizli (dja_abell1689 in MAST_CATALOGS) and live API crossmatch
    # =================================================================

    # =================================================================
    # CEERS / Extended Groth Strip
    # =================================================================
    {
        "name": "astrodeep_ceers",
        "label": "ASTRODEEP-JWST-CEERS",
        "tap_table": "J/A+A/691/A240/ceerso",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": None,
        "z_type": None,
        "field": "ceers_egs",
    },
    {
        "name": "astrodeep_ceers_pz",
        "label": "ASTRODEEP-JWST-CEERS-photoz",
        "tap_table": "J/A+A/691/A240/ceersp",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zphot",
        "z_type": "photo",
        "field": "ceers_egs",
    },
    {
        "name": "candels_egs",
        "label": "CANDELS-EGS",
        "tap_table": "J/ApJS/229/32/table6",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": None,
        "z_type": None,
        "field": "ceers_egs",
    },

    # =================================================================
    # NGDEEP (HUDF-Par2)
    # =================================================================
    {
        "name": "astrodeep_ngdeep",
        "label": "ASTRODEEP-JWST-NGDEEP",
        "tap_table": "J/A+A/691/A240/ngdeepo",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": None,
        "z_type": None,
        "field": "ngdeep",
    },
    {
        "name": "astrodeep_ngdeep_pz",
        "label": "ASTRODEEP-JWST-NGDEEP-photoz",
        "tap_table": "J/A+A/691/A240/ngdeepp",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zphot",
        "z_type": "photo",
        "field": "ngdeep",
    },

    # =================================================================
    # RX J2129.7+0005
    # =================================================================
    {
        "name": "clash_rxj2129",
        "label": "CLASH-Molino-RXJ2129",
        "tap_table": "J/MNRAS/470/95/rxj2129",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zb1",
        "z_type": "photo",
        "field": "rxj2129",
    },

    # =================================================================
    # MS 0451.6-0305
    # =================================================================
    {
        "name": "jauzac_ms0451",
        "label": "Jauzac-MUSE-MS0451",
        "tap_table": "J/MNRAS/508/1206/tablea2",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "field": "ms0451",
    },

    # =================================================================
    # Cosmic Gems / SPT-CL J0615-5746
    # =================================================================
    {
        "name": "relics_spt0615",
        "label": "RELICS-SPT0615",
        "tap_table": "J/ApJ/884/85/catalogs",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zb",
        "z_type": "photo",
        "where_clause": "\"Name\" LIKE '%0615%'",
        "field": "cosmic_gems",
    },
    {
        "name": "relics_hz_spt0615",
        "label": "RELICS-HighZ-SPT0615",
        "tap_table": "J/ApJ/889/189/table234",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zAvg",
        "z_type": "photo",
        "where_clause": "\"Cl\" LIKE '%0615%'",
        "field": "cosmic_gems",
    },

    # =================================================================
    # RXC J0600-2007 (Phase 11 — new catalogs)
    # =================================================================
    {
        "name": "relics_rxc0600",
        "label": "RELICS-RXC0600",
        "tap_table": "J/ApJ/884/85/catalogs",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zb",
        "z_type": "photo",
        "where_clause": "\"Name\" LIKE '%0600%'",
        "field": "rxc_j0600",
    },
    {
        "name": "relics_hz_rxc0600",
        "label": "RELICS-HighZ-RXC0600",
        "tap_table": "J/ApJ/910/135/table1",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "where_clause": "\"Cl\" LIKE '%rxc0600%'",
        "field": "rxc_j0600",
    },

    # =================================================================
    # RXC J2211.7-0350 (Phase 11 — new catalogs)
    # =================================================================
    {
        "name": "relics_rxc2211",
        "label": "RELICS-RXC2211",
        "tap_table": "J/ApJ/884/85/catalogs",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zb",
        "z_type": "photo",
        "where_clause": "\"Name\" LIKE '%2211%'",
        "field": "rxc_j2211",
    },
    {
        "name": "relics_hz_rxc2211",
        "label": "RELICS-HighZ-RXC2211",
        "tap_table": "J/ApJ/910/135/table1",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "where_clause": "\"Cl\" LIKE '%rxc2211%'",
        "field": "rxc_j2211",
    },

    # =================================================================
    # MACS J0329.7-0211 (Phase 11 — new catalogs)
    # =================================================================
    {
        "name": "clash_macs0329",
        "label": "CLASH-Molino-MACS0329",
        "tap_table": "J/MNRAS/470/95/macs0329",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zb1",
        "z_type": "photo",
        "field": "macs_j0329",
    },
    {
        "name": "richard_muse_macs0329",
        "label": "Richard-MUSE-MACS0329",
        "tap_table": "J/A+A/646/A83/mc329",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "field": "macs_j0329",
    },

    # =================================================================
    # Bullet Cluster (Phase 11 — new catalogs)
    # =================================================================
    {
        "name": "richard_muse_bullet",
        "label": "Richard-MUSE-Bullet",
        "tap_table": "J/A+A/646/A83/bullet",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "field": "bullet",
    },

    # =================================================================
    # MS 0451.6-0305 (Phase 11 — additional catalogs)
    # =================================================================
    {
        "name": "moran_ms0451",
        "label": "Moran-MS0451",
        "tap_table": "J/ApJ/671/1503/table2",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "field": "ms0451",
    },

    # =================================================================
    # Abell S1063 (Phase 11 — additional catalogs)
    # =================================================================
    {
        "name": "clash_abells1063",
        "label": "CLASH-Molino-AbellS1063",
        "tap_table": "J/MNRAS/470/95/rxj2248",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zb1",
        "z_type": "photo",
        "field": "abell_s1063",
    },
    {
        "name": "bradac_abells1063_phot",
        "label": "Bradac-ASTRODEEP-AbellS1063-phot",
        "tap_table": "J/MNRAS/489/99/j2248ph",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "field": "abell_s1063",
    },
    {
        "name": "bradac_abells1063_prop",
        "label": "Bradac-ASTRODEEP-AbellS1063-prop",
        "tap_table": "J/MNRAS/489/99/j2248pr",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zbest",
        "z_type": "photo",
        "field": "abell_s1063",
    },

    # =================================================================
    # RX J2129.7+0005 (Phase 11 — additional catalogs)
    # =================================================================
    {
        "name": "jauzac_rxj2129",
        "label": "Jauzac-MUSE-RXJ2129",
        "tap_table": "J/MNRAS/508/1206/tablea1",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "field": "rxj2129",
    },

    # =================================================================
    # Phase 12 — New catalogs for high-novel-rate fields
    # =================================================================
    # Girardi 2024 CLASH-VLT for MACS J0329 (1712 spec-z)
    {
        "name": "girardi_macs0329",
        "label": "Girardi-CLASH-VLT-MACS0329",
        "tap_table": "J/A+A/692/A175/table1",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "zspec",
        "z_type": "spec",
        "field": "macs_j0329",
    },
    # Caminha 2019 CLASH spectroscopic — MACS J0329 portion (105 spec-z)
    {
        "name": "caminha_clash_macs0329",
        "label": "Caminha-CLASH-MACS0329",
        "tap_table": "J/A+A/632/A36/table2",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "field": "macs_j0329",
        "where_clause": "\"RAJ2000\" BETWEEN 52.0 AND 53.0",
    },
    # Caminha 2019 CLASH spectroscopic — RXJ 2129 portion (354 spec-z)
    {
        "name": "caminha_clash_rxj2129",
        "label": "Caminha-CLASH-RXJ2129",
        "tap_table": "J/A+A/632/A36/table2",
        "ra_col": "RAJ2000",
        "dec_col": "DEJ2000",
        "z_col": "z",
        "z_type": "spec",
        "field": "rxj2129",
        "where_clause": "\"RAJ2000\" BETWEEN 322.0 AND 323.0",
    },

    # =================================================================
    # RXC J0032.1+1808 — RELICS HST photometric catalog (2,445 sources)
    # =================================================================
    {
        "name": "relics_rxc0032",
        "label": "RELICS-RXC0032",
        "tap_table": "J/ApJ/884/85/catalogs",
        "ra_col": "ra",
        "dec_col": "dec",
        "z_col": "zb",
        "z_type": "photo",
        "where_clause": "\"Name\" LIKE '%0032%'",
        "field": "rxc_j0032",
    },
    {
        "name": "relics_hz_rxc0032",
        "label": "RELICS-HighZ-RXC0032",
        "tap_table": "J/ApJ/889/189/table234",
        "ra_col": "ra",
        "dec_col": "dec",
        "where_clause": "\"Cl\" LIKE '%rxc0032%'",
        "field": "rxc_j0032",
    },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_ref_catalogs_dir() -> Path:
    """Return the directory for locally cached reference catalogs."""
    d = Path.home() / ".jwst-discovery" / "reference_catalogs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_local_catalog(name: str) -> dict | None:
    """Look up a catalog definition by name (LOCAL + MAST). Returns None if not found."""
    for cat in LOCAL_CATALOGS:
        if cat["name"] == name:
            return cat
    for cat in MAST_CATALOGS:
        if cat["name"] == name:
            return cat
    return None


def get_all_catalog_names() -> list[str]:
    """Return all catalog names (API + local + MAST) in matching order."""
    return (API_CATALOGS
            + [c["name"] for c in LOCAL_CATALOGS]
            + [c["name"] for c in MAST_CATALOGS])


def _angular_separation_deg(ra1: float, dec1: float,
                             ra2: float, dec2: float) -> float:
    """Haversine angular separation in degrees between two sky positions."""
    ra1_r, dec1_r = math.radians(ra1), math.radians(dec1)
    ra2_r, dec2_r = math.radians(ra2), math.radians(dec2)
    dra = ra2_r - ra1_r
    ddec = dec2_r - dec1_r
    a = (math.sin(ddec / 2) ** 2 +
         math.cos(dec1_r) * math.cos(dec2_r) * math.sin(dra / 2) ** 2)
    return math.degrees(2 * math.asin(math.sqrt(a)))


def detect_field(ra_center: float, dec_center: float) -> str | None:
    """Auto-detect field from workspace center RA/Dec.

    Compares the given position against every KNOWN_FIELDS entry and returns
    the name of the closest field within its ``match_radius_deg``, or *None*
    if no field is close enough.
    """
    best_name: str | None = None
    best_sep = float("inf")

    for name, info in KNOWN_FIELDS.items():
        sep = _angular_separation_deg(
            ra_center, dec_center,
            info["ra_center"], info["dec_center"],
        )
        if sep < info["match_radius_deg"] and sep < best_sep:
            best_sep = sep
            best_name = name

    return best_name


def get_catalogs_for_field(field_name: str | None) -> list[dict]:
    """Return LOCAL_CATALOGS + MAST_CATALOGS filtered to the given field.

    If *field_name* is None (unknown field), returns an empty list so that
    only API catalogs are used for cross-matching.
    """
    if field_name is None:
        return []
    return ([c for c in LOCAL_CATALOGS if c.get("field") == field_name]
            + [c for c in MAST_CATALOGS if c.get("field") == field_name])


# ---------------------------------------------------------------------------
# HLSP Mosaics — pre-built mosaics for known fields
# ---------------------------------------------------------------------------
HLSP_MOSAICS: dict[str, dict] = {
    "goods_north": {
        "label": "JADES DR3 GOODS-North",
        "source": "direct_url",
        "base_url": "https://archive.stsci.edu/hlsps/jades/dr3/goods-n/images",
        "filename_template": "hlsp_jades_jwst_nircam_goods-n_{filter_lower}_v1.0_drz.fits",
        "filters": ["F090W", "F115W", "F150W", "F182M", "F200W", "F210M",
                     "F277W", "F335M", "F356W", "F410M", "F444W"],
        "est_size_gb_per_filter": 10.0,
    },
    "goods_south": {
        "label": "JADES GOODS-South",
        "source": "mast_query",
        "program_ids": ["1210", "1180"],
        "filters": ["F090W", "F115W", "F150W", "F200W",
                     "F277W", "F335M", "F356W", "F410M", "F444W"],
        "est_size_gb_per_filter": 2.0,
    },
    "macs0417": {
        "label": "CANUCS MACS-J0417",
        "source": "mast_query",
        "program_ids": ["1208"],
        "target_name": "MACSJ0417.5-1154",
        "filters": ["F090W", "F115W", "F140M", "F150W", "F182M", "F200W",
                     "F210M", "F250M", "F277W", "F300M", "F335M", "F356W",
                     "F360M", "F410M", "F444W"],
        "est_size_gb_per_filter": 0.9,
    },
    "smacs0723": {
        "label": "ERO SMACS-0723",
        "source": "mast_query",
        "program_ids": ["2736"],
        "filters": ["F090W", "F150W", "F200W", "F277W", "F356W", "F444W"],
        "est_size_gb_per_filter": 1.0,
    },
    # abell2744: no known HLSP — uses standard MAST ingest
}


def get_hlsp_mosaics_for_field(field_name: str) -> dict | None:
    """Return HLSP mosaic info for a known field, or None."""
    return HLSP_MOSAICS.get(field_name)


def get_known_field_info(field_name: str) -> dict | None:
    """Return a summary dict for a known field (for UI display)."""
    if field_name not in KNOWN_FIELDS:
        return None
    info = KNOWN_FIELDS[field_name]
    catalogs = get_catalogs_for_field(field_name)
    hlsp = HLSP_MOSAICS.get(field_name)
    return {
        "field": field_name,
        "label": info["label"],
        "ra_center": info["ra_center"],
        "dec_center": info["dec_center"],
        "catalog_count": len(catalogs),
        "catalogs": [{"name": c["name"], "label": c.get("label", c["name"])} for c in catalogs],
        "has_hlsp_mosaics": hlsp is not None,
        "mosaic_filters": hlsp["filters"] if hlsp else [],
        "mosaic_label": hlsp["label"] if hlsp else None,
        "est_mosaic_size_gb": round(len(hlsp["filters"]) * hlsp["est_size_gb_per_filter"], 1) if hlsp else 0,
    }


def list_known_fields() -> list[dict]:
    """Return info dicts for all known fields (for UI field picker)."""
    return [get_known_field_info(f) for f in KNOWN_FIELDS]
