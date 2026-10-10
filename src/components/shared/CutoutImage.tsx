import { useState, useEffect } from "react";
import { useResultsStore } from "@/stores/resultsStore";

/**
 * Render a cutout PNG from the workspace report directory, going through the
 * results store's image cache so the same file is only read once.
 */
export function CutoutImage({ workspace, relativePath, alt }: { workspace: string; relativePath: string; alt: string }) {
  const loadImage = useResultsStore((s) => s.loadImage);
  const cached = useResultsStore((s) => s.imageCache[`${workspace}/${relativePath}`]);
  const [src, setSrc] = useState<string | null>(cached || null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (cached) {
      setSrc(cached);
      return;
    }
    let cancelled = false;
    loadImage(workspace, relativePath).then((url) => {
      if (!cancelled) setSrc(url);
    }).catch(() => {
      if (!cancelled) setFailed(true);
    });
    return () => { cancelled = true; };
  }, [workspace, relativePath, cached, loadImage]);

  if (failed) {
    return (
      <div className="w-full h-full bg-gray-800 flex items-center justify-center text-gray-600 text-xs">
        No image
      </div>
    );
  }
  if (!src) {
    return (
      <div className="w-full h-full bg-gray-800 flex items-center justify-center">
        <div className="w-4 h-4 border-2 border-gray-600 border-t-blue-400 rounded-full animate-spin" />
      </div>
    );
  }
  return <img src={src} alt={alt} className="w-full h-full object-contain" />;
}
