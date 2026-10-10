export interface PipelineConfig {
  target_field: string;
  center_ra: number;
  center_dec: number;
  field_size_arcmin: number;
  program_id: string;
  filters: string[];
  product_type: string;
  detection_threshold: number;
  deblend_nthresh: number;
  deblend_cont: number;
  min_area: number;
  aperture_radii_arcsec: number[];
  crossmatch_radius_arcsec: number;
  crossmatch_catalogs: string[];
  anomaly_contamination: number;
  anomaly_n_estimators: number;
  top_candidates: number;
  enable_deblend: boolean;
  enable_cigale: boolean;
  workspace: string | null;
}

export const DEFAULT_CONFIG: PipelineConfig = {
  target_field: "JADES-GOODS-S",
  center_ra: 53.126,
  center_dec: -27.802,
  field_size_arcmin: 5.0,
  program_id: "1210",
  filters: [
    "F090W", "F115W", "F150W", "F200W",
    "F277W", "F335M", "F356W", "F410M", "F444W",
  ],
  product_type: "i2d",
  detection_threshold: 2.0,
  deblend_nthresh: 32,
  deblend_cont: 0.005,
  min_area: 5,
  aperture_radii_arcsec: [0.16, 0.32, 0.5],
  crossmatch_radius_arcsec: 2.0,
  crossmatch_catalogs: ["NED", "SDSS", "Gaia", "SIMBAD"],
  anomaly_contamination: 0.05,
  anomaly_n_estimators: 200,
  top_candidates: 50,
  enable_deblend: false,
  enable_cigale: false,
  workspace: null,
};

