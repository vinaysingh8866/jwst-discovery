"""PSF kernel loading and Gaussian fallback for Scarlet2 deblending."""
import logging
from functools import lru_cache
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# NIRCam PSF FWHM in arcseconds (from STScI NIRCam documentation)
# Duplicated from characterize.py to avoid circular imports.
PSF_FWHM_ARCSEC: dict[str, float] = {
    "F070W": 0.030, "F090W": 0.034, "F115W": 0.040, "F140M": 0.048,
    "F150W": 0.055, "F162M": 0.059, "F182M": 0.062, "F200W": 0.066,
    "F210M": 0.070, "F250M": 0.080, "F277W": 0.091, "F300M": 0.100,
    "F335M": 0.108, "F356W": 0.115, "F360M": 0.116, "F410M": 0.133,
    "F430M": 0.139, "F444W": 0.145, "F460M": 0.149, "F480M": 0.155,
}

# Directory for pre-computed PSF FITS kernels (WebbPSF-generated)
PSF_KERNEL_DIR = Path(__file__).parent.parent.parent / "data" / "psf_kernels"


def _make_gaussian_psf(fwhm_pix: float, size: int = 0) -> np.ndarray:
    """Create a normalized 2D Gaussian PSF kernel.

    Parameters
    ----------
    fwhm_pix : float
        FWHM of the Gaussian in pixels.
    size : int
        Kernel side length. If 0, auto-compute as 6*sigma rounded up to odd.

    Returns
    -------
    np.ndarray
        Normalized 2D Gaussian kernel (sums to 1).
    """
    sigma = fwhm_pix / 2.3548  # FWHM = 2*sqrt(2*ln2)*sigma
    if size <= 0:
        size = max(int(np.ceil(sigma * 6)) | 1, 5)  # at least 5, always odd
        if size % 2 == 0:
            size += 1
    center = size // 2
    y, x = np.mgrid[:size, :size]
    kernel = np.exp(-((x - center) ** 2 + (y - center) ** 2) / (2 * sigma ** 2))
    kernel /= kernel.sum()
    return kernel.astype(np.float32)


@lru_cache(maxsize=20)
def load_psf_kernel(filter_name: str, pixel_scale_arcsec: float) -> np.ndarray:
    """Load or generate a PSF kernel for a given filter.

    Looks for a pre-computed FITS PSF kernel first, then falls back to a
    synthetic Gaussian constructed from the known FWHM table.

    Parameters
    ----------
    filter_name : str
        NIRCam filter name, e.g. "F200W".
    pixel_scale_arcsec : float
        Pixel scale of the images in arcseconds/pixel.

    Returns
    -------
    np.ndarray
        2D normalized PSF kernel.
    """
    filt = filter_name.upper()

    # Try pre-computed FITS kernel
    fits_path = PSF_KERNEL_DIR / f"nircam_{filt.lower()}_psf.fits"
    if fits_path.exists():
        try:
            from astropy.io import fits as afits
            with afits.open(fits_path) as hdul:
                kernel = hdul[0].data.astype(np.float32)
            kernel /= kernel.sum()
            logger.info("Loaded pre-computed PSF for %s from %s", filt, fits_path.name)
            return kernel
        except Exception as e:
            logger.warning("Failed to load PSF FITS for %s: %s, falling back to Gaussian", filt, e)

    # Gaussian fallback
    fwhm_arcsec = PSF_FWHM_ARCSEC.get(filt, 0.10)  # default ~F300M
    fwhm_pix = fwhm_arcsec / pixel_scale_arcsec
    kernel = _make_gaussian_psf(fwhm_pix)
    logger.debug("Generated Gaussian PSF for %s: FWHM=%.3f\" = %.2f px, kernel %dx%d",
                 filt, fwhm_arcsec, fwhm_pix, kernel.shape[0], kernel.shape[1])
    return kernel
