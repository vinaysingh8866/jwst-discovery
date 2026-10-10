import { create } from "zustand";
import type { PipelineConfig } from "@/types/config";
import { DEFAULT_CONFIG } from "@/types/config";

interface ConfigStore {
  config: PipelineConfig;
  isDirty: boolean;

  updateConfig: (partial: Partial<PipelineConfig>) => void;
  resetToDefaults: () => void;
}

export const useConfigStore = create<ConfigStore>((set) => ({
  config: { ...DEFAULT_CONFIG },
  isDirty: false,

  updateConfig: (partial) =>
    set((state) => ({
      config: { ...state.config, ...partial },
      isDirty: true,
    })),

  resetToDefaults: () =>
    set({
      config: { ...DEFAULT_CONFIG },
      isDirty: false,
    }),
}));

