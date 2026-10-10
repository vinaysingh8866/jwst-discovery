import { useState, useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { useConfigStore } from "@/stores/configStore";
import { useSetupStore, type CatalogStatus, type MosaicStatus } from "@/stores/setupStore";
import { formatSize } from "@/lib/formatters";

/**
 * Field Setup panel: detects a known field from the configured coordinates and
 * manages reference-catalog and HLSP-mosaic downloads for it.
 */
export function FieldSetupPanel({ workspace }: { workspace: string }) {
  const config = useConfigStore((s) => s.config);
  const refCatalogStatus = useSetupStore((s) => s.refCatalogStatus);
  const mosaicStatus = useSetupStore((s) => s.mosaicStatus);
  const catalogDownloading = useSetupStore((s) => s.catalogDownloading);
  const mosaicDownloading = useSetupStore((s) => s.mosaicDownloading);
  const setRefCatalogStatus = useSetupStore((s) => s.setRefCatalogStatus);
  const setMosaicStatus = useSetupStore((s) => s.setMosaicStatus);
  const setCatalogDownloading = useSetupStore((s) => s.setCatalogDownloading);
  const setMosaicDownloading = useSetupStore((s) => s.setMosaicDownloading);
  const updateCatalogStatus = useSetupStore((s) => s.updateCatalogStatus);
  const selectedField = useSetupStore((s) => s.selectedField);
  const setSelectedField = useSetupStore((s) => s.setSelectedField);

  const [collapsed, setCollapsed] = useState(false);
  const [checking, setChecking] = useState(false);
  const [overallProgress, setOverallProgress] = useState<{ fraction: number; message: string } | null>(null);

  // Detect field from current config
  useEffect(() => {
    // Simple field detection based on known coordinates
    const FIELDS: Record<string, { ra: number; dec: number; radius: number }> = {
      goods_south: { ra: 53.126, dec: -27.802, radius: 5 },
      goods_north: { ra: 189.229, dec: 62.238, radius: 5 },
      abell2744: { ra: 3.586, dec: -30.4, radius: 5 },
      macs0417: { ra: 64.394, dec: -11.909, radius: 5 },
      smacs0723: { ra: 110.838, dec: -73.453, radius: 5 },
    };
    let detected: string | null = null;
    for (const [name, info] of Object.entries(FIELDS)) {
      const dra = config.center_ra - info.ra;
      const ddec = config.center_dec - info.dec;
      if (Math.sqrt(dra * dra + ddec * ddec) < info.radius) {
        detected = name;
        break;
      }
    }
    setSelectedField(detected);
  }, [config.center_ra, config.center_dec, setSelectedField]);

  // Check reference catalogs when field changes
  useEffect(() => {
    if (selectedField && workspace) {
      setChecking(true);
      invoke("check_reference_catalogs", { field: selectedField, workspace })
        .catch(() => setChecking(false));
    } else {
      setRefCatalogStatus(null);
      setMosaicStatus(null);
    }
  }, [selectedField, workspace, setRefCatalogStatus, setMosaicStatus]);

  // Listen for catalog status
  useEffect(() => {
    const unlisten = listen<{ field: string; catalogs: CatalogStatus[]; mosaics: MosaicStatus[] }>(
      "catalog:ref-status",
      (event) => {
        setRefCatalogStatus(event.payload.catalogs);
        setMosaicStatus(event.payload.mosaics || []);
        setChecking(false);
      }
    );
    return () => { unlisten.then((fn) => fn()); };
  }, [setRefCatalogStatus, setMosaicStatus]);

  // Listen for per-catalog download progress
  useEffect(() => {
    const unlisten = listen<{ catalog: string; status: string; detail: string }>(
      "catalog:download-progress",
      (event) => {
        updateCatalogStatus(
          event.payload.catalog,
          event.payload.status as CatalogStatus["status"],
          event.payload.detail,
        );
      }
    );
    return () => { unlisten.then((fn) => fn()); };
  }, [updateCatalogStatus]);

  // Listen for overall progress (ref_catalogs stage)
  useEffect(() => {
    const unlisten = listen<{ stageName: string; status: string; progress: number; message: string }>(
      "pipeline:stage-update",
      (event) => {
        if (event.payload.stageName === "ref_catalogs") {
          if (event.payload.status === "completed") {
            setCatalogDownloading(false);
            setOverallProgress(null);
            // Refresh status
            if (selectedField && workspace) {
              invoke("check_reference_catalogs", { field: selectedField, workspace }).catch(() => {});
            }
          } else if (event.payload.status === "running") {
            setOverallProgress({ fraction: event.payload.progress, message: event.payload.message });
          } else if (event.payload.status === "failed") {
            setCatalogDownloading(false);
            setOverallProgress(null);
          }
        }
        if (event.payload.stageName === "hlsp_mosaics") {
          if (event.payload.status === "completed" || event.payload.status === "failed") {
            setMosaicDownloading(false);
            if (selectedField && workspace) {
              invoke("check_reference_catalogs", { field: selectedField, workspace }).catch(() => {});
            }
          }
        }
      }
    );
    return () => { unlisten.then((fn) => fn()); };
  }, [selectedField, workspace, setCatalogDownloading, setMosaicDownloading]);

  const handleDownloadCatalogs = (force: boolean) => {
    if (!selectedField) return;
    setCatalogDownloading(true);
    invoke("download_reference_catalogs", { field: selectedField, force })
      .catch(() => setCatalogDownloading(false));
  };

  const handleDownloadMosaics = () => {
    if (!selectedField || !workspace) return;
    setMosaicDownloading(true);
    invoke("download_hlsp_mosaics", { field: selectedField, workspace, filters: null, force: false })
      .catch(() => setMosaicDownloading(false));
  };

  if (!selectedField) {
    return (
      <div className="bg-gray-900 rounded-lg border border-gray-800 p-4 mb-4">
        <h3 className="text-sm font-medium text-gray-400">Field Setup</h3>
        <p className="text-xs text-gray-600 mt-1">
          Select a known field preset in Config to enable automatic catalog and mosaic downloads.
        </p>
      </div>
    );
  }

  const downloadedCatalogs = refCatalogStatus?.filter((c) => c.status === "downloaded").length ?? 0;
  const totalCatalogs = refCatalogStatus?.length ?? 0;
  const missingCatalogs = totalCatalogs - downloadedCatalogs;

  const downloadedMosaics = mosaicStatus?.filter((m) => m.status === "downloaded").length ?? 0;
  const totalMosaics = mosaicStatus?.length ?? 0;

  const FIELD_LABELS: Record<string, string> = {
    goods_south: "GOODS-South",
    goods_north: "GOODS-North",
    abell2744: "Abell 2744",
    macs0417: "MACS-J0417",
    smacs0723: "SMACS-0723",
  };

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 mb-4 overflow-hidden">
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="w-full flex items-center justify-between p-4 hover:bg-gray-800/50 transition-colors text-left"
      >
        <div className="flex items-center gap-3">
          <svg className={`w-3 h-3 text-gray-500 transition-transform ${collapsed ? "" : "rotate-90"}`} fill="currentColor" viewBox="0 0 20 20">
            <path d="M6 6l8 4-8 4V6z" />
          </svg>
          <h3 className="text-sm font-medium">Field Setup</h3>
          <span className="text-xs text-gray-500">{FIELD_LABELS[selectedField] || selectedField}</span>
        </div>
        <div className="flex items-center gap-3 text-xs">
          {checking ? (
            <span className="text-gray-500">Checking...</span>
          ) : refCatalogStatus && (
            <>
              <span className={downloadedCatalogs === totalCatalogs ? "text-green-400" : "text-yellow-400"}>
                {downloadedCatalogs}/{totalCatalogs} catalogs
              </span>
              {totalMosaics > 0 && (
                <span className={downloadedMosaics === totalMosaics ? "text-green-400" : "text-yellow-400"}>
                  {downloadedMosaics}/{totalMosaics} mosaics
                </span>
              )}
            </>
          )}
        </div>
      </button>

      {!collapsed && (
        <div className="border-t border-gray-800 p-4 space-y-4">
          {/* Reference Catalogs */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-xs font-medium text-gray-300">Reference Catalogs</h4>
              <div className="flex gap-2">
                {missingCatalogs > 0 && !catalogDownloading && (
                  <button
                    onClick={() => handleDownloadCatalogs(false)}
                    className="text-xs px-2.5 py-1 bg-blue-600 hover:bg-blue-700 rounded transition-colors"
                  >
                    Download Missing ({missingCatalogs})
                  </button>
                )}
                {!catalogDownloading && (
                  <button
                    onClick={() => handleDownloadCatalogs(true)}
                    className="text-xs px-2.5 py-1 bg-gray-800 hover:bg-gray-700 rounded transition-colors text-gray-400"
                  >
                    Re-download All
                  </button>
                )}
                {catalogDownloading && (
                  <span className="text-xs text-blue-400 flex items-center gap-1.5">
                    <span className="inline-block w-3 h-3 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                    Downloading...
                  </span>
                )}
              </div>
            </div>

            {overallProgress && (
              <div className="mb-2">
                <div className="flex items-center justify-between text-[10px] text-gray-500 mb-0.5">
                  <span>{overallProgress.message}</span>
                  <span>{Math.round(overallProgress.fraction * 100)}%</span>
                </div>
                <div className="w-full h-1 bg-gray-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500 transition-all duration-300"
                    style={{ width: `${overallProgress.fraction * 100}%` }}
                  />
                </div>
              </div>
            )}

            {refCatalogStatus && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-1">
                {refCatalogStatus.map((cat) => (
                  <div
                    key={cat.name}
                    className="flex items-center justify-between px-2.5 py-1.5 rounded bg-gray-800/50 text-xs"
                  >
                    <span className="text-gray-300 truncate mr-2">{cat.label}</span>
                    <div className="flex items-center gap-2 shrink-0">
                      {cat.status === "downloaded" && (
                        <>
                          <span className="text-gray-500">{cat.rows.toLocaleString()} rows</span>
                          <svg className="w-3 h-3 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                          </svg>
                        </>
                      )}
                      {cat.status === "missing" && (
                        <span className="text-yellow-500/70">missing</span>
                      )}
                      {cat.status === "downloading" && (
                        <span className="inline-block w-3 h-3 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                      )}
                      {(cat.status === "ok" || cat.status === "skip") && (
                        <svg className="w-3 h-3 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                      )}
                      {cat.status === "fail" && (
                        <span className="text-red-400">failed</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* HLSP Mosaics */}
          {totalMosaics > 0 && (
            <div>
              <div className="flex items-center justify-between mb-2">
                <h4 className="text-xs font-medium text-gray-300">HLSP Mosaics</h4>
                <div className="flex gap-2 items-center">
                  {downloadedMosaics < totalMosaics && !mosaicDownloading && (
                    <button
                      onClick={handleDownloadMosaics}
                      className="text-xs px-2.5 py-1 bg-blue-600 hover:bg-blue-700 rounded transition-colors"
                    >
                      Download HLSP Mosaics
                    </button>
                  )}
                  {mosaicDownloading && (
                    <span className="text-xs text-blue-400 flex items-center gap-1.5">
                      <span className="inline-block w-3 h-3 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                      Downloading mosaics...
                    </span>
                  )}
                </div>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-1">
                {mosaicStatus?.map((m) => (
                  <div
                    key={m.filter}
                    className="flex items-center justify-between px-2.5 py-1.5 rounded bg-gray-800/50 text-xs"
                  >
                    <span className="font-mono text-gray-300">{m.filter}</span>
                    {m.status === "downloaded" ? (
                      <div className="flex items-center gap-1.5">
                        <span className="text-gray-500">{formatSize(m.size_mb)}</span>
                        <svg className="w-3 h-3 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                      </div>
                    ) : (
                      <span className="text-yellow-500/70">missing</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
