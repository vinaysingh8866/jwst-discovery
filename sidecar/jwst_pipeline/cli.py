"""``jwst-field``: run the pipeline on a registered field from the command line.

This replaces the thirty near-identical ``run_pipeline_<field>.py`` scripts
the project started with. Everything field-specific lives in
:mod:`jwst_pipeline.fields`.

Examples::

    jwst-field list
    jwst-field download SMACS-0723
    jwst-field run SMACS-0723
    jwst-field run SMACS-0723 --stages crossmatch anomaly report
    jwst-field run SMACS-0723 --workspace-root /Volumes/data/jwst
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
import traceback
from pathlib import Path
from threading import Event

from jwst_pipeline.fields import DEFAULT_STAGES, FIELDS, get_field


def workspace_root() -> Path:
    """Where per-field workspaces live. Override with ``JWST_WORKSPACE_ROOT``."""
    env = os.environ.get("JWST_WORKSPACE_ROOT")
    return Path(env).expanduser() if env else Path.home() / ".jwst-discovery" / "workspaces"


def cmd_list(_args) -> int:
    width = max(len(k) for k in FIELDS)
    for key, f in FIELDS.items():
        cfg = f["config"]
        print(
            f"{key:<{width}}  PID {cfg.get('program_id', '-'):<5}  "
            f"{len(cfg['filters'])} filters  {f.get('description', '')}"
        )
    return 0


def cmd_download(args) -> int:
    """Generic MAST download: largest Level-3 i2d mosaic per filter.

    Some fields need a hand-written recipe (HLSP or DJA mosaics instead of
    MAST products); those are listed under ``download_script`` in the registry
    and this command tells you to run them instead.
    """
    field = get_field(args.field)
    cfg = field["config"]
    if field.get("download_script") and not args.force_mast:
        print(f"{field['key']} has a field-specific download recipe:")
        print(f"    python {field['download_script']}")
        print("Run that, or pass --force-mast to try the generic MAST download anyway.")
        return 2

    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from astroquery.mast import Observations

    raw_dir = workspace_root() / field["key"] / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    coord = SkyCoord(ra=cfg["center_ra"], dec=cfg["center_dec"], unit="deg")
    print(f"Querying MAST for program {cfg['program_id']} around {coord.to_string('hmsdms')}")
    obs = Observations.query_criteria(
        coordinates=coord,
        radius=args.radius_arcmin * u.arcmin,
        obs_collection="JWST",
        instrument_name="NIRCAM/IMAGE",
        proposal_id=cfg["program_id"],
    )
    if len(obs) == 0:
        print("No observations found.")
        return 1
    products = Observations.get_product_list(obs)
    per_detector = re.compile(r"_nrc[ab](?:[1-4]|long)_")
    best: dict[str, object] = {}
    for row in products:
        fname = str(row["productFilename"])
        if "_i2d.fits" not in fname or per_detector.search(fname):
            continue
        for filt in cfg["filters"]:
            if filt.lower() in fname.lower():
                size = int(row["size"]) if row["size"] else 0
                if filt not in best or size > int(best[filt]["size"]):
                    best[filt] = row
                break
    print(f"Selected {len(best)}/{len(cfg['filters'])} mosaics: {sorted(best)}")
    tmp = raw_dir / "_tmp"
    for filt in sorted(best):
        row = best[filt]
        fname = str(row["productFilename"])
        dest = raw_dir / fname
        if dest.exists() and dest.stat().st_size > 1000:
            print(f"  [{filt}] already present")
            continue
        print(f"  [{filt}] downloading {fname} ({int(row['size'] or 0) / 1e6:.0f} MB)")
        result = Observations.download_products(
            products[products["productFilename"] == fname][:1], download_dir=str(tmp)
        )
        for dl in result:
            src = Path(str(dl["Local Path"]))
            if src.exists():
                src.rename(raw_dir / src.name)
    if tmp.exists():
        shutil.rmtree(tmp)
    missing = [f for f in cfg["filters"] if not list(raw_dir.glob(f"*{f.lower()}*i2d.fits"))]
    print("Missing filters:", missing or "none")
    return 0


def cmd_run(args) -> int:
    from jwst_pipeline.stages import STAGE_REGISTRY

    field = get_field(args.field)
    cfg = dict(field["config"])
    stages = args.stages or field.get("stages") or DEFAULT_STAGES
    unknown = [s for s in stages if s not in STAGE_REGISTRY]
    if unknown:
        print(f"Unknown stage(s) {unknown}. Valid stages: {' '.join(STAGE_REGISTRY)}", file=sys.stderr)
        return 2
    root = Path(args.workspace_root).expanduser() if args.workspace_root else workspace_root()
    workspace = root / field["key"]

    fits_files = sorted((workspace / "raw").glob("*.fits"))
    print(f"Workspace: {workspace}")
    print(f"Raw FITS files: {len(fits_files)}")
    if not fits_files and "ingest" not in stages:
        print(f"No FITS files in {workspace / 'raw'}. Run `jwst-field download {field['key']}` first.")
        return 1

    cancel = Event()
    t0 = time.time()
    failed: list[str] = []
    for name in stages:
        print(f"\n=== {name} ===")
        stage = STAGE_REGISTRY[name](
            workspace=workspace,
            config=cfg,
            progress_callback=lambda frac, msg: print(f"  [{frac:5.1%}] {msg}", flush=True),
            cancel_event=cancel,
            emit_fn=lambda *a, **k: None,
        )
        t = time.time()
        try:
            result = stage.run()
        except Exception as exc:  # keep going so one stage can't hide the rest
            failed.append(name)
            print(f"  FAILED: {exc}")
            traceback.print_exc()
            if args.fail_fast:
                break
            continue
        print(f"  done in {time.time() - t:.1f}s")
        for k, v in result.items():
            if isinstance(v, (str, int, float, bool)) or (isinstance(v, (list, dict)) and len(v) <= 10):
                print(f"    {k}: {v}")
    print(f"\nPipeline finished in {time.time() - t0:.0f}s; failed stages: {failed or 'none'}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="jwst-field", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list registered fields").set_defaults(func=cmd_list)
    d = sub.add_parser("download", help="download Level-3 i2d mosaics from MAST")
    d.add_argument("field")
    d.add_argument("--radius-arcmin", type=float, default=10.0)
    d.add_argument("--force-mast", action="store_true", help="ignore the field-specific recipe")
    d.set_defaults(func=cmd_download)
    r = sub.add_parser("run", help="run pipeline stages on a downloaded field")
    r.add_argument("field")
    r.add_argument("--stages", nargs="+", help=f"stages to run (default per field, usually {' '.join(DEFAULT_STAGES)})")
    r.add_argument("--workspace-root", help="override JWST_WORKSPACE_ROOT / ~/.jwst-discovery/workspaces")
    r.add_argument("--fail-fast", action="store_true")
    r.set_defaults(func=cmd_run)
    args = p.parse_args(argv)
    try:
        return args.func(args)
    except KeyError as exc:
        print(exc.args[0], file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
