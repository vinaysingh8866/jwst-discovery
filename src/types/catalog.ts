export interface Source {
  source_id: number;
  ra: number;
  dec: number;
  x: number;
  y: number;
  flux: number;
  a: number;
  b: number;
  theta: number;
  fluxes?: Record<string, number>;
  fluxerrs?: Record<string, number>;
  z_phot?: number;
  z_phot_lo?: number;
  z_phot_hi?: number;
  p_star?: number;
  anomaly_score?: number;
  anomaly_rank?: number;
  novel?: boolean;
}

export interface ColumnFilter {
  column: string;
  operator: "gt" | "lt" | "eq" | "between";
  value: number | [number, number];
}

export interface CatalogPage {
  sources: Source[];
  total: number;
  offset: number;
  limit: number;
}

