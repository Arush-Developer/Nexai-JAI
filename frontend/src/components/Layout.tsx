import { useEffect, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { DATA_CONNECTED, useApi } from "../services/api";
import { Navbar } from "./Navbar";
import { Sidebar } from "./Sidebar";
import { Notice } from "./ui";

/**
 * App shell. Nothing is preloaded: until data is connected, every screen leads to the Data Manager, which asks for it.
 */
export function Layout({ children }: { children: ReactNode }) {
  const health = useApi<{ status: string; detail: string | null }>("/health", 3000);
  const ready = health.data?.status === "ok";
  const standalone = useLocation().pathname === "/data";
  // Health is only polled every few seconds, so a run announces itself. Otherwise a click straight after it bounces back.
  const [justConnected, setJustConnected] = useState(false);
  useEffect(() => {
    const connected = () => { setJustConnected(true); health.reload(); };
    window.addEventListener(DATA_CONNECTED, connected);
    return () => window.removeEventListener(DATA_CONNECTED, connected);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (health.data?.status === "no_data" && !standalone && !justConnected) return <Navigate to="/data" replace />;
  return (
    <div className="h-full flex flex-col">
      <Navbar ready={ready} />
      <div className="flex-1 min-h-0 flex flex-col md:flex-row">
        <Sidebar ready={ready} detail={health.data?.detail ?? null} />
        <main className="flex-1 min-w-0 min-h-0 overflow-y-auto">
          {health.error && (
            <div className="p-4"><Notice tone="crit" title="Backend unreachable">{health.error}. Start it with uvicorn on port 8000.</Notice></div>
          )}
          {!standalone && health.data?.status === "unavailable" && (
            <div className="p-4"><Notice tone="crit" title="Backend could not load models">{health.data.detail}</Notice></div>
          )}
          {!standalone && health.data?.status === "loading" && (
            <div className="p-4"><Notice tone="info" title="Backend is loading the models and scoring the input…" /></div>
          )}
          {(ready || standalone || justConnected) && children}
        </main>
      </div>
    </div>
  );
}
