"""Stage 1: Download JWST data from MAST.

Downloads Level 3 combined mosaic images (i2d) for the target field,
one per filter. Uses parallel direct HTTP downloads for speed.
Emits per-filter progress events for the UI.
Writes a downloads.json manifest for tracking what has been downloaded.
"""
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from astroquery.mast import Observations

from jwst_pipeline.stages.base import PipelineStage

logger = logging.getLogger(__name__)

MAST_DOWNLOAD_URL = "https://mast.stsci.edu/api/v0.1/Download/file"


def _get_data_uri(product_row) -> str:
    return f"mast:JWST/product/{product_row['productFilename']}"


def _download_file(url: str, dest: Path, chunk_size: int = 1024 * 1024,
                   progress_fn=None, cancel_event=None) -> Path:
    req = Request(url)
    req.add_header("User-Agent", "jwst-discovery-pipeline/0.1")

    with urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0

        tmp = dest.with_suffix(".part")
        with open(tmp, "wb") as f:
            while True:
                if cancel_event and cancel_event.is_set():
                    tmp.unlink(missing_ok=True)
                    raise InterruptedError("Download cancelled")

                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)

                if progress_fn and total > 0:
                    progress_fn(downloaded, total)

        tmp.rename(dest)
    return dest


class IngestStage(PipelineStage):
    name = "ingest"

    def run(self) -> dict:
        raw_dir = self.workspace / "raw"
        cfg = self.config
        emit_fn = self.emit  # set by __main__.py

        target_field = cfg.get("target_field", "Abell-2744")
        program_id = str(cfg.get("program_id", "2561"))
        filters = cfg.get("filters", ["F115W", "F150W", "F200W", "F277W", "F356W", "F444W"])
        max_workers = int(cfg.get("download_threads", 4))
        force_redownload = cfg.get("force_redownload", False)

        self.progress(0.0, f"Querying MAST for program {program_id}...")
        self.check_cancel()

        obs = Observations.query_criteria(
            proposal_id=[program_id],
            obs_collection="JWST",
            instrument_name="NIRCAM/IMAGE",
            dataproduct_type="image",
        )

        if len(obs) == 0:
            self.progress(1.0, "No observations found")
            return {"files_downloaded": 0, "observations_found": 0}

        logger.info("Found %d observations for program %s", len(obs), program_id)
        self.progress(0.1, f"Found {len(obs)} observations, fetching products...")
        self.check_cancel()

        products = Observations.get_product_list(obs)
        science = Observations.filter_products(
            products, productType="SCIENCE", extension="fits"
        )

        self.progress(0.15, "Selecting best mosaic per filter...")
        self.check_cancel()

        detector_pattern = re.compile(r"_nrc[ab](?:[1-4]|long)_")
        filter_pattern = re.compile(r"clear-([a-z]\d+[a-z])", re.IGNORECASE)

        selected = {}
        for row in science:
            fname = str(row["productFilename"])
            if "_i2d.fits" not in fname or detector_pattern.search(fname):
                continue
            m = filter_pattern.search(fname)
            if not m:
                continue
            filt = m.group(1).upper()
            if filt not in filters:
                continue
            size = int(row["size"]) if row["size"] else 0
            if filt not in selected or size < int(selected[filt]["size"]):
                selected[filt] = row

        if not selected:
            self.progress(1.0, "No matching mosaic products found")
            return {"files_downloaded": 0, "observations_found": len(obs)}

        total_size_mb = sum(int(r["size"]) for r in selected.values()) / 1e6

        self.progress(0.2, f"Downloading {len(selected)} mosaics ({total_size_mb:.0f} MB)...")
        self.check_cancel()

        # Build download tasks
        use_cloud = cfg.get("use_cloud_downloads", False)
        tasks = []
        for filt in sorted(selected.keys()):
            row = selected[filt]
            fname = str(row["productFilename"])
            url = f"{MAST_DOWNLOAD_URL}?uri={_get_data_uri(row)}"

            # Try cloud URL if enabled
            if use_cloud:
                cloud_url = self._resolve_cloud_url(row)
                if cloud_url:
                    url = cloud_url
                    logger.info("Using cloud URL for %s: %s", filt, url)

            dest = raw_dir / fname
            size = int(row["size"]) if row["size"] else 0
            tasks.append({"filter": filt, "filename": fname, "url": url, "dest": dest, "size": size})

        # Skip already-downloaded files (unless force_redownload)
        to_download = []
        already_done = []
        for t in tasks:
            if not force_redownload and t["dest"].exists() and t["dest"].stat().st_size > 0:
                actual = t["dest"].stat().st_size
                expected = t["size"]
                if expected == 0 or abs(actual - expected) < expected * 0.01:
                    logger.info("Skipping %s (cached, %.1f MB)", t["filter"], actual / 1e6)
                    already_done.append(t)
                    emit_fn(t["filter"], "completed", actual, expected)
                    continue
            if force_redownload and t["dest"].exists():
                t["dest"].unlink(missing_ok=True)
            to_download.append(t)

        # Emit pending status for files to download
        for t in to_download:
            emit_fn(t["filter"], "pending", 0, t["size"])

        # Track global progress
        lock = threading.Lock()
        progress_state = {
            "bytes_done": sum(t["dest"].stat().st_size for t in already_done if t["dest"].exists()),
            "total_bytes": sum(t["size"] for t in tasks),
        }
        downloaded_files = list(already_done)

        def make_progress_fn(filt, total_size):
            last_reported = [0]
            last_emit = [0]
            def fn(downloaded, total):
                with lock:
                    delta = downloaded - last_reported[0]
                    last_reported[0] = downloaded
                    progress_state["bytes_done"] += delta
                    total_bytes = progress_state["total_bytes"]
                    if total_bytes > 0:
                        frac = 0.2 + 0.75 * (progress_state["bytes_done"] / total_bytes)
                        mb_done = progress_state["bytes_done"] / 1e6
                        mb_total = total_bytes / 1e6
                        self.progress(
                            min(frac, 0.95),
                            f"Downloading... {mb_done:.0f}/{mb_total:.0f} MB"
                        )
                # Emit per-filter progress (throttle to every 2MB to avoid flooding)
                if downloaded - last_emit[0] > 2 * 1024 * 1024 or downloaded >= total:
                    last_emit[0] = downloaded
                    emit_fn(filt, "downloading", downloaded, total)
            return fn

        def download_one(task):
            filt = task["filter"]
            logger.info("Starting download: %s (%.0f MB)", filt, task["size"] / 1e6)
            emit_fn(filt, "downloading", 0, task["size"])
            try:
                _download_file(
                    task["url"], task["dest"],
                    progress_fn=make_progress_fn(filt, task["size"]),
                    cancel_event=self.cancel,
                )
                actual_size = task["dest"].stat().st_size
                logger.info("Completed: %s (%.1f MB)", filt, actual_size / 1e6)
                emit_fn(filt, "completed", actual_size, task["size"])
                return {**task, "success": True, "actual_size": actual_size}
            except Exception as e:
                logger.error("Failed to download %s: %s", filt, e)
                task["dest"].unlink(missing_ok=True)
                task["dest"].with_suffix(".part").unlink(missing_ok=True)
                emit_fn(filt, "failed", 0, task["size"])
                return {**task, "success": False, "error": str(e)}

        if to_download:
            workers = min(max_workers, len(to_download))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(download_one, t): t for t in to_download}
                for future in as_completed(futures):
                    result = future.result()
                    if result["success"]:
                        downloaded_files.append(result)

        self.progress(0.95, "Verifying...")

        verified = []
        for f in downloaded_files:
            dest = f["dest"] if isinstance(f["dest"], Path) else Path(f["dest"])
            if dest.exists() and dest.stat().st_size > 0:
                verified.append({
                    "path": str(dest), "filter": f["filter"],
                    "filename": dest.name, "size_mb": round(dest.stat().st_size / 1e6, 1),
                })

        missing_filters = set(filters) - {f["filter"] for f in verified}
        total_downloaded = sum(f["size_mb"] for f in verified)
        self.progress(0.98, f"Downloaded {len(verified)} files ({total_downloaded:.0f} MB)")

        # Write/update downloads manifest
        manifest_path = self.workspace / "downloads.json"
        manifest = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
            except (json.JSONDecodeError, OSError):
                manifest = {}

        now_iso = datetime.now(UTC).isoformat()
        for f in verified:
            key = f"{program_id}:{f['filter']}"
            manifest[key] = {
                "program_id": program_id,
                "filter": f["filter"],
                "filename": f["filename"],
                "path": f["path"],
                "size_mb": f["size_mb"],
                "downloaded_at": now_iso,
                "target": target_field,
            }

        try:
            manifest_path.write_text(json.dumps(manifest, indent=2))
            logger.info("Updated download manifest: %d entries", len(manifest))
        except OSError as exc:
            logger.warning("Failed to write manifest: %s", exc)

        self.progress(1.0, f"Downloaded {len(verified)} files ({total_downloaded:.0f} MB)")

        return {
            "files_downloaded": len(verified), "observations_found": len(obs),
            "total_size_mb": round(total_downloaded, 1), "files": verified,
            "missing_filters": list(missing_filters), "target": target_field,
            "program_id": program_id, "cached": len(already_done),
        }

    @staticmethod
    def _resolve_cloud_url(product_row) -> str | None:
        """Try to resolve an S3 cloud URI and convert to HTTPS.

        Returns an HTTPS URL or None on failure.
        """
        try:
            Observations.enable_cloud_dataset()
            from astropy.table import Table
            product_table = Table([product_row])
            uris = Observations.get_cloud_uris(product_table)
            if uris and len(uris) > 0:
                s3_uri = str(uris[0])
                if s3_uri.startswith("s3://stpubdata/"):
                    return s3_uri.replace("s3://stpubdata/", "https://stpubdata.stsci.edu/", 1)
                elif s3_uri.startswith("s3://"):
                    # Other S3 buckets — not directly convertible
                    return None
                return s3_uri
        except Exception as e:
            logger.debug("Cloud URL resolution failed: %s", e)
        return None
