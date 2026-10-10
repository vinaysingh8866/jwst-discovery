"""End-to-end: preprocess -> detect -> measure -> characterize on a synthetic field.

Uses the ``synthetic_workspace`` fixture (three i2d mosaics with 15 injected
Gaussian point sources of known position and flux). No network access.
"""
from __future__ import annotations

from threading import Event

import numpy as np
import polars as pl
import pytest

from jwst_pipeline.stages import STAGE_REGISTRY

MATCH_RADIUS_PIX = 1.5


def _run(name: str, ws, cancel: Event | None = None) -> dict:
    stage = STAGE_REGISTRY[name](
        workspace=ws.path,
        config=ws.config,
        progress_callback=lambda *_: None,
        cancel_event=cancel or Event(),
    )
    return stage.run()


def _match(catalog: pl.DataFrame, x: float, y: float) -> tuple[int, float]:
    """Index and pixel distance of the catalog row nearest (x, y)."""
    d = np.hypot(catalog["x"].to_numpy() - x, catalog["y"].to_numpy() - y)
    i = int(np.argmin(d))
    return i, float(d[i])


@pytest.fixture
def preprocessed(synthetic_workspace):
    result = _run("preprocess", synthetic_workspace)
    return synthetic_workspace, result


@pytest.fixture
def detected(preprocessed):
    ws, _ = preprocessed
    return ws, _run("detect", ws)


@pytest.fixture
def measured(detected):
    ws, _ = detected
    return ws, _run("measure", ws)


def test_preprocess_builds_detection_image(preprocessed):
    ws, result = preprocessed
    assert result["images_processed"] == 3
    assert sorted(result["filters"]) == sorted(ws.filters)
    assert result["bkg_failures"] == []
    det = ws.path / "processed" / "detection.fits"
    assert det.is_file()
    for filt in ws.filters:
        assert (ws.path / "processed" / f"{filt}_bksub.fits").is_file()
        # Same-grid mode symlinks the reprojected image to the bksub image.
        assert (ws.path / "processed" / f"{filt}_reproj.fits").exists()


def test_detect_recovers_injected_sources(detected):
    ws, result = detected
    n_injected = len(ws.sources)
    cat_path = ws.path / "catalogs" / "detection_catalog.parquet"
    assert cat_path.is_file()
    assert (ws.path / "segmentation" / "segmentation_map.fits").is_file()

    cat = pl.read_parquet(cat_path)
    assert cat.height == result["sources_detected"]
    # Count tolerance: no more than 2 missed, no more than 3 spurious.
    assert n_injected - 2 <= cat.height <= n_injected + 3, cat.height

    recovered = sum(1 for s in ws.sources if _match(cat, s.x, s.y)[1] <= MATCH_RADIUS_PIX)
    assert recovered >= n_injected - 2

    bright = ws.brightest
    i, dist = _match(cat, bright.x, bright.y)
    assert dist <= 1.0, f"brightest source off by {dist:.2f} px"
    assert cat["flux"][i] == cat["flux"].max()
    assert cat["flags"][i] == 0

    ra, dec = cat["ra"][i], cat["dec"][i]
    assert abs(ra - 3.586) < 0.01 and abs(dec + 30.4) < 0.01


def test_measure_writes_multiband_photometry(measured):
    ws, result = measured
    phot_path = ws.path / "catalogs" / "photometry_catalog.parquet"
    assert phot_path.is_file()
    assert result["catalog_path"] == str(phot_path)
    assert sorted(result["filters"]) == sorted(ws.filters)

    phot = pl.read_parquet(phot_path)
    for filt in ws.filters:
        for col in ("flux", "fluxerr", "mag", "magerr", "sn"):
            assert f"{col}_{filt}" in phot.columns
    assert {"max_sn", "median_sn", "n_bands_sn5"} <= set(phot.columns)

    bright = ws.brightest
    i, _ = _match(phot, bright.x, bright.y)
    flux = phot["flux_F150W"][i]
    expected = bright.total_flux_f150w_jy
    # 0.32" aperture at 0.03"/px is ~10.7 px: essentially all of a sigma=1.5px Gaussian.
    assert np.isfinite(flux) and flux > 0
    assert abs(flux / expected - 1.0) < 0.3, (flux, expected)
    assert phot["n_bands_sn5"][i] == 3
    assert np.isfinite(phot["mag_F150W"][i]) and 15 < phot["mag_F150W"][i] < 35

    # Colours follow the injected per-filter scaling: F150W brightest, F444W faintest.
    assert phot["flux_F150W"][i] > phot["flux_F277W"][i] > phot["flux_F444W"][i]


def test_characterize_runs_without_photoz(measured):
    ws, _ = measured
    result = _run("characterize", ws)
    assert result["has_eazy_photoz"] is False
    char_path = ws.path / "catalogs" / "characterized_catalog.parquet"
    assert char_path.is_file()
    char = pl.read_parquet(char_path)
    assert char.height == pl.read_parquet(ws.path / "catalogs" / "photometry_catalog.parquet").height
    assert "object_class" in char.columns
    assert "color_F150W_F277W" in char.columns


def test_stage_honours_cancel_event(synthetic_workspace):
    from jwst_pipeline.stages.base import CancellationError

    cancel = Event()
    cancel.set()
    with pytest.raises(CancellationError):
        _run("preprocess", synthetic_workspace, cancel=cancel)


@pytest.mark.network
def test_characterize_with_eazy_photoz(measured):
    """Full EAZY photo-z path; fetches templates on first run."""
    ws, _ = measured
    stage = STAGE_REGISTRY["characterize"](
        workspace=ws.path,
        config={**ws.config, "enable_photoz": True},
        progress_callback=lambda *_: None,
        cancel_event=Event(),
    )
    result = stage.run()
    assert result["has_eazy_photoz"] is True
