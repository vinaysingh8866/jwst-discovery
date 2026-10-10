import { create } from "zustand";

export interface FilterInfo {
  filter: string;
  filename: string;
  size_bytes: number;
  size_mb: number;
}

export interface ProgramInfo {
  program_id: string;
  observations: number;
  instruments: string[];
  targets: string[];
  filters: FilterInfo[];
  total_size_mb: number;
  mosaic_count: number;
  has_level3?: boolean;
}

export interface MastQueryResult {
  observations: number;
  programs: ProgramInfo[];
  totalSizeGb: number;
  target: string;
  cached: boolean;
  cachedAt: string | null;
}

export interface DownloadSelection {
  programId: string;
  filters: string[];
  forceRedownload: boolean;
}

interface DataStore {
  queryResult: MastQueryResult | null;
  // key = "programId:filter"
  selectedItems: Set<string>;
  downloadedFiles: Record<string, { size_mb: number; downloaded_at: string | null }>;
  // Pending download to be picked up by the Dashboard
  pendingDownload: DownloadSelection[] | null;

  setQueryResult: (result: MastQueryResult | null) => void;
  setSelectedItems: (items: Set<string>) => void;
  toggleItem: (key: string) => void;
  selectAllInProgram: (programId: string) => void;
  clearProgram: (programId: string) => void;
  setDownloadedFiles: (files: Record<string, { size_mb: number; downloaded_at: string | null }>) => void;
  setPendingDownload: (selections: DownloadSelection[] | null) => void;
  consumePendingDownload: () => DownloadSelection[] | null;
}

export const useDataStore = create<DataStore>((set, get) => ({
  queryResult: null,
  selectedItems: new Set<string>(),
  downloadedFiles: {},
  pendingDownload: null,

  setQueryResult: (result) => set({ queryResult: result }),

  setSelectedItems: (items) => set({ selectedItems: items }),

  toggleItem: (key) =>
    set((state) => {
      const next = new Set(state.selectedItems);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return { selectedItems: next };
    }),

  selectAllInProgram: (programId) =>
    set((state) => {
      const prog = state.queryResult?.programs.find((p) => p.program_id === programId);
      if (!prog) return state;
      const next = new Set(state.selectedItems);
      prog.filters.forEach((f) => next.add(`${programId}:${f.filter}`));
      return { selectedItems: next };
    }),

  clearProgram: (programId) =>
    set((state) => {
      const next = new Set(state.selectedItems);
      for (const key of state.selectedItems) {
        if (key.startsWith(`${programId}:`)) next.delete(key);
      }
      return { selectedItems: next };
    }),

  setDownloadedFiles: (files) => set({ downloadedFiles: files }),

  setPendingDownload: (selections) => set({ pendingDownload: selections }),

  consumePendingDownload: () => {
    const pending = get().pendingDownload;
    set({ pendingDownload: null });
    return pending;
  },
}));
