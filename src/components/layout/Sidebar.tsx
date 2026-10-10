import { useLocation, Link } from "react-router-dom";
import { LayoutDashboard, Database, Telescope, Table2, Sparkles, BarChart3, Settings } from "lucide-react";
import { clsx } from "clsx";

const NAV_ITEMS = [
  { path: "/data", label: "Data", icon: Database },
  { path: "/", label: "Pipeline", icon: LayoutDashboard },
  { path: "/viewer", label: "Viewer", icon: Telescope },
  { path: "/catalog", label: "Catalog", icon: Table2 },
  { path: "/anomalies", label: "Anomalies", icon: Sparkles },
  { path: "/compare", label: "Compare", icon: BarChart3 },
  { path: "/config", label: "Config", icon: Settings },
];

export function Sidebar() {
  const location = useLocation();

  return (
    <aside className="w-56 bg-gray-900 border-r border-gray-800 flex flex-col">
      <div className="p-4 border-b border-gray-800">
        <h1 className="text-lg font-bold text-blue-400">JWST Discovery</h1>
        <p className="text-xs text-gray-500 mt-1">Autonomous Pipeline</p>
      </div>
      <nav className="flex-1 p-2 space-y-1">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const isActive = location.pathname === item.path;
          return (
            <Link
              key={item.path}
              to={item.path}
              className={clsx(
                "flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors",
                isActive
                  ? "bg-blue-500/20 text-blue-400"
                  : "text-gray-400 hover:text-gray-200 hover:bg-gray-800"
              )}
            >
              <Icon size={18} />
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="p-4 border-t border-gray-800">
        <p className="text-xs text-gray-600">v0.1.0</p>
      </div>
    </aside>
  );
}
