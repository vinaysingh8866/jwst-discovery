import { create } from "zustand";

export interface KnownFieldInfo {
  field: string;
  label: string;
  ra_center: number;
  dec_center: number;
  catalog_count: number;
  catalogs: { name: string; label: string }[];
  has_hlsp_mosaics: boolean;
  mosaic_filters: string[];
  mosaic_label: string | null;
  est_mosaic_size_gb: number;
}

export interface CatalogStatus {
  name: string;
  label: string;
  status: "downloaded" | "missing" | "corrupt" | "downloading" | "ok" | "skip" | "fail";
  rows: number;
  size_kb: number;
}

export interface MosaicStatus {
  filter: string;
  status: "downloaded" | "missing";
  size_mb: number;
}

interface SetupStore {
  knownFields: KnownFieldInfo[] | null;
  refCatalogStatus: CatalogStatus[] | null;
  mosaicStatus: MosaicStatus[] | null;
  catalogDownloading: boolean;
  mosaicDownloading: boolean;
  selectedField: string | null;

  setKnownFields: (fields: KnownFieldInfo[]) => void;
  setRefCatalogStatus: (catalogs: CatalogStatus[] | null) => void;
  setMosaicStatus: (mosaics: MosaicStatus[] | null) => void;
  updateCatalogStatus: (name: string, status: CatalogStatus["status"], detail?: string) => void;
  setCatalogDownloading: (v: boolean) => void;
  setMosaicDownloading: (v: boolean) => void;
  setSelectedField: (field: string | null) => void;
}

export const useSetupStore = create<SetupStore>((set) => ({
  knownFields: null,
  refCatalogStatus: null,
  mosaicStatus: null,
  catalogDownloading: false,
  mosaicDownloading: false,
  selectedField: null,

  setKnownFields: (fields) => set({ knownFields: fields }),

  setRefCatalogStatus: (catalogs) => set({ refCatalogStatus: catalogs }),

  setMosaicStatus: (mosaics) => set({ mosaicStatus: mosaics }),

  updateCatalogStatus: (name, status, _detail) =>
    set((state) => {
      if (!state.refCatalogStatus) return state;
      return {
        refCatalogStatus: state.refCatalogStatus.map((c) =>
          c.name === name ? { ...c, status } : c
        ),
      };
    }),

  setCatalogDownloading: (v) => set({ catalogDownloading: v }),

  setMosaicDownloading: (v) => set({ mosaicDownloading: v }),

  setSelectedField: (field) => set({ selectedField: field }),
}));
