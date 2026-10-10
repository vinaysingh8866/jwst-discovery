declare module "aladin-lite" {
  interface AladinOptions {
    target?: string;
    fov?: number;
    survey?: string;
    showCooGrid?: boolean;
    showSimbadPointerControl?: boolean;
    cooFrame?: string;
    [key: string]: unknown;
  }

  interface CatalogOptions {
    name?: string;
    sourceSize?: number;
    color?: string;
    shape?: string;
    onClick?: string;
    [key: string]: unknown;
  }

  interface AladinSource {
    data?: Record<string, unknown>;
  }

  interface AladinCatalog {
    addSources: (sources: AladinSource[]) => void;
  }

  interface AladinInstance {
    addCatalog: (cat: AladinCatalog) => void;
    removeLayers: () => void;
    on: (event: string, callback: (object: AladinSource) => void) => void;
    setFov: (fov: number) => void;
    gotoRaDec: (ra: number, dec: number) => void;
  }

  interface AladinStatic {
    init: Promise<void>;
    aladin: (container: HTMLElement | string, options?: AladinOptions) => AladinInstance;
    catalog: (options?: CatalogOptions) => AladinCatalog;
    source: (ra: number, dec: number, data?: Record<string, unknown>) => AladinSource;
  }

  const A: AladinStatic;
  export default A;
}
