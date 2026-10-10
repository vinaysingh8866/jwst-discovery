#!/usr/bin/env python3
"""Re-run characterize → crossmatch → anomaly → report on all 27 fields.

After fixing:
  - characterize.py: CI extraction, grid-edge/chi2-low/CI-width quality checks
  - anomaly.py: 0.5" dedup radius, fragments removed from novel, added to artifact mask
  - consolidate_validation.py: pipeline-vs-refit dz, CI width in verdicts
  - run_photoz_crosscheck.py: exact z_grid, dz_pipeline, CI width in confidence

Usage:
    python scripts/rerun_full_pipeline.py              # all 27 fields
    python scripts/rerun_full_pipeline.py elgordo       # single field
    python scripts/rerun_full_pipeline.py --list        # list fields
    python scripts/rerun_full_pipeline.py --from anomaly # start from specific stage
"""
import sys
import time
import traceback
from pathlib import Path
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sidecar"))

from jwst_pipeline.stages import STAGE_REGISTRY

# ── All 27 processed fields ──
FIELDS = {
    "abell370": {
        "workspace": "Abell-370",
        "config": {
            "target_field": "Abell-370", "program_id": "1208",
            "center_ra": 39.970, "center_dec": -1.577,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 0.8, "min_area": 3,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "abell1689": {
        "workspace": "Abell-1689",
        "config": {
            "target_field": "Abell-1689", "program_id": "1840",
            "center_ra": 197.8729, "center_dec": -1.3386,
            "filters": ["F115W", "F150W", "F182M", "F200W", "F277W", "F300M", "F356W", "F444W"],
            "detection_threshold": 0.8, "min_area": 3,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "abell2744": {
        "workspace": "Abell-2744",
        "config": {
            "target_field": "Abell-2744", "program_id": "2561",
            "center_ra": 3.586, "center_dec": -30.400,
            "filters": ["F115W", "F150W", "F200W", "F277W", "F356W", "F444W"],
            "detection_threshold": 2.0, "min_area": 5,
            "crossmatch_radius_arcsec": 2.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "abell_s1063": {
        "workspace": "Abell-S1063",
        "config": {
            "target_field": "Abell-S1063", "program_id": "3293",
            "center_ra": 342.1833, "center_dec": -44.5308,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W", "F480M"],
            "detection_threshold": 0.8, "min_area": 3,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "bullet": {
        "workspace": "Bullet-Cluster",
        "config": {
            "target_field": "Bullet-Cluster", "program_id": "4598",
            "center_ra": 104.6235, "center_dec": -55.9469,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 0.8, "min_area": 3,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "ceers_egs": {
        "workspace": "CEERS-EGS",
        "config": {
            "target_field": "CEERS-EGS", "program_id": "1345",
            "center_ra": 214.900, "center_dec": 52.900,
            "filters": ["F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 1.5, "min_area": 5,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "clgj1212": {
        "workspace": "CLG-J1212",
        "config": {
            "target_field": "CLG-J1212", "program_id": "1176",
            "center_ra": 183.08, "center_dec": 27.55,
            "filters": ["F090W", "F150W", "F200W", "F277W", "F356W", "F444W"],
            "detection_threshold": 0.8, "min_area": 3,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "cosmic_gems": {
        "workspace": "Cosmic-Gems",
        "config": {
            "target_field": "Cosmic-Gems", "program_id": "4212",
            "center_ra": 93.9654, "center_dec": -57.7803,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 0.8, "min_area": 3,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "dragon_arc": {
        "workspace": "Dragon-Arc",
        "config": {
            "target_field": "Dragon-Arc", "program_id": "3843",
            "center_ra": 167.633, "center_dec": 64.990,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 1.5, "min_area": 5,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "elgordo": {
        "workspace": "El-Gordo",
        "config": {
            "target_field": "El-Gordo", "program_id": "1176",
            "center_ra": 15.7208, "center_dec": -49.2553,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 1.5, "min_area": 5,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "jades_goods_s": {
        "workspace": "JADES-GOODS-S",
        "config": {
            "target_field": "JADES-GOODS-S", "program_id": "1210",
            "center_ra": 53.126, "center_dec": -27.802,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F335M", "F356W", "F410M", "F444W"],
            "detection_threshold": 2.0, "min_area": 5,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "macs0417": {
        "workspace": "MACS-J0417",
        "config": {
            "target_field": "MACS-J0417", "program_id": "1208",
            "center_ra": 64.394, "center_dec": -11.909,
            "filters": ["F090W", "F115W", "F140M", "F150W", "F182M", "F200W", "F210M",
                         "F250M", "F277W", "F300M", "F335M", "F356W", "F360M", "F410M", "F444W"],
            "detection_threshold": 2.0, "min_area": 5,
            "crossmatch_radius_arcsec": 2.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "macs_j0329": {
        "workspace": "MACS-J0329",
        "config": {
            "target_field": "MACS-J0329", "program_id": "6882",
            "center_ra": 52.423, "center_dec": -2.196,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 1.5, "min_area": 5,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "macs0416": {
        "workspace": "MACS-J0416",
        "config": {
            "target_field": "MACS-J0416", "program_id": "1208",
            "center_ra": 64.039, "center_dec": -24.067,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 0.8, "min_area": 3,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "macs0647": {
        "workspace": "MACS-J0647",
        "config": {
            "target_field": "MACS-J0647", "program_id": "1433",
            "center_ra": 101.9583, "center_dec": 70.2500,
            "filters": ["F115W", "F150W", "F200W", "F277W", "F356W", "F444W", "F480M"],
            "detection_threshold": 0.8, "min_area": 3,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "macs1149": {
        "workspace": "MACS-J1149",
        "config": {
            "target_field": "MACS-J1149", "program_id": "1208",
            "center_ra": 177.399, "center_dec": 22.399,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 0.8, "min_area": 3,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "macs1423": {
        "workspace": "MACS-J1423",
        "config": {
            "target_field": "MACS-J1423", "program_id": "1208",
            "center_ra": 215.949, "center_dec": 24.078,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 0.8, "min_area": 3,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "ms0451": {
        "workspace": "MS-0451",
        "config": {
            "target_field": "MS-0451", "program_id": "6882",
            "center_ra": 73.545, "center_dec": -3.014,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 1.5, "min_area": 5,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "ngdeep": {
        "workspace": "NGDEEP",
        "config": {
            "target_field": "NGDEEP", "program_id": "2079",
            "center_ra": 53.1625, "center_dec": -27.7914,
            "filters": ["F115W", "F150W", "F200W", "F277W", "F356W", "F444W"],
            "detection_threshold": 1.5, "min_area": 5,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "pearls_nep": {
        "workspace": "PEARLS-NEP",
        "config": {
            "target_field": "PEARLS-NEP", "program_id": "2738",
            "center_ra": 261.470, "center_dec": 66.380,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 1.5, "min_area": 5,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "plckg165": {
        "workspace": "PLCK-G165",
        "config": {
            "target_field": "PLCK-G165", "program_id": "1176",
            "center_ra": 171.8125, "center_dec": 42.4753,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 0.8, "min_area": 3,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "plckg191": {
        "workspace": "PLCK-G191",
        "config": {
            "target_field": "PLCK-G191", "program_id": "1176",
            "center_ra": 161.18, "center_dec": 33.85,
            "filters": ["F090W", "F150W", "F200W", "F277W", "F356W", "F444W"],
            "detection_threshold": 0.8, "min_area": 3,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "primer_uds": {
        "workspace": "PRIMER-UDS",
        "config": {
            "target_field": "PRIMER-UDS", "program_id": "1837",
            "center_ra": 34.400, "center_dec": -5.150,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 1.5, "min_area": 5,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "rxc_j0032": {
        "workspace": "RXC-J0032",
        "config": {
            "target_field": "RXC-J0032", "program_id": "6882",
            "center_ra": 8.040, "center_dec": 18.140,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 1.5, "min_area": 5,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "rxc_j0600": {
        "workspace": "RXC-J0600",
        "config": {
            "target_field": "RXC-J0600", "program_id": "6882",
            "center_ra": 90.064, "center_dec": -20.121,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 0.8, "min_area": 3,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "rxc_j2211": {
        "workspace": "RXC-J2211",
        "config": {
            "target_field": "RXC-J2211", "program_id": "6882",
            "center_ra": 332.940, "center_dec": -3.833,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 0.8, "min_area": 3,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "rxj2129": {
        "workspace": "RXJ-2129",
        "config": {
            "target_field": "RXJ-2129", "program_id": "6882",
            "center_ra": 322.417, "center_dec": 0.089,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 0.8, "min_area": 3,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "smacs0723": {
        "workspace": "SMACS-0723",
        "config": {
            "target_field": "SMACS-0723", "program_id": "2736",
            "center_ra": 110.8375, "center_dec": -73.4531,
            "filters": ["F090W", "F150W", "F200W", "F277W", "F356W", "F444W"],
            "detection_threshold": 2.0, "min_area": 5,
            "crossmatch_radius_arcsec": 2.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
    "sunrise_arc": {
        "workspace": "Sunrise-Arc",
        "config": {
            "target_field": "Sunrise-Arc", "program_id": "2282",
            "center_ra": 24.3542, "center_dec": -8.4556,
            "filters": ["F090W", "F115W", "F150W", "F200W", "F277W", "F356W", "F410M", "F444W"],
            "detection_threshold": 0.8, "min_area": 3,
            "crossmatch_radius_arcsec": 3.0, "anomaly_contamination": 0.05, "top_candidates": 50,
        },
    },
}

# Default: characterize → crossmatch → anomaly → report
ALL_STAGES = ["characterize", "crossmatch", "anomaly", "report"]


def _fuzzy_match(name: str) -> str | None:
    name_lower = name.lower().replace("-", "").replace("_", "").replace(" ", "")
    for key in FIELDS:
        if name_lower in key.lower().replace("-", "").replace("_", ""):
            return key
        ws = FIELDS[key]["workspace"].lower().replace("-", "").replace("_", "")
        if name_lower in ws:
            return key
    return None


def run_field(field_key: str, stages: list[str]) -> dict:
    field = FIELDS[field_key]
    workspace = Path.home() / ".jwst-discovery" / "workspaces" / field["workspace"]
    config = field["config"]

    print(f"\n{'='*60}")
    print(f"FIELD: {config['target_field']}  ({len(stages)} stages: {' → '.join(stages)})")
    print(f"Workspace: {workspace}")
    print(f"{'='*60}")

    # Check prereqs: need photometry catalog for characterize, characterized for crossmatch
    if "characterize" in stages:
        prereq = workspace / "catalogs" / "photometry_catalog.parquet"
        if not prereq.exists():
            print("SKIP: No photometry_catalog.parquet — needs earlier pipeline stages")
            return {"status": "skip", "reason": "no photometry catalog"}
    elif "crossmatch" in stages:
        prereq = workspace / "catalogs" / "characterized_catalog.parquet"
        if not prereq.exists():
            print("SKIP: No characterized_catalog.parquet")
            return {"status": "skip", "reason": "no characterized catalog"}

    for d in ["catalogs", "report"]:
        (workspace / d).mkdir(parents=True, exist_ok=True)

    cancel = Event()
    field_start = time.time()
    result_info = {}

    for stage_name in stages:
        if stage_name not in STAGE_REGISTRY:
            print(f"SKIP: Stage '{stage_name}' not in registry")
            continue

        print(f"\n--- {stage_name} ---")
        stage_cls = STAGE_REGISTRY[stage_name]

        def progress(frac, msg):
            print(f"  [{frac:5.1%}] {msg}", flush=True)

        def emit_fn(filter_name, status, bytes_done=0, bytes_total=0):
            pass

        try:
            run_config = {**config, "enable_zcut_cutouts": False, "enable_hla_cutouts": False}
            stage = stage_cls(
                workspace=workspace,
                config=run_config,
                progress_callback=progress,
                cancel_event=cancel,
                emit_fn=emit_fn,
            )
            start = time.time()
            result = stage.run()
            elapsed = time.time() - start
            print(f"  DONE in {elapsed:.1f}s")
            for k, v in result.items():
                if isinstance(v, (str, int, float, bool)):
                    print(f"    {k}: {v}")
            result_info[stage_name] = result
        except Exception as e:
            print(f"  FAILED: {e}")
            traceback.print_exc()
            return {"status": "fail", "stage": stage_name, "error": str(e)}

    total = time.time() - field_start
    print(f"\n{config['target_field']} complete in {total:.0f}s")

    report = result_info.get("report", result_info.get("anomaly", {}))
    return {
        "status": "ok",
        "total_sources": report.get("total_sources", "?"),
        "novel_sources": report.get("novel_sources", "?"),
        "elapsed": f"{total:.0f}s",
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Re-run pipeline with fixed code on all 27 fields")
    parser.add_argument("field", nargs="?", default="all", help="Field name, 'all', or --list")
    parser.add_argument("--list", action="store_true", help="List available fields")
    parser.add_argument("--from-stage", default="characterize",
                        choices=["characterize", "crossmatch", "anomaly", "report"],
                        help="Start from this stage (default: characterize)")
    args = parser.parse_args()

    if args.list:
        print("Available fields (27):")
        for key, field in sorted(FIELDS.items()):
            ws = Path.home() / ".jwst-discovery" / "workspaces" / field["workspace"]
            phot = (ws / "catalogs" / "photometry_catalog.parquet").exists()
            char = (ws / "catalogs" / "characterized_catalog.parquet").exists()
            status = "READY" if phot else ("CHAR ONLY" if char else "NO DATA")
            print(f"  {key:18s} {field['workspace']:20s} {status}")
        return

    # Determine stages to run
    start_idx = ALL_STAGES.index(args.from_stage)
    stages = ALL_STAGES[start_idx:]

    # Determine fields to run
    if args.field == "all":
        fields_to_run = list(FIELDS.keys())
    else:
        matched = _fuzzy_match(args.field)
        if matched:
            fields_to_run = [matched]
        else:
            print(f"Unknown field: {args.field}")
            print(f"Available: {', '.join(sorted(FIELDS.keys()))}")
            sys.exit(1)

    print(f"Running stages: {' → '.join(stages)}")
    print(f"Fields: {len(fields_to_run)}")
    print()

    total_start = time.time()
    results = {}

    for i, field_key in enumerate(fields_to_run):
        print(f"\n[{i+1}/{len(fields_to_run)}]", end="")
        results[field_key] = run_field(field_key, stages)

    # Summary
    total_elapsed = time.time() - total_start
    print(f"\n{'='*70}")
    print(f"SUMMARY — {total_elapsed:.0f}s total ({total_elapsed/60:.1f} min)")
    print(f"{'='*70}")
    print(f"{'Field':20s} {'Status':8s} {'Sources':>8s} {'Novel':>8s} {'Time':>8s}")
    print("-" * 60)

    n_ok = 0
    n_fail = 0
    n_skip = 0
    total_sources = 0
    total_novel = 0

    for field_key in fields_to_run:
        info = results[field_key]
        ws_name = FIELDS[field_key]["workspace"]
        status = info.get("status", "?")
        sources = str(info.get("total_sources", ""))
        novel = str(info.get("novel_sources", ""))
        elapsed = info.get("elapsed", "")

        if status == "ok":
            n_ok += 1
            try:
                total_sources += int(info.get("total_sources", 0))
                total_novel += int(info.get("novel_sources", 0))
            except (ValueError, TypeError):
                pass
        elif status == "skip":
            n_skip += 1
        else:
            n_fail += 1

        marker = "" if status == "ok" else f" ({info.get('reason', info.get('error', ''))})"
        print(f"  {ws_name:20s} {status:8s} {sources:>8s} {novel:>8s} {elapsed:>8s}{marker}")

    print("-" * 60)
    print(f"  OK: {n_ok}, Skip: {n_skip}, Fail: {n_fail}")
    if total_sources > 0:
        novel_pct = 100 * total_novel / total_sources
        print(f"  Total: {total_sources:,} sources, {total_novel:,} novel ({novel_pct:.2f}%)")


if __name__ == "__main__":
    main()
