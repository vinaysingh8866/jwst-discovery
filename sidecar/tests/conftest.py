"""Shared fixtures: a tiny synthetic JWST workspace with known injected sources.

The fixture writes three Level-3 style ``*_i2d.fits`` mosaics (F150W, F277W,
F444W) into ``<ws>/raw`` using the same HDU layout the real pipeline reads:
an empty PrimaryHDU plus ``SCI`` / ``ERR`` / ``WHT`` image extensions with a
TAN WCS and the ``BUNIT`` / ``PIXAR_SR`` / ``PHOTMJSR`` keys of a NIRCam i2d.
All filters share one pixel grid so preprocess takes its same-grid path.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

IMAGE_SIZE = 256
PIXEL_SCALE_ARCSEC = 0.03
PIXEL_SCALE_DEG = PIXEL_SCALE_ARCSEC / 3600.0
PIXAR_SR = (PIXEL_SCALE_DEG * math.pi / 180.0) ** 2
CENTER_RA, CENTER_DEC = 3.586, -30.400
NOISE_SIGMA = 0.01  # MJy/sr per pixel
SKY_LEVEL = 0.05  # constant background so preprocess has something to remove
PSF_SIGMA_PIX = 1.5
# Per-filter flux scaling so sources have (mild) colours.
FILTER_SCALE = {"F150W": 1.0, "F277W": 0.8, "F444W": 0.6}


@dataclass(frozen=True)
class InjectedSource:
    x: float
    y: float
    amplitude: float  # peak surface brightness in F150W, MJy/sr

    @property
    def total_flux_f150w_jy(self) -> float:
        """Integrated flux of the 2-D Gaussian, converted MJy/sr*pix -> Jy."""
        return self.amplitude * 2.0 * math.pi * PSF_SIGMA_PIX**2 * PIXAR_SR * 1e6


@dataclass(frozen=True)
class SyntheticWorkspace:
    path: Path
    filters: tuple[str, ...]
    sources: tuple[InjectedSource, ...]

    @property
    def brightest(self) -> InjectedSource:
        return max(self.sources, key=lambda s: s.amplitude)

    @property
    def config(self) -> dict:
        return {
            "target_field": "SYNTHETIC",
            "filters": list(self.filters),
            # 3 sigma rather than the field default of 1.5: the synthetic noise is
            # white, so 1.5 sigma / 5 px picks up ~40 noise groups per 65k pixels.
            "detection_threshold": 3.0,
            "min_area": 5,
            "deblend_nthresh": 32,
            "deblend_cont": 0.005,
            "aperture_radii_arcsec": [0.16, 0.32, 0.5],
            # The LSB pass smooths with sigma=5px and picks up the wings of the
            # brightest injected sources; it is not what this test measures.
            "enable_lsb_detection": False,
            "enable_psf_matching": True,
            # Never reach for EAZY templates (network) in the default test run.
            "enable_photoz": False,
        }


def _make_wcs() -> WCS:
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [CENTER_RA, CENTER_DEC]
    w.wcs.crpix = [IMAGE_SIZE / 2.0 + 0.5, IMAGE_SIZE / 2.0 + 0.5]
    w.wcs.cdelt = [-PIXEL_SCALE_DEG, PIXEL_SCALE_DEG]
    return w


def _injected_sources(rng: np.random.Generator) -> tuple[InjectedSource, ...]:
    """15 point sources on a jittered 4x4 grid (one cell left empty)."""
    grid = [40.0, 98.0, 156.0, 214.0]
    cells = [(gx, gy) for gy in grid for gx in grid][:15]
    amps = np.geomspace(5.0, 0.3, num=len(cells))
    sources = []
    for (gx, gy), amp in zip(cells, amps, strict=True):
        jx, jy = rng.uniform(-6, 6, size=2)
        sources.append(InjectedSource(x=float(gx + jx), y=float(gy + jy), amplitude=float(amp)))
    return tuple(sources)


def _render(sources, scale: float, rng: np.random.Generator) -> np.ndarray:
    yy, xx = np.mgrid[0:IMAGE_SIZE, 0:IMAGE_SIZE].astype(np.float64)
    img = np.full((IMAGE_SIZE, IMAGE_SIZE), SKY_LEVEL, dtype=np.float64)
    for s in sources:
        r2 = (xx - s.x) ** 2 + (yy - s.y) ** 2
        img += s.amplitude * scale * np.exp(-r2 / (2.0 * PSF_SIGMA_PIX**2))
    img += rng.normal(0.0, NOISE_SIGMA, size=img.shape)
    return img.astype(np.float32)


def write_i2d(path: Path, filt: str, sci: np.ndarray, wcs: WCS) -> None:
    primary = fits.PrimaryHDU()
    primary.header["TELESCOP"] = "JWST"
    primary.header["INSTRUME"] = "NIRCAM"
    primary.header["FILTER"] = filt
    primary.header["PUPIL"] = "CLEAR"

    sci_hdr = wcs.to_header()
    sci_hdr["BUNIT"] = "MJy/sr"
    sci_hdr["PIXAR_SR"] = PIXAR_SR
    sci_hdr["PIXAR_A2"] = PIXEL_SCALE_ARCSEC**2
    sci_hdr["PHOTMJSR"] = 1.0
    sci_hdu = fits.ImageHDU(data=sci, header=sci_hdr, name="SCI")
    err_hdu = fits.ImageHDU(data=np.full_like(sci, NOISE_SIGMA), name="ERR")
    wht_hdu = fits.ImageHDU(data=np.full_like(sci, 1.0 / NOISE_SIGMA**2), name="WHT")
    fits.HDUList([primary, sci_hdu, err_hdu, wht_hdu]).writeto(path, overwrite=True)


@pytest.fixture
def synthetic_workspace(tmp_path: Path) -> SyntheticWorkspace:
    rng = np.random.default_rng(20261008)
    filters = tuple(FILTER_SCALE)
    sources = _injected_sources(rng)
    wcs = _make_wcs()
    ws = tmp_path / "ws"
    raw = ws / "raw"
    raw.mkdir(parents=True)
    for filt in filters:
        sci = _render(sources, FILTER_SCALE[filt], rng)
        # Standard Level-3 product naming: the stages parse the filter from "clear-f150w".
        write_i2d(raw / f"jw09999-o001_t001_nircam_clear-{filt.lower()}_i2d.fits", filt, sci, wcs)
    return SyntheticWorkspace(path=ws, filters=filters, sources=sources)
