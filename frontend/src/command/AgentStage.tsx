import { useEffect, useRef, useState } from "react";
import { Notice } from "../components/ui";
import { AGENT_IDS, AGENT_LOOK, AGENT_NAMES, type AgentId } from "./agents";
import { AgentScene } from "./AgentScene";
import type { Trace } from "./useAgentFocus";

const WORKING: Record<AgentId, string> = {
  A1: "Classifying flow…", A2: "Analyzing behavior…", A3: "Combining results…", A4: "Grouping flows…", A5: "Evaluating policy…",
};
const STATE: Record<string, string> = { waiting: "Waiting", offline: "Off for this input" };

function AgentCard({ id, row }: { id: AgentId; row: Trace[AgentId] }) {
  const look = AGENT_LOOK[id];
  const on = row.state === "working";
  const off = row.state === "offline";
  return (
    <div className={`hud-tag ${on ? "hud-tag-on" : ""} ${off ? "opacity-60" : ""}`} style={{ borderColor: on ? look.css : undefined, boxShadow: on ? `0 0 18px ${look.css}66` : undefined }} data-testid={`stage-${id}`} data-state={row.state}>
      <p className="font-medium text-[11px] text-text-1 leading-tight whitespace-nowrap"><span className="font-semibold" style={{ color: look.css }}>{id}</span> {AGENT_NAMES[id]}</p>
      {(on || row.state !== "done") && <p className="text-[10px] text-text-3 mt-0.5 whitespace-nowrap">{on ? WORKING[id] : STATE[row.state]}</p>}
      {row.value && !off && <p className="text-[11px] font-medium mt-0.5 num whitespace-nowrap max-w-[150px] truncate" style={{ color: look.css }}>{row.value}</p>}
    </div>
  );
}

/** The 3D room, with each agent's real state on a label that follows its robot. */
export default function AgentStage({ focus, trace, offline, heading, watching }: {
  focus: AgentId | null;
  trace: Trace;
  offline: AgentId[];
  heading: React.ReactNode;
  watching: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const labels = useRef<Partial<Record<AgentId, HTMLDivElement | null>>>({});
  const badges = useRef<Partial<Record<AgentId, HTMLDivElement | null>>>({});
  const scene = useRef<AgentScene | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    if (!canvas.current || !container.current) return;
    try {
      const made = new AgentScene(canvas.current, container.current);
      made.onFrame = () => {
        for (const id of AGENT_IDS) {
          const look = AGENT_LOOK[id];
          const label = labels.current[id];
          if (label) {
            const at = made.project(id, look.side === "right" ? 0.85 : -0.85, 2.15);
            // Labels stay inside the room, however narrow it gets.
            const width = label.offsetWidth;
            const left = look.side === "right" ? at.x : at.x - width;
            const x = Math.max(6, Math.min(left, container.current!.clientWidth - width - 6));
            label.style.transform = `translate(${x}px, ${at.y}px) translateY(-50%)`;
          }
          const badge = badges.current[id];
          if (badge) {
            const at = made.project(id, 0, 2.86);
            badge.style.transform = `translate(${at.x}px, ${at.y}px) translate(-50%, -50%)`;
          }
        }
      };
      scene.current = made;
      made.setOffline(offline);
      made.setFocus(focus);
      return () => { made.dispose(); scene.current = null; };
    } catch (e) {
      setFailed((e as Error).message);
    }
    // The scene is built once. The effects below keep it current.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { scene.current?.setFocus(focus); }, [focus]);
  useEffect(() => { scene.current?.setOffline(offline); }, [offline]);

  return (
    <div ref={container} className="hud-stage relative h-[560px] xl:h-full xl:min-h-[640px] overflow-hidden" data-testid="agent-stage">
      <canvas ref={canvas} className="absolute inset-0 w-full h-full" aria-label={`3D view of the five agents and the orchestrator. ${watching}`} role="img" />
      <div className="absolute top-3 left-4 right-4 sm:right-auto z-10 sm:max-w-[32%]">{heading}</div>
      <div className="absolute top-16 sm:top-3 left-1/2 -translate-x-1/2 z-10 hud-tag text-center w-[210px]" data-testid="orchestrator-card">
        <p className="font-semibold tracking-wide text-text-1">ORCHESTRATOR</p>
        <p className="text-[11px] text-text-2 mt-1">{watching}</p>
      </div>
      {failed && <div className="absolute inset-x-4 top-24 z-10"><Notice tone="warn" title="3D view is unavailable in this browser">{failed} The agents' live state is still in the panels beside this.</Notice></div>}
      {!failed && AGENT_IDS.map((id) => (
        <div key={id} ref={(el) => { labels.current[id] = el; }} className="absolute left-0 top-0 z-10 will-change-transform pointer-events-none max-sm:hidden"><AgentCard id={id} row={trace[id]} /></div>
      ))}
      {!failed && AGENT_IDS.map((id) => (
        <div key={id} ref={(el) => { badges.current[id] = el; }} className="absolute left-0 top-0 z-10 will-change-transform pointer-events-none">
          <div className="hud-badge" style={{ borderColor: AGENT_LOOK[id].css, color: AGENT_LOOK[id].css, opacity: trace[id].state === "offline" ? 0.5 : 1 }}>{id}</div>
        </div>
      ))}
    </div>
  );
}
