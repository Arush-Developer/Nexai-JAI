import { BarChart3, Database, FlaskConical, LayoutDashboard, Network, Search, ShieldCheck, type LucideIcon } from "lucide-react";
import { NavLink } from "react-router-dom";
import { useApi } from "../services/api";
import type { ReplayStatus } from "../services/types";

const NAV: { to: string; label: string; icon: LucideIcon }[] = [
  { to: "/command", label: "Command Centre", icon: LayoutDashboard },
  { to: "/replay", label: "Live Replay", icon: FlaskConical },
  { to: "/intelligence", label: "Network Graph", icon: Network },
  { to: "/defence", label: "Policies", icon: ShieldCheck },
  { to: "/investigate", label: "Investigation", icon: Search },
  { to: "/data", label: "Dataset", icon: Database },
  { to: "/models", label: "Model Lab", icon: BarChart3 },
];

export function Sidebar({ ready, detail }: { ready: boolean; detail: string | null }) {
  const replay = useApi<ReplayStatus>(ready ? "/replay/status" : null, 2000);
  return (
    <aside className="md:w-52 shrink-0 border-b md:border-b-0 md:border-r border-ink-700/70 bg-[#070c18] md:flex md:flex-col">
      <nav className="flex md:flex-col gap-1 px-2 py-2 md:py-3 overflow-x-auto" aria-label="Primary">
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            title={label}
            className={({ isActive }) =>
              `flex items-center gap-2.5 rounded-lg px-3 py-2.5 whitespace-nowrap transition-colors ${
                isActive ? "bg-gradient-to-r from-blue-600/90 to-blue-500/40 text-white shadow-[0_0_14px_rgba(59,130,246,0.45)]" : "text-text-2 hover:bg-ink-800 hover:text-text-1"
              }`
            }
          >
            <Icon size={15} aria-hidden />
            <span className="hidden sm:inline">{label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="hidden md:block mt-auto px-4 py-3 border-t border-ink-700/60 text-[11px] space-y-2" data-testid="system-status">
        <div>
          <p className="text-text-3">System</p>
          <p className={`flex items-center gap-1.5 ${ready ? "text-good" : "text-warn"}`} title={detail ?? undefined}>
            <span className={`w-1.5 h-1.5 rounded-full ${ready ? "bg-good" : "bg-warn"}`} aria-hidden />{ready ? "Models and data loaded" : "Not fully loaded"}
          </p>
        </div>
        <div>
          <p className="text-text-3">Replay Engine</p>
          <p className="flex items-center gap-1.5 text-text-2 capitalize"><span className="w-1.5 h-1.5 rounded-full bg-accent" aria-hidden />{replay.data?.status ?? "Unknown"}</p>
        </div>
      </div>
    </aside>
  );
}
