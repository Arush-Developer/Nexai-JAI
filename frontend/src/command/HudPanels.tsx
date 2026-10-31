// The panels around the 3D room. Each one shows real pipeline data, or says plainly that the input lacks it.
import { Activity, Pause, Play, Square, StepForward } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { CLASS_COLOR, Empty, fmt } from "../components/ui";
import { api, post, useApi } from "../services/api";
import type { DefenseState, FlowDetail, ReplayFlowEvent, ReplayStatus } from "../services/types";
import { AGENT_IDS, AGENT_LOOK, AGENT_NAMES } from "./agents";
import type { Trace, TraceFlow } from "./useAgentFocus";

const ABSENT = "Not in this input";

export function Hud({ title, actions, children, className = "" }: { title: string; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`hud-panel ${className}`}>
      <div className="flex items-center justify-between gap-2 mb-2.5">
        <h2 className="text-[15px] font-semibold text-text-1">{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex justify-between gap-3 py-[3px] text-xs">
      <dt className="text-text-3 shrink-0">{label}</dt>
      <dd className="text-text-1 num text-right truncate">{value ?? ABSENT}</dd>
    </div>
  );
}

const IconButton = (props: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
  <button {...props} className="w-8 h-8 inline-flex items-center justify-center rounded-full bg-ink-700 text-text-1 hover:bg-ink-600 disabled:opacity-35 disabled:cursor-not-allowed" />
);

/** The selected flow's details and raw features, read without starting an investigation. */
export function useFlowDetail(flowId: string | null) {
  const [detail, setDetail] = useState<FlowDetail | null>(null);
  const [features, setFeatures] = useState<Record<string, number> | null>(null);
  useEffect(() => {
    if (!flowId) return;
    let current = true;
    api<FlowDetail>(`/flows/${encodeURIComponent(flowId)}?investigate=false`).then((d) => { if (current) setDetail(d); }).catch(() => { if (current) setDetail(null); });
    api<Record<string, number>>(`/flows/${encodeURIComponent(flowId)}/features`).then((f) => { if (current) setFeatures(f); }).catch(() => { if (current) setFeatures(null); });
    return () => { current = false; };
  }, [flowId]);
  return { detail, features };
}

export function LiveReplayPanel({ status, flow, detail, features, reload }: {
  status: ReplayStatus | null;
  flow: TraceFlow | null;
  detail: FlowDetail | null;
  features: Record<string, number> | null;
  reload: () => void;
}) {
  const [problem, setProblem] = useState<string | null>(null);
  // A small file would otherwise finish in under a second, so its default pace is one a person can watch.
  const [picked, setPicked] = useState<number | null>(null);
  const small = (status?.total ?? 0) <= 2000;
  const speed = picked ?? (small ? 2 : 500);
  const state = status?.status ?? "idle";
  const running = state === "running";
  const paused = state === "paused";
  const call = async (path: string, body?: unknown) => {
    setProblem(null);
    try { await post(path, body); reload(); } catch (e) { setProblem((e as Error).message); }
  };
  const net = detail?.network ?? {};
  return (
    <Hud title="Live Replay" actions={<Link to="/replay" className="text-[11px] text-text-3 hover:text-accent">Replay Lab</Link>}>
      <p className="text-text-1 text-[13px] mb-2 truncate" data-testid="live-record">
        {flow ? <>{running ? "Processing" : "Last"} record <span className="num font-medium">{flow.flow_id}</span></> : "No record yet"}
      </p>
      <div className="flex items-center gap-2">
        <div className="flex-1 min-w-0">
          <div className="h-2 rounded-full bg-ink-700 overflow-hidden" role="progressbar" aria-label="Replay progress" aria-valuemin={0} aria-valuemax={status?.total ?? 0} aria-valuenow={status?.cursor ?? 0}>
            <div className="h-full rounded-full bg-gradient-to-r from-accent to-blue-500 transition-[width]" style={{ width: `${(status?.progress ?? 0) * 100}%` }} />
          </div>
          <p className="text-[11px] text-text-3 mt-1 num truncate"><span className="capitalize">{state}</span>, {fmt(status?.cursor)} of {fmt(status?.total)}</p>
        </div>
        {paused ? <IconButton aria-label="Resume replay" onClick={() => call("/replay/resume")}><StepForward size={14} /></IconButton>
          : running ? <IconButton aria-label="Pause replay" onClick={() => call("/replay/pause")}><Pause size={14} /></IconButton>
          : <IconButton aria-label="Start replay" title="Agents 4 and 5 work one batch ahead of the display, so a small file uses batches of 10" onClick={() => call("/replay/start", { speed, batch_size: small ? 10 : 500, limit: null, resume_saved: false })}><Play size={14} /></IconButton>}
        <IconButton aria-label="Stop replay" disabled={!running && !paused} onClick={() => call("/replay/stop")}><Square size={13} /></IconButton>
      </div>
      <label className="flex items-center justify-between gap-2 text-[11px] text-text-3 mt-2">Speed (flows per second)
        <select aria-label="Replay speed" value={speed} disabled={running || paused} onChange={(e) => setPicked(Number(e.target.value))} className="bg-ink-800 border border-ink-600 rounded px-1.5 py-0.5 text-text-1 num">
          {[1, 2, 5, 20, 100, 500, 2000].map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
      </label>
      {problem && <p className="text-xs text-crit mt-1" role="alert">{problem}</p>}
      <dl className="mt-2 border-t border-ink-700/70 pt-2">
        <Row label="Source IP" value={net.source_ip} />
        <Row label="Destination IP" value={net.destination_ip} />
        <Row label="Protocol" value={net.protocol} />
        <Row label="Src Port" value={net.source_port} />
        <Row label="Dst Port" value={net.destination_port} />
        <Row label="Flow Duration" value={features ? fmt(features["Flow Duration"]) : null} />
        <Row label="Total Fwd Packets" value={features ? fmt(features["Total Fwd Packets"]) : null} />
      </dl>
    </Hud>
  );
}

export function AgentTracePanel({ trace, flow }: { trace: Trace; flow: TraceFlow | null }) {
  return (
    <Hud title="AI Agent Trace" actions={flow && <span className="text-[11px] text-text-3 num">{flow.flow_id}</span>}>
      <ul className="space-y-2" data-testid="agent-trace">
        {AGENT_IDS.map((id) => (
          <li key={id} className={`flex items-center gap-2 text-xs rounded-md ${trace[id].state === "working" ? "bg-ink-700/60" : ""}`}>
            <span className="w-6 h-6 shrink-0 rounded-full border text-[10px] font-semibold flex items-center justify-center" style={{ borderColor: AGENT_LOOK[id].css, color: AGENT_LOOK[id].css }}>{id}</span>
            <span className="text-text-2 truncate flex-1 min-w-0">{AGENT_NAMES[id]}</span>
            <span className="num font-medium text-right truncate max-w-[52%]" title={trace[id].value} style={{ color: trace[id].value && trace[id].state !== "offline" ? AGENT_LOOK[id].css : "#74829a" }}>
              {trace[id].value || (trace[id].state === "waiting" ? "–" : "")}
            </span>
          </li>
        ))}
      </ul>
      <p className="text-[10px] text-text-3 mt-2">An A2 score is a percentile among benign flows, not a probability of attack.</p>
    </Hud>
  );
}

export function ActivePoliciesPanel({ defense }: { defense: DefenseState | null }) {
  const items = useMemo(() => {
    const all = defense?.policies ?? [];
    return [...all.filter((p) => p.status === "ACTIVE"), ...all.filter((p) => p.status !== "ACTIVE")].slice(0, 5);
  }, [defense]);
  return (
    <Hud title="Active Policies" actions={<Link to="/defence" className="text-[11px] text-text-3 hover:text-accent">View All →</Link>}>
      {!defense?.available ? <Empty title="Agent 5 is off">{defense?.unavailable_reason ?? "Waiting for the backend."}</Empty>
        : !items.length ? <Empty title="No policies yet">Agent 5 activates one only when the evidence justifies it.</Empty> : (
          <table className="w-full text-xs num" data-testid="policies">
            <tbody>
              {items.map((p) => (
                <tr key={p.policy_id} className="border-t border-ink-700/60 first:border-0">
                  <td className="py-1.5 text-text-1">{p.policy_id}</td>
                  <td className="py-1.5 text-text-2">{p.scope_value}</td>
                  <td className={`py-1.5 text-right font-medium ${p.status === "ACTIVE" ? "text-good" : "text-text-3"}`}>{p.status === "ACTIVE" ? "Active" : "Expired"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
    </Hud>
  );
}

type TimelineFlow = ReplayFlowEvent;

export function TimelinePanel({ status, flows, selected, onSelect }: { status: ReplayStatus | null; flows: ReplayFlowEvent[]; selected: string | null; onSelect: (flow: TimelineFlow) => void }) {
  const cursor = status?.cursor ?? 0;
  // After a page reload the live stream may be empty, so the recent past comes from the saved replay.
  const saved = useApi<{ items: TimelineFlow[] }>(!flows.length && cursor ? `/flows?scope=replay&offset=${Math.max(0, cursor - 64)}&limit=64` : null);
  const ordered = useMemo(() => (flows.length ? [...flows].reverse() : saved.data?.items ?? []).slice(-64), [flows, saved.data]);
  const cards = ordered.slice(-6);
  const actionOf = (f: TimelineFlow) => f.defense_action ?? f.policy_decision;
  return (
    <Hud title="Chronological Replay Timeline" actions={<span className="text-[11px] text-text-3">Supplied order, newest right</span>}>
      {!ordered.length ? <Empty title="No flows replayed yet">Press play in Live Replay.</Empty> : (
        <>
          <div className="relative h-8 flex items-center gap-[3px] overflow-hidden" data-testid="timeline-dots">
            <div className="absolute inset-x-0 top-1/2 h-px bg-accent/40" />
            {ordered.map((f) => (
              <button key={f.flow_id} title={`${f.flow_id}: ${f.final_class}, ${actionOf(f)}`} aria-label={`Show record ${f.flow_id}`} onClick={() => onSelect(f)}
                className={`relative rounded-full shrink-0 ${f.flow_id === selected ? "w-3 h-3 ring-2 ring-white" : "w-1.5 h-1.5"}`} style={{ background: CLASS_COLOR[f.final_class] ?? "#74829a" }} />
            ))}
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 2xl:grid-cols-6 gap-2 mt-2">
            {cards.map((f) => {
              const blocked = actionOf(f) === "BLOCK";
              return (
                <button key={f.flow_id} onClick={() => onSelect(f)} className={`text-left rounded-lg border p-2 min-w-0 transition-colors hover:bg-ink-700/40 ${f.flow_id === selected ? "border-warn/70 bg-warn/5" : blocked ? "border-crit/40" : "border-ink-700"}`}>
                  <span className="block text-[11px] text-text-3 num truncate">{f.flow_id}</span>
                  <span className="block font-medium mt-1 truncate" style={{ color: CLASS_COLOR[f.final_class] }}>{f.final_class}</span>
                  <span className={`block text-[11px] mt-0.5 ${blocked ? "text-crit" : "text-text-3"}`}>{actionOf(f)}</span>
                </button>
              );
            })}
          </div>
        </>
      )}
    </Hud>
  );
}

const TABS = ["Raw", "Features", "Agent Output", "Policy"] as const;

export function FlowDetailsPanel({ detail, features }: { detail: FlowDetail | null; features: Record<string, number> | null }) {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Features");
  return (
    <Hud title="Flow Details" actions={detail && <Link to={`/investigate?flow=${detail.flow_id}`} className="text-[11px] text-text-3 hover:text-accent num">Open {detail.flow_id}</Link>}>
      <div className="flex gap-1 mb-2" role="tablist" aria-label="Flow details">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={`px-2 py-1 rounded text-[11px] ${tab === t ? "bg-accent/20 text-accent" : "text-text-3 hover:text-text-1"}`}>{t}</button>
        ))}
      </div>
      {!detail ? <Empty title="No record selected" /> : (
        <dl className="max-h-[172px] overflow-y-auto pr-1">
          {tab === "Raw" && <>
            <Row label="Record ID" value={detail.flow_id} />
            {Object.entries(detail.network).map(([k, v]) => <Row key={k} label={k} value={v} />)}
            {detail.unavailable_metadata.length > 0 && <Row label="Absent from input" value={detail.unavailable_metadata.join(", ")} />}
          </>}
          {tab === "Features" && (features ? Object.entries(features).map(([k, v]) => <Row key={k} label={k} value={v?.toLocaleString("en-IN", { maximumFractionDigits: 2 })} />) : <p className="text-xs text-text-3">Features are not available.</p>)}
          {tab === "Agent Output" && <>
            <Row label="A1 class" value={detail.threat.predicted_class} />
            <Row label="A1 confidence" value={detail.threat.confidence.toFixed(3)} />
            <Row label="A2 score (percentile)" value={detail.anomaly.anomaly_score.toFixed(3)} />
            <Row label="A2 flagged" value={detail.anomaly.is_anomaly ? "Yes" : "No"} />
            {detail.anomaly.supporting_features.slice(0, 4).map((s) => <Row key={s.feature} label={`A2 evidence: ${s.feature}`} value={s.measured_value.toLocaleString("en-IN", { maximumFractionDigits: 2 })} />)}
            <Row label="A3 final class" value={detail.fusion.final_class} />
            <Row label="A3 status" value={detail.fusion.evidence_status} />
          </>}
          {tab === "Policy" && <>
            <Row label="Simulated decision" value={detail.policy.decision} />
            <Row label="Agent 5 action" value={detail.replay?.action ?? "Not available"} />
            <Row label="Applied policy" value={detail.replay?.applied_policy_id ?? "None"} />
          </>}
        </dl>
      )}
    </Hud>
  );
}

export function ThreatGraphPanel({ defense }: { defense: DefenseState | null }) {
  const graph = useMemo(() => {
    const groups = (defense?.groups ?? []).filter((g) => g.scope_type === "SOURCE_IP");
    if (!groups.length) return null;
    const hub = groups.reduce((a, b) => (b.member_count > a.member_count ? b : a));
    const blocked = (defense?.policies ?? []).some((p) => p.status === "ACTIVE" && p.scope_value === hub.scope_value);
    const peers = groups.filter((g) => g.scope_value !== hub.scope_value && g.destination_ips.some((d) => hub.destination_ips.includes(d))).slice(0, 3).map((g) => g.scope_value);
    const nodes = [...hub.destination_ips.slice(0, 6 - peers.length).map((label) => ({ label, kind: "destination" })), ...[...new Set(peers)].map((label) => ({ label, kind: "source" }))];
    return { hub, blocked, nodes };
  }, [defense]);
  const color = (kind: string) => (kind === "destination" ? "#38bdf8" : "#fbbf24");
  return (
    <Hud title="Threat Intelligence Graph" actions={<span className="text-[11px] text-text-3">Largest Agent 4 group</span>}>
      {!defense?.available ? <Empty title="Agent 4 is off">{defense?.unavailable_reason ?? "Waiting for the backend."}</Empty>
        : !graph ? <Empty title="No correlation groups yet" /> : (
          <div>
            <svg viewBox="0 0 360 170" className="w-full h-[170px]" role="img" aria-label={`Group ${graph.hub.group_id}: source ${graph.hub.scope_value} and ${graph.nodes.length} related addresses`} data-testid="threat-graph">
              {graph.nodes.map((n, i) => {
                const angle = (i / graph.nodes.length) * Math.PI * 2 - Math.PI / 2;
                const x = 180 + Math.cos(angle) * 130;
                const y = 80 + Math.sin(angle) * 58;
                return (
                  <g key={n.label}>
                    <line x1={180} y1={80} x2={x} y2={y} stroke={color(n.kind)} strokeOpacity={0.45} />
                    <circle cx={x} cy={y} r={5} fill={color(n.kind)} />
                    <text x={x} y={y + 16} textAnchor="middle" fontSize={9.5} fill="#a9b5c7">{n.label}</text>
                  </g>
                );
              })}
              <circle cx={180} cy={80} r={15} fill="none" stroke={graph.blocked ? "#f87171" : "#fbbf24"} strokeOpacity={0.5} />
              <circle cx={180} cy={80} r={8} fill={graph.blocked ? "#f87171" : "#fbbf24"} />
              <text x={180} y={106} textAnchor="middle" fontSize={10} fontWeight={600} fill="#e8edf5">{graph.hub.scope_value}</text>
            </svg>
            <p className="text-[11px] text-text-3 flex flex-wrap gap-x-3 gap-y-0.5">
              <span><span className="inline-block w-2 h-2 rounded-full bg-crit mr-1" />Active policy</span>
              <span><span className="inline-block w-2 h-2 rounded-full bg-warn mr-1" />Suspicious source</span>
              <span><span className="inline-block w-2 h-2 rounded-full bg-accent mr-1" />Destination</span>
              <span className="num">{fmt(graph.hub.member_count)} flows in {graph.hub.group_id}</span>
            </p>
          </div>
        )}
    </Hud>
  );
}

export function PacingNote() {
  return <p className="text-[11px] text-text-3 flex gap-1.5 items-start"><Activity size={12} className="shrink-0 mt-0.5" aria-hidden />The pipeline scores whole batches in milliseconds, too fast to see. So the orchestrator steps through the newest flow's real results one agent at a time. Totals include every flow.</p>;
}
