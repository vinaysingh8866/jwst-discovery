import { useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { useConfigStore } from "@/stores/configStore";
import { useSetupStore, type KnownFieldInfo } from "@/stores/setupStore";
import { NIRCAM_FILTERS } from "@/lib/constants";

// Preset configs for known fields (program_id + target_field label)
const FIELD_PRESETS: Record<string, { target_field: string; program_id: string }> = {
  goods_south: { target_field: "JADES-GOODS-S", program_id: "1210" },
  goods_north: { target_field: "JADES-GOODS-N", program_id: "1181" },
  abell2744: { target_field: "Abell-2744", program_id: "2561" },
  macs0417: { target_field: "MACS-J0417", program_id: "1208" },
  smacs0723: { target_field: "SMACS-0723", program_id: "2736" },
};

export function ConfigPage() {
  const { config, updateConfig, resetToDefaults, isDirty } = useConfigStore();
  const knownFields = useSetupStore((s) => s.knownFields);
  const setKnownFields = useSetupStore((s) => s.setKnownFields);

  // Load known fields on mount
  useEffect(() => {
    if (!knownFields) {
      invoke("list_known_fields").catch(() => {});
    }
  }, [knownFields]);

  // Listen for known fields response
  useEffect(() => {
    const unlisten = listen<KnownFieldInfo[]>("field:known-fields", (event) => {
      setKnownFields(event.payload);
    });
    return () => { unlisten.then((fn) => fn()); };
  }, [setKnownFields]);

  const applyPreset = (field: KnownFieldInfo) => {
    const preset = FIELD_PRESETS[field.field] || {
      target_field: field.label,
      program_id: "",
    };
    updateConfig({
      target_field: preset.target_field,
      center_ra: field.ra_center,
      center_dec: field.dec_center,
      program_id: preset.program_id,
      filters: field.mosaic_filters.length > 0 ? field.mosaic_filters : config.filters,
    });
  };

  return (
    <div className="absolute inset-0 overflow-auto p-4">
    <div className="max-w-3xl">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-bold">Pipeline Configuration</h2>
        <div className="flex gap-2 items-center">
          {isDirty && (
            <span className="text-xs text-yellow-400">Unsaved changes</span>
          )}
          <button
            onClick={resetToDefaults}
            className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm transition-colors"
          >
            Reset Defaults
          </button>
        </div>
      </div>

      {/* Known Field Presets */}
      <section className="bg-gray-900 rounded-lg border border-gray-800 p-4 mb-4">
        <h3 className="text-sm font-medium mb-2">Known Field Presets</h3>
        <p className="text-xs text-gray-500 mb-3">
          Select a known JWST deep field to auto-fill coordinates, filters, and program ID.
        </p>
        <div className="flex flex-wrap gap-2">
          {knownFields ? (
            knownFields.map((field) => (
              <button
                key={field.field}
                onClick={() => applyPreset(field)}
                className="px-3 py-2 bg-gray-800 hover:bg-gray-700 border border-gray-700 hover:border-blue-500/50 rounded-lg text-sm transition-colors text-left"
              >
                <div className="font-medium text-gray-200">{field.label}</div>
                <div className="text-[10px] text-gray-500 mt-0.5">
                  {field.catalog_count} catalogs
                  {field.has_hlsp_mosaics && ` \u00b7 ${field.mosaic_filters.length} HLSP filters`}
                </div>
              </button>
            ))
          ) : (
            <span className="text-xs text-gray-600">Loading fields...</span>
          )}
        </div>
      </section>

      <section className="bg-gray-900 rounded-lg border border-gray-800 p-4 mb-4">
        <h3 className="text-sm font-medium mb-3">Target Field</h3>
        <div className="grid grid-cols-2 gap-4">
          <label className="block">
            <span className="text-xs text-gray-400">Field Name</span>
            <input
              type="text"
              value={config.target_field}
              onChange={(e) => updateConfig({ target_field: e.target.value })}
              className="mt-1 block w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="text-xs text-gray-400">Program ID</span>
            <input
              type="text"
              value={config.program_id}
              onChange={(e) => updateConfig({ program_id: e.target.value })}
              className="mt-1 block w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="text-xs text-gray-400">Center RA (deg)</span>
            <input
              type="number"
              step="0.001"
              value={config.center_ra}
              onChange={(e) => updateConfig({ center_ra: parseFloat(e.target.value) })}
              className="mt-1 block w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="text-xs text-gray-400">Center Dec (deg)</span>
            <input
              type="number"
              step="0.001"
              value={config.center_dec}
              onChange={(e) => updateConfig({ center_dec: parseFloat(e.target.value) })}
              className="mt-1 block w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
            />
          </label>
        </div>
      </section>

      <section className="bg-gray-900 rounded-lg border border-gray-800 p-4 mb-4">
        <h3 className="text-sm font-medium mb-3">NIRCam Filters</h3>
        <div className="flex flex-wrap gap-2">
          {NIRCAM_FILTERS.map((f) => {
            const active = config.filters.includes(f);
            return (
              <button
                key={f}
                onClick={() => {
                  const filters = active
                    ? config.filters.filter((x) => x !== f)
                    : [...config.filters, f];
                  updateConfig({ filters });
                }}
                className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                  active
                    ? "bg-blue-500/20 text-blue-400 border border-blue-500/50"
                    : "bg-gray-800 text-gray-500 border border-gray-700"
                }`}
              >
                {f}
              </button>
            );
          })}
        </div>
      </section>

      <section className="bg-gray-900 rounded-lg border border-gray-800 p-4 mb-4">
        <h3 className="text-sm font-medium mb-3">Source Detection</h3>
        <div className="grid grid-cols-2 gap-4">
          <label className="block">
            <span className="text-xs text-gray-400">Detection Threshold (sigma)</span>
            <input
              type="number"
              step="0.1"
              value={config.detection_threshold}
              onChange={(e) => updateConfig({ detection_threshold: parseFloat(e.target.value) })}
              className="mt-1 block w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="text-xs text-gray-400">Min Area (pixels)</span>
            <input
              type="number"
              value={config.min_area}
              onChange={(e) => updateConfig({ min_area: parseInt(e.target.value) })}
              className="mt-1 block w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
            />
          </label>
        </div>
      </section>

      <section className="bg-gray-900 rounded-lg border border-gray-800 p-4 mb-4">
        <h3 className="text-sm font-medium mb-3">Cross-Match</h3>
        <label className="block">
          <span className="text-xs text-gray-400">Match Radius (arcsec)</span>
          <input
            type="number"
            step="0.1"
            value={config.crossmatch_radius_arcsec}
            onChange={(e) => updateConfig({ crossmatch_radius_arcsec: parseFloat(e.target.value) })}
            className="mt-1 block w-48 bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
          />
        </label>
      </section>

      <section className="bg-gray-900 rounded-lg border border-gray-800 p-4 mb-4">
        <h3 className="text-sm font-medium mb-3">Anomaly Detection</h3>
        <div className="grid grid-cols-2 gap-4">
          <label className="block">
            <span className="text-xs text-gray-400">Contamination</span>
            <input
              type="number"
              step="0.01"
              min="0.01"
              max="0.5"
              value={config.anomaly_contamination}
              onChange={(e) => updateConfig({ anomaly_contamination: parseFloat(e.target.value) })}
              className="mt-1 block w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="text-xs text-gray-400">Top Candidates</span>
            <input
              type="number"
              value={config.top_candidates}
              onChange={(e) => updateConfig({ top_candidates: parseInt(e.target.value) })}
              className="mt-1 block w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
            />
          </label>
        </div>
      </section>

      <section className="bg-gray-900 rounded-lg border border-gray-800 p-4">
        <h3 className="text-sm font-medium mb-3">Advanced Processing</h3>
        <div className="space-y-3">
          <label className="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={config.enable_deblend}
              onChange={(e) => updateConfig({ enable_deblend: e.target.checked })}
              className="mt-0.5 rounded border-gray-600"
            />
            <div>
              <span className="text-sm text-gray-200">Enable Deblending</span>
              <p className="text-xs text-gray-500 mt-0.5">
                Use Scarlet2 PSF-aware deblending for blended sources. Slower but improves photometry in crowded fields. Requires scarlet2.
              </p>
            </div>
          </label>
          <label className="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={config.enable_cigale}
              onChange={(e) => updateConfig({ enable_cigale: e.target.checked })}
              className="mt-0.5 rounded border-gray-600"
            />
            <div>
              <span className="text-sm text-gray-200">Enable CIGALE SED Fitting</span>
              <p className="text-xs text-gray-500 mt-0.5">
                Run CIGALE on top candidates to estimate stellar mass, SFR, and dust attenuation. Requires pcigale.
              </p>
            </div>
          </label>
        </div>
      </section>
    </div>
    </div>
  );
}
