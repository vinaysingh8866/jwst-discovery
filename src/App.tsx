import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { DashboardPage } from "@/pages/DashboardPage";
import { DataPage } from "@/pages/DataPage";
import { ViewerPage } from "@/pages/ViewerPage";
import { CatalogPage } from "@/pages/CatalogPage";
import { AnomalyPage } from "@/pages/AnomalyPage";
import { ConfigPage } from "@/pages/ConfigPage";
import { ComparisonPage } from "@/pages/ComparisonPage";
import { usePipelineEvents } from "@/hooks/usePipelineEvents";

function AppContent() {
  usePipelineEvents();

  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/data" element={<DataPage />} />
        <Route path="/viewer" element={<ViewerPage />} />
        <Route path="/catalog" element={<CatalogPage />} />
        <Route path="/anomalies" element={<AnomalyPage />} />
        <Route path="/compare" element={<ComparisonPage />} />
        <Route path="/config" element={<ConfigPage />} />
      </Routes>
    </AppShell>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppContent />
    </BrowserRouter>
  );
}
