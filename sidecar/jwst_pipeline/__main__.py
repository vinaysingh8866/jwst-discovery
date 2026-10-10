"""
JWST Pipeline sidecar entry point.
Reads JSON commands from stdin, writes JSON responses to stdout.
All logging goes to stderr to keep stdout clean for the protocol.
"""
import argparse
import json
import logging
import sys
import threading
import time
import traceback
from datetime import UTC

from jwst_pipeline import __version__
from jwst_pipeline.catalog_registry import (
    get_catalogs_for_field,
    get_hlsp_mosaics_for_field,
    list_known_fields,
)
from jwst_pipeline.protocol import (
    format_error,
    format_filter_progress,
    format_progress,
    format_response,
    format_stage_complete,
    parse_request,
)
from jwst_pipeline.stages import STAGE_REGISTRY
from jwst_pipeline.utils.catalog_downloader import (
    check_hlsp_mosaics as _check_hlsp_mosaics,
)
from jwst_pipeline.utils.catalog_downloader import (
    check_reference_catalogs as _check_ref_catalogs,
)
from jwst_pipeline.utils.catalog_downloader import (
    download_hlsp_mosaic,
    download_mast_catalog,
    download_vizier_catalog,
)

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("jwst_pipeline")


def emit(obj: dict):
    """Write a JSON line to stdout (the protocol channel)."""
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def run_stage(stage_name: str, workspace: str, config: dict, request_id: str, cancel_event: threading.Event):
    """Run a single pipeline stage."""
    try:
        if stage_name not in STAGE_REGISTRY:
            emit(format_error(request_id, f"Unknown stage: {stage_name}", stage=stage_name))
            return

        stage_cls = STAGE_REGISTRY[stage_name]

        def progress_callback(fraction: float, message: str):
            emit(format_progress(request_id, stage_name, fraction, message))

        def emit_fn(filter_name: str, status: str, bytes_done: int = 0, bytes_total: int = 0):
            emit(format_filter_progress(request_id, filter_name, status, bytes_done, bytes_total))

        from pathlib import Path
        stage = stage_cls(
            workspace=Path(workspace),
            config=config,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            emit_fn=emit_fn,
        )

        start_time = time.time()
        result = stage.run()
        duration = time.time() - start_time

        emit(format_stage_complete(request_id, stage_name, result, duration))

    except Exception as e:
        tb = traceback.format_exc()
        logger.error("Stage %s failed: %s\n%s", stage_name, e, tb)
        emit(format_error(request_id, str(e), stage=stage_name, tb=tb))


def _get_cache_path(target_field: str, ra: float, dec: float):
    """Return the cache file path for a MAST query."""
    from pathlib import Path
    cache_dir = Path.home() / ".jwst-discovery"
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_target = target_field.replace("/", "_").replace(" ", "_")
    return cache_dir / f"mast_cache_{safe_target}_{ra}_{dec}.json"


def query_mast(config: dict, request_id: str, force_refresh: bool = False):
    """Query MAST for ALL available JWST data at target position.

    Uses only the observation table (~3-5s) instead of fetching all product
    details (~100-170s).  Filter names, instruments, and targets are parsed
    directly from observation metadata.  Actual product URIs and file sizes
    are resolved lazily at download time by the ingest stage.
    """
    try:
        from collections import defaultdict
        from datetime import datetime

        target_field = config.get("target_field", "Abell-2744")
        center_ra = float(config.get("center_ra", 3.586))
        center_dec = float(config.get("center_dec", -30.400))
        radius = float(config.get("field_size_arcmin", 5.0))

        cache_path = _get_cache_path(target_field, center_ra, center_dec)

        # Check cache first (unless force_refresh is requested)
        if not force_refresh and cache_path.exists():
            try:
                cached_data = json.loads(cache_path.read_text())
                logger.info("Returning cached MAST results from %s", cache_path)
                cached_data["cached"] = True
                cached_data["cached_at"] = cached_data.get("cached_at", None)
                emit(format_response(request_id, cached_data))
                return
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Cache file corrupt, re-querying: %s", exc)

        import astropy.units as u
        import numpy as np
        from astropy.coordinates import SkyCoord
        from astroquery.mast import Observations

        logger.info("Querying JWST data at %s (RA=%.3f Dec=%.3f r=%.1f')...",
                     target_field, center_ra, center_dec, radius)

        t0 = time.time()
        coord = SkyCoord(ra=center_ra, dec=center_dec, unit="deg")

        obs = Observations.query_criteria(
            coordinates=coord,
            radius=radius * u.arcmin,
            obs_collection="JWST",
            dataproduct_type="image",
        )

        query_time = time.time() - t0
        logger.info("Observation query returned %d rows in %.1fs", len(obs), query_time)

        if len(obs) == 0:
            emit(format_response(request_id, {
                "type": "QueryResult",
                "observations": 0,
                "programs": [],
                "target": target_field,
                "cached": False,
                "cached_at": None,
            }))
            return

        # Filter to imaging instruments (skip NIRSPEC, FGS, etc.)
        imaging_mask = np.array([
            any(inst in str(row["instrument_name"]) for inst in ("NIRCAM", "MIRI", "NIRISS"))
            for row in obs
        ])
        imaging_obs = obs[imaging_mask]
        logger.info("Found %d imaging observations (of %d total)", len(imaging_obs), len(obs))

        if len(imaging_obs) == 0:
            emit(format_response(request_id, {
                "type": "QueryResult",
                "observations": len(obs),
                "programs": [],
                "target": target_field,
                "cached": False,
                "cached_at": None,
            }))
            return

        # Parse clean filter name from JWST filter strings like "F115W;CLEAR", "CLEAR;F200W", "F770W"
        def parse_filter_name(filters_str):
            parts = str(filters_str).split(";")
            for part in parts:
                part = part.strip().upper()
                if part and part != "CLEAR":
                    return part
            return None

        # Build per-program summaries directly from observation table
        program_data = defaultdict(lambda: {
            "obs_count": 0,
            "instruments": set(),
            "targets": set(),
            "filters": set(),
            "calib_levels": set(),
        })

        for row in imaging_obs:
            pid = str(row["proposal_id"]).strip()
            pd = program_data[pid]
            pd["obs_count"] += 1
            pd["instruments"].add(str(row["instrument_name"]).strip())
            pd["targets"].add(str(row["target_name"]).strip())
            pd["calib_levels"].add(int(row["calib_level"]))
            filt = parse_filter_name(row["filters"])
            if filt:
                pd["filters"].add(filt)

        programs = []
        for pid in sorted(program_data.keys()):
            pd = program_data[pid]
            filters_sorted = sorted(pd["filters"])
            has_level3 = 3 in pd["calib_levels"]

            programs.append({
                "program_id": pid,
                "observations": pd["obs_count"],
                "instruments": sorted(pd["instruments"]),
                "targets": sorted(pd["targets"])[:5],
                "filters": [
                    {"filter": f, "filename": "", "size_bytes": 0, "size_mb": 0}
                    for f in filters_sorted
                ],
                "total_size_mb": 0,
                "mosaic_count": len(filters_sorted),
                "has_level3": has_level3,
            })

        # Sort: programs with Level 3 mosaics first, then by observation count
        programs.sort(key=lambda p: (-int(p.get("has_level3", False)), -p["observations"]))

        total_time = time.time() - t0
        logger.info("Built %d program summaries with %d total filters in %.1fs",
                     len(programs), sum(p["mosaic_count"] for p in programs), total_time)

        now_iso = datetime.now(UTC).isoformat()

        result_data = {
            "type": "QueryResult",
            "observations": len(obs),
            "programs": programs,
            "total_size_gb": 0,
            "target": target_field,
            "cached": False,
            "cached_at": now_iso,
        }

        # Save to cache
        try:
            cache_path.write_text(json.dumps(result_data, indent=2))
            logger.info("Saved MAST query cache to %s", cache_path)
        except OSError as exc:
            logger.warning("Failed to write cache file: %s", exc)

        emit(format_response(request_id, result_data))

    except Exception as e:
        tb = traceback.format_exc()
        logger.error("MAST query failed: %s\n%s", e, tb)
        emit(format_error(request_id, str(e), tb=tb))


def check_downloads(workspace: str, request_id: str):
    """Check which files are already downloaded in the workspace."""
    try:
        from pathlib import Path
        manifest_path = Path(workspace) / "downloads.json"
        raw_dir = Path(workspace) / "raw"

        downloads = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
                # Verify files still exist on disk
                for key, entry in manifest.items():
                    fpath = Path(entry.get("path", ""))
                    if fpath.exists() and fpath.stat().st_size > 0:
                        downloads[key] = {
                            **entry,
                            "size_mb": round(fpath.stat().st_size / 1e6, 1),
                            "exists": True,
                        }
                    else:
                        downloads[key] = {**entry, "exists": False}
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to read manifest: %s", exc)

        # Also scan raw/ for any FITS files not in the manifest
        if raw_dir.exists():
            for fits_file in raw_dir.glob("*.fits"):
                # Try to extract program_id and filter from filename
                import re
                pid_m = re.search(r"jw(\d{5})", fits_file.name)
                filt_m = re.search(r"clear-([a-z]\d+[a-z])", fits_file.name, re.IGNORECASE)
                if not filt_m:
                    filt_m = re.search(r"([a-z]\d+[a-z])[-_]", fits_file.name, re.IGNORECASE)
                if pid_m and filt_m:
                    pid = pid_m.group(1)
                    filt = filt_m.group(1).upper()
                    key = f"{int(pid)}:{filt}"
                    if key not in downloads:
                        downloads[key] = {
                            "program_id": str(int(pid)),
                            "filter": filt,
                            "filename": fits_file.name,
                            "path": str(fits_file),
                            "size_mb": round(fits_file.stat().st_size / 1e6, 1),
                            "downloaded_at": None,
                            "exists": True,
                        }

        emit(format_response(request_id, {
            "type": "DownloadManifest",
            "downloads": downloads,
            "workspace": workspace,
        }))

    except Exception as e:
        tb = traceback.format_exc()
        logger.error("Check downloads failed: %s\n%s", e, tb)
        emit(format_error(request_id, str(e), tb=tb))


def run_pipeline(workspace: str, config: dict, request_id: str, cancel_event: threading.Event):
    """Run all pipeline stages sequentially."""
    stage_order = [
        "ingest", "preprocess", "detect", "deblend", "measure",
        "characterize", "crossmatch", "anomaly", "report",
    ]

    for stage_name in stage_order:
        if cancel_event.is_set():
            emit(format_error(request_id, "Pipeline cancelled by user"))
            return

        run_stage(stage_name, workspace, config, request_id, cancel_event)

    emit(format_response(request_id, {
        "type": "PipelineComplete",
        "summary": {"stages_completed": len(stage_order)},
    }))


# The MAST Missions API returns HTTP 500 (which astroquery reports as "invalid
# input") or simply never answers for dense fields such as GOODS-S. Time-box
# each attempt, shrink the search once, and explain the failure in plain terms.
MISSIONS_TIMEOUT_S = 150
MISSIONS_ROW_LIMIT = 2000
MISSIONS_SELECT_COLS = [
    "fileSetName", "instrume", "exp_type", "program",
    "pi_name", "opticalElements", "date_obs", "duration", "calib_level",
]


class MissionsUnavailable(RuntimeError):
    """MAST Missions did not return a usable answer for this position."""


def _missions_query_with_fallback(MastMissions, coord, radius_arcmin: float, u):
    """Query MAST Missions, retrying once with a smaller radius and row limit."""
    missions = MastMissions(mission="jwst")
    try:
        missions._service_api_connection.TIMEOUT = MISSIONS_TIMEOUT_S
    except AttributeError:
        pass
    attempts = [(radius_arcmin, MISSIONS_ROW_LIMIT), (max(radius_arcmin / 2.0, 0.5), 500)]
    last_exc: Exception | None = None
    for attempt_radius, limit in attempts:
        t0 = time.time()
        try:
            res = missions.query_region(
                coord, radius=attempt_radius * u.arcmin, limit=limit, select_cols=MISSIONS_SELECT_COLS,
            )
            logger.info("MastMissions r=%.1f' limit=%d answered in %.0fs", attempt_radius, limit, time.time() - t0)
            return res
        except Exception as exc:  # InvalidQueryError (HTTP 400/500) or TimeoutError
            last_exc = exc
            logger.warning(
                "MastMissions r=%.1f' limit=%d failed after %.0fs: %s",
                attempt_radius, limit, time.time() - t0, str(exc)[:120],
            )
    raise MissionsUnavailable(
        "MAST Missions did not answer for this position (its server times out or returns an error "
        "on dense fields such as GOODS-S and Abell 2744). The main archive search above does not use "
        "this service. Try a smaller search radius, or retry later. "
        f"Last error: {str(last_exc)[:160]}"
    ) from last_exc


def query_mast_missions(config: dict, request_id: str):
    """Query MAST Missions API to discover all JWST observations at target position.

    Returns per-program summaries with PI, instruments, filters, total exposure.
    """
    try:
        from collections import defaultdict

        import astropy.units as u
        from astropy.coordinates import SkyCoord
        from astroquery.mast import MastMissions

        center_ra = float(config.get("center_ra", 3.586))
        center_dec = float(config.get("center_dec", -30.400))
        radius = float(config.get("field_size_arcmin", 5.0))

        logger.info("QueryMastMissions: RA=%.3f Dec=%.3f r=%.1f'", center_ra, center_dec, radius)

        t0 = time.time()
        coord = SkyCoord(ra=center_ra, dec=center_dec, unit="deg")
        results = _missions_query_with_fallback(MastMissions, coord, radius, u)

        total_obs = len(results) if results is not None else 0
        logger.info("MastMissions returned %d rows in %.1fs", total_obs, time.time() - t0)

        if total_obs == 0:
            emit(format_response(request_id, {
                "type": "MastMissionsResult",
                "total_observations": 0,
                "programs": [],
            }))
            return

        # Aggregate by program
        program_data = defaultdict(lambda: {
            "pi_name": "",
            "observations": 0,
            "instruments": set(),
            "filters": set(),
            "total_exposure_s": 0.0,
        })

        import numpy as np
        for row in results:
            pid = str(row["program"]).strip()
            pd = program_data[pid]
            pd["observations"] += 1
            pi = str(row["pi_name"]).strip() if row["pi_name"] else ""
            if pi and not pd["pi_name"]:
                pd["pi_name"] = pi
            inst = str(row["instrume"]).strip() if row["instrume"] else ""
            if inst:
                pd["instruments"].add(inst)
            opt = str(row["opticalElements"]).strip() if row["opticalElements"] else ""
            if opt:
                # Parse filter from opticalElements (e.g. "F200W;CLEAR")
                for part in opt.split(";"):
                    part = part.strip().upper()
                    if part and part != "CLEAR" and part != "OPAQUE":
                        pd["filters"].add(part)
            dur = row["duration"]
            if dur is not None and np.isfinite(float(dur)):
                pd["total_exposure_s"] += float(dur)

        programs = []
        for pid in sorted(program_data.keys()):
            pd = program_data[pid]
            programs.append({
                "program_id": pid,
                "pi_name": pd["pi_name"],
                "observations": pd["observations"],
                "instruments": sorted(pd["instruments"]),
                "filters": sorted(pd["filters"]),
                "total_exposure_hrs": round(pd["total_exposure_s"] / 3600.0, 2),
            })

        programs.sort(key=lambda p: -p["observations"])

        emit(format_response(request_id, {
            "type": "MastMissionsResult",
            "total_observations": total_obs,
            "programs": programs,
        }))

    except Exception as e:
        tb = traceback.format_exc()
        logger.error("QueryMastMissions failed: %s\n%s", e, tb)
        emit(format_error(request_id, str(e), tb=tb))


PROTOCOL_SUMMARY = """\
The sidecar speaks newline-delimited JSON (NDJSON) over stdin/stdout; all
logging goes to stderr. Each request is one line:

    {"id": "<request id>", "command": {"type": "<Type>", "params": {...}}}

and every response line is {"id": "<request id>", "payload": {"type": ..., ...}}.
Long-running commands stream Progress / Log / FilterProgress payloads and end
with StageComplete, a result payload, or Error.

Command types:
  Status                     -> Ack
  Shutdown                   -> Ack, then the process exits
  Cancel                     -> Ack; sets the cancel flag for the running command
  ListKnownFields            -> KnownFieldsList
  RunStage                   params: stage, workspace, config   -> Progress..., StageComplete | Error
  RunPipeline                params: workspace, config          -> per-stage StageComplete..., PipelineComplete
  QueryMAST                  params: config, force_refresh      -> QueryResult
  QueryMastMissions          params: config                     -> MastMissionsResult
  CheckDownloads             params: workspace                  -> DownloadManifest
  CheckReferenceCatalogs     params: field, workspace           -> ReferenceCatalogStatus
  DownloadReferenceCatalogs  params: field, force               -> CatalogDownloadProgress..., StageComplete
  DownloadHLSPMosaics        params: field, workspace, filters  -> FilterProgress..., StageComplete

Example:
  printf '%s\\n' '{"id":"1","command":{"type":"Status"}}' '{"id":"2","command":{"type":"Shutdown"}}' | jwst-pipeline
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Handle --help / --version; with no arguments the process enters the stdin loop."""
    parser = argparse.ArgumentParser(
        prog="jwst-pipeline",
        description="JWST discovery pipeline sidecar: NDJSON command loop over stdin/stdout.",
        epilog=PROTOCOL_SUMMARY,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"jwst-pipeline {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    parse_args(sys.argv[1:] if argv is None else argv)
    logger.info("JWST Pipeline sidecar started")
    if sys.stdin.isatty():
        logger.info("Reading NDJSON requests from the terminal; run with --help for the protocol")

    cancel_event = threading.Event()
    current_thread = None

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = parse_request(line)
        except Exception as e:
            emit(format_error(None, f"Failed to parse request: {e}"))
            continue

        request_id = request["id"]
        cmd = request["command"]
        cmd_type = cmd["type"]

        if cmd_type == "RunStage":
            params = cmd["params"]
            cancel_event.clear()
            current_thread = threading.Thread(
                target=run_stage,
                args=(params["stage"], params["workspace"], params.get("config", {}), request_id, cancel_event),
                daemon=True,
            )
            current_thread.start()

        elif cmd_type == "RunPipeline":
            params = cmd["params"]
            cancel_event.clear()
            current_thread = threading.Thread(
                target=run_pipeline,
                args=(params["workspace"], params.get("config", {}), request_id, cancel_event),
                daemon=True,
            )
            current_thread.start()

        elif cmd_type == "QueryMAST":
            params = cmd["params"]
            cancel_event.clear()
            current_thread = threading.Thread(
                target=query_mast,
                args=(params.get("config", {}), request_id, params.get("force_refresh", False)),
                daemon=True,
            )
            current_thread.start()

        elif cmd_type == "CheckDownloads":
            params = cmd["params"]
            threading.Thread(
                target=check_downloads,
                args=(params["workspace"], request_id),
                daemon=True,
            ).start()

        elif cmd_type == "ListKnownFields":
            fields = list_known_fields()
            emit(format_response(request_id, {"type": "KnownFieldsList", "fields": fields}))

        elif cmd_type == "CheckReferenceCatalogs":
            params = cmd["params"]
            field = params["field"]
            workspace = params.get("workspace", "")

            def _do_check_ref(fld, ws, rid):
                try:
                    catalogs = _check_ref_catalogs(fld)
                    mosaics = _check_hlsp_mosaics(fld, ws) if ws else []
                    emit(format_response(rid, {
                        "type": "ReferenceCatalogStatus",
                        "field": fld,
                        "catalogs": catalogs,
                        "mosaics": mosaics,
                    }))
                except Exception as e:
                    tb = traceback.format_exc()
                    logger.error("CheckReferenceCatalogs failed: %s\n%s", e, tb)
                    emit(format_error(rid, str(e), tb=tb))

            threading.Thread(
                target=_do_check_ref,
                args=(field, workspace, request_id),
                daemon=True,
            ).start()

        elif cmd_type == "DownloadReferenceCatalogs":
            params = cmd["params"]
            field = params["field"]
            force = params.get("force", False)
            cancel_event.clear()

            def _do_download_ref(fld, frc, rid, cancel_ev):
                try:
                    catalogs = get_catalogs_for_field(fld)
                    total = len(catalogs)
                    ok = 0
                    fail = 0
                    skip = 0

                    for i, cat in enumerate(catalogs):
                        if cancel_ev.is_set():
                            emit(format_error(rid, "Download cancelled by user"))
                            return

                        def progress_fn(msg, cat=cat):
                            emit(format_response(rid, {
                                "type": "CatalogDownloadProgress",
                                "catalog": cat["name"],
                                "status": "downloading",
                                "detail": msg,
                            }))

                        # Route to VizieR or MAST handler
                        if "tap_table" in cat:
                            result = download_vizier_catalog(cat, force=frc, progress_fn=progress_fn)
                        elif "mast_url" in cat:
                            result = download_mast_catalog(cat, force=frc, progress_fn=progress_fn)
                        else:
                            continue

                        if result["status"] == "ok":
                            ok += 1
                        elif result["status"] == "skip":
                            skip += 1
                        else:
                            fail += 1

                        emit(format_response(rid, {
                            "type": "CatalogDownloadProgress",
                            "catalog": cat["name"],
                            "status": result["status"],
                            "detail": f"{result.get('rows', 0)} rows" if result["status"] != "fail"
                                      else result.get("error", "unknown error"),
                        }))

                        # Overall progress
                        frac = (i + 1) / total
                        emit(format_progress(rid, "ref_catalogs", frac,
                                             f"{i+1}/{total} catalogs processed"))

                        # Rate limit VizieR queries
                        if "tap_table" in cat and result["status"] == "ok":
                            time.sleep(2.0)

                    emit(format_stage_complete(rid, "ref_catalogs", {
                        "downloaded": ok,
                        "skipped": skip,
                        "failed": fail,
                        "total": total,
                    }, 0))

                except Exception as e:
                    tb = traceback.format_exc()
                    logger.error("DownloadReferenceCatalogs failed: %s\n%s", e, tb)
                    emit(format_error(rid, str(e), stage="ref_catalogs", tb=tb))

            current_thread = threading.Thread(
                target=_do_download_ref,
                args=(field, force, request_id, cancel_event),
                daemon=True,
            )
            current_thread.start()

        elif cmd_type == "DownloadHLSPMosaics":
            params = cmd["params"]
            field = params["field"]
            workspace = params["workspace"]
            filter_list = params.get("filters")
            force = params.get("force", False)
            cancel_event.clear()

            def _do_download_hlsp(fld, ws, filts, frc, rid, cancel_ev):
                try:
                    from pathlib import Path
                    hlsp = get_hlsp_mosaics_for_field(fld)
                    if not hlsp:
                        emit(format_error(rid, f"No HLSP mosaics defined for field: {fld}"))
                        return

                    raw_dir = Path(ws) / "raw"
                    raw_dir.mkdir(parents=True, exist_ok=True)

                    filters = filts or hlsp["filters"]
                    total = len(filters)

                    if hlsp["source"] == "direct_url":
                        base_url = hlsp["base_url"]
                        template = hlsp["filename_template"]

                        for i, filt in enumerate(filters):
                            if cancel_ev.is_set():
                                emit(format_error(rid, "Download cancelled by user"))
                                return

                            filename = template.format(filter_lower=filt.lower())
                            dest = raw_dir / filename
                            url = f"{base_url}/{filename}"

                            if dest.exists() and not frc:
                                emit(format_filter_progress(rid, filt, "done",
                                                            int(dest.stat().st_size),
                                                            int(dest.stat().st_size)))
                                continue

                            def _progress(fname, status, done, total_bytes):
                                emit(format_filter_progress(rid, fname, status, done, total_bytes))

                            result = download_hlsp_mosaic(
                                url, dest, filt,
                                progress_fn=_progress,
                                cancel_event=cancel_ev,
                            )
                            if result.get("status") != "ok":
                                logger.warning("HLSP mosaic %s: %s", filt, result.get("status"))

                            frac = (i + 1) / total
                            emit(format_progress(rid, "hlsp_mosaics", frac,
                                                 f"{i+1}/{total} filters downloaded"))

                    elif hlsp["source"] == "mast_query":
                        # For MAST-query sources, use the existing ingest flow
                        # by writing config that the ingest stage understands.
                        # The user can use the standard Data page MAST browser for these.
                        emit(format_error(rid,
                            "MAST-query mosaics should be downloaded via the Data page MAST browser. "
                            "Select the program and filters there."))
                        return

                    emit(format_stage_complete(rid, "hlsp_mosaics", {
                        "filters_downloaded": total,
                    }, 0))

                except Exception as e:
                    tb = traceback.format_exc()
                    logger.error("DownloadHLSPMosaics failed: %s\n%s", e, tb)
                    emit(format_error(rid, str(e), stage="hlsp_mosaics", tb=tb))

            current_thread = threading.Thread(
                target=_do_download_hlsp,
                args=(field, workspace, filter_list, force, request_id, cancel_event),
                daemon=True,
            )
            current_thread.start()

        elif cmd_type == "QueryMastMissions":
            params = cmd["params"]
            cancel_event.clear()
            current_thread = threading.Thread(
                target=query_mast_missions,
                args=(params.get("config", {}), request_id),
                daemon=True,
            )
            current_thread.start()

        elif cmd_type == "Cancel":
            cancel_event.set()
            emit(format_response(request_id, {"type": "Ack", "command": "Cancel"}))

        elif cmd_type == "Status":
            emit(format_response(request_id, {"type": "Ack", "command": "Status"}))

        elif cmd_type == "Shutdown":
            emit(format_response(request_id, {"type": "Ack", "command": "Shutdown"}))
            logger.info("Shutdown requested, exiting")
            sys.exit(0)

        else:
            emit(format_error(request_id, f"Unknown command type: {cmd_type}"))

    logger.info("stdin closed, exiting")


if __name__ == "__main__":
    main()
