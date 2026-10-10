#!/usr/bin/env python3
"""Generate NIRCam PSF kernels using WebbPSF.

Requires: pip install webbpsf
Plus the WebbPSF data files (~1.5 GB): see https://webbpsf.readthedocs.io

Output: sidecar/data/psf_kernels/nircam_{filter}_psf.fits
"""
import sys
from pathlib import Path

FILTERS = [
    "F070W", "F090W", "F115W", "F140M", "F150W", "F162M", "F182M", "F200W",
    "F210M", "F250M", "F277W", "F300M", "F335M", "F356W", "F360M", "F410M",
    "F430M", "F444W", "F460M", "F480M",
]

OUT_DIR = Path(__file__).parent.parent / "sidecar" / "data" / "psf_kernels"


def main():
    try:
        import webbpsf
    except ImportError:
        print("webbpsf not installed. Run: pip install webbpsf", file=sys.stderr)
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    nc = webbpsf.NIRCam()

    for filt in FILTERS:
        out_path = OUT_DIR / f"nircam_{filt.lower()}_psf.fits"
        if out_path.exists():
            print(f"  {filt}: already exists, skipping")
            continue

        print(f"  {filt}: generating...", end="", flush=True)
        nc.filter = filt
        psf = nc.calc_psf(oversample=1, fov_pixels=51)
        psf["DET_SAMP"].writeto(out_path, overwrite=True)
        print(" done")

    print(f"\nPSF kernels written to {OUT_DIR}")


if __name__ == "__main__":
    main()
