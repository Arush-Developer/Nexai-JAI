import { OctagonX, Search } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { post, useApi } from "../services/api";
import type { ReplayStatus } from "../services/types";
import { Btn } from "./ui";

function Logo() {
  return (
    <svg viewBox="0 0 40 40" className="w-9 h-9 shrink-0 drop-shadow-[0_0_8px_rgba(56,189,248,0.7)]" aria-hidden>
      <path d="M20 2 36 11v18L20 38 4 29V11z" fill="none" stroke="#38bdf8" strokeWidth="2" />
      <path d="M20 11 28 15.500v9L20 29l-8-4.500v-9zM20 11v9m0 0 8-4.500M20 20l-8-4.500" fill="none" stroke="#7dd3fc" strokeWidth="1.500" strokeLinejoin="round" />
    </svg>
  );
}

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <div className="hidden lg:block text-right leading-tight">
      <p className="text-[11px] text-text-2">{now.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" })}</p>
      <p className="text-accent font-semibold num">{now.toLocaleTimeString("en-US")}</p>
    </div>
  );
}

/** The brand, record search, replay state and the global stop, visible from every screen. */
export function Navbar({ ready }: { ready: boolean }) {
  const replay = useApi<ReplayStatus>(ready ? "/replay/status" : null, 2000);
  const [stopping, setStopping] = useState(false);
  const [query, setQuery] = useState("");
  const navigate = useNavigate();
  const status = replay.data?.status ?? "idle";
  const running = status === "running";
  const active = running || status === "paused";

  const stop = async () => {
    setStopping(true);
    try {
      await post("/replay/stop");
      replay.reload();
    } finally {
      setStopping(false);
    }
  };

  const search = (e: FormEvent) => {
    e.preventDefault();
    const id = query.trim();
    if (id) navigate(`/investigate?flow=${encodeURIComponent(id)}`);
  };

  return (
    <header className="shrink-0 flex items-center gap-3 md:gap-5 border-b border-ink-700/70 bg-[#070c18] px-3 md:px-4 py-2">
      <div className="flex items-center gap-2.5 min-w-0">
        <Logo />
        <div className="min-w-0 leading-tight">
          <p className="text-[15px] sm:text-[17px] font-semibold tracking-tight text-white sm:whitespace-nowrap leading-tight">CyberSentinel <span className="block sm:inline text-accent text-[11px] sm:text-[13px] font-medium">by NexAI</span></p>
          <p className="hidden xl:block text-[11px] text-text-2 whitespace-nowrap">Multi-Agent Network Intrusion Detection & Adaptive Defense</p>
        </div>
      </div>
      <form onSubmit={search} className="hidden md:flex flex-1 max-w-md items-center gap-2 rounded-lg border border-ink-700 bg-ink-900 px-3 py-1.5" role="search">
        <Search size={14} className="text-text-3 shrink-0" aria-hidden />
        <input aria-label="Search record ID" placeholder="Search record ID…" value={query} onChange={(e) => setQuery(e.target.value)} className="bg-transparent outline-none w-full text-text-1 placeholder:text-text-3" />
      </form>
      <div className="ml-auto flex items-center gap-3 md:gap-4">
        <span className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium capitalize ${running ? "border-good/50 bg-good/10 text-good" : "border-ink-600 text-text-2"}`} data-testid="replay-state">
          <span className={`w-2 h-2 rounded-full ${running ? "bg-good animate-pulse" : status === "paused" ? "bg-warn" : "bg-ink-600"}`} aria-hidden />
          {running ? "LIVE" : <><span className="hidden sm:inline">Replay </span>{status}</>}
        </span>
        <Clock />
        <div className="hidden lg:flex items-center gap-2">
          <span className="w-8 h-8 rounded-full bg-blue-600 text-white text-[11px] font-semibold flex items-center justify-center" aria-hidden>NX</span>
          <div className="leading-tight"><p className="text-xs text-text-1">NexAI</p><p className="text-[11px] text-text-3">Local session</p></div>
        </div>
        <Btn tone="danger" onClick={stop} disabled={!active || stopping} title="Stop replay and running investigations">
          <OctagonX size={14} aria-hidden /> STOP
        </Btn>
      </div>
    </header>
  );
}
