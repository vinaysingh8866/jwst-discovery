import { PipelineDashboard } from "@/components/pipeline/PipelineDashboard";

export function DashboardPage() {
  return (
    <div className="absolute inset-0 overflow-auto p-4">
      <PipelineDashboard />
    </div>
  );
}
