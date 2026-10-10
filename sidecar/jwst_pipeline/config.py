"""Pipeline configuration."""
from dataclasses import dataclass, field


@dataclass
class PipelineConfig:
    # Target field
    target_field: str = "Abell-2744"
    center_ra: float = 3.586  # degrees
    center_dec: float = -30.400  # degrees
    field_size_arcmin: float = 5.0

    # MAST query
    program_id: str = "2561"  # UNCOVER
    filters: list[str] = field(default_factory=lambda: [
        "F115W", "F150W", "F200W", "F277W", "F356W", "F444W"
    ])
    product_type: str = "i2d"

    # Detection
    detection_threshold: float = 1.5
    deblend_nthresh: int = 32
    deblend_cont: float = 0.005
    min_area: int = 5

    # Photometry
    aperture_radii_arcsec: list[float] = field(default_factory=lambda: [0.16, 0.32, 0.5])

    # Cross-match
    crossmatch_radius_arcsec: float = 3.0
    # Catalog list is now managed by catalog_registry.py

    # Validation
    bright_star_proximity_arcsec: float = 5.0
    bright_star_mag_limit: float = 18.0
    artifact_ellipticity_threshold: float = 0.8
    artifact_size_threshold_pix: float = 1.5
    min_band_coverage_fraction: float = 0.5

    # Anomaly detection
    anomaly_contamination: float = 0.05
    anomaly_n_estimators: int = 200
    top_candidates: int = 50

    # Optional processing stages
    enable_deblend: bool = False
    enable_cigale: bool = False
    enable_photoz: bool = True  # EAZY photo-z in characterize (needs templates; may fetch from network)

    # Workspace
    workspace: str | None = None
