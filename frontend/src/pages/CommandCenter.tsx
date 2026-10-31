import { Suspense, lazy, useMemo } from "react";
import { Link } from "react-router-dom";
import type { AgentId } from "../command/agents";
import { ActivePoliciesPanel, AgentTracePanel, FlowDetailsPanel, LiveReplayPanel, PacingNote, ThreatGraphPanel, TimelinePanel, useFlowDetail } from "../command/HudPanels";
import { useAgentFocus } from "../command/useAgentFocus";
import { AgentStatus } from "../components/AgentStatus";
import { BarList, CLASS_COLOR, ClassTag, DecisionBadge, Empty, Notice, Page, Panel, Stat, fmt, pct } from "../components/ui";
import { ReplayInputNotice } from "../replay/ReplayInputNotice";
import { IncidentTimeline } from "../investigations/IncidentTimeline";
import { useApi } from "../services/api";
import { useReplayStream } from "../services/replay";
import { CLASSES, type DefenseState, type Incident, type ReplayStatus } from "../services/types";

// three.js is large, so only this screen downloads it, and only when it opens.
const AgentStage = lazy(() => import("../command/AgentStage"));
const NO_ENDPOINTS: AgentId[] = ["A4", "A5"];
const NONE: AgentId[] = [];
const WATCHING: Record<AgentId, string> = {
  A1: "Watching A1: Threat Detection", A2: "Watching A2: Anomaly Detection", A3: "Watching A3: Evidence Fusion",
  A4: "Watching A4: Temporal Correlation", A5: "Watching A5: Adaptive Defense",
};

interface Summary {
  replay: ReplayStatus;
  incidents: { scope: string; total: number; items: Incident[] };
  campaign_mode: string;
  limitations: string[];
  replay_input: string;
}

export function CommandCenter() {
  const { data, error, reload } = useApi<Summary>("/summary", 1500);
  const stream = useReplayStream();
  const replayStatus = data?.replay.status;
  const defenseOn = data?.replay.defense_available ?? false;
  const { focus, flow, trace, inspect } = useAgentFocus(stream.flows, stream.policies, replayStatus, defenseOn);
  const { detail, features } = useFlowDetail(flow?.flow_id ?? null);
  const defense = useApi<DefenseState>("/defense?member_limit=0&group_limit=200", 3000);
  const offline = useMemo(() => (defenseOn ? NONE : NO_ENDPOINTS), [defenseOn]);
  if (error && !data) return <Page title="Command Centre"><Notice tone="crit" title="Could not load summary">{error}</Notice></Page>;
  if (!data) return <Page title="Command Centre"><p className="text-text-3">Loading…</p></Page>;
  const { replay, incidents } = data;
  const c = replay.counts;
  const agree = c.agreement.AGREE ?? 0;
  const disagree = c.agreement.DISAGREE ?? 0;

  return (
    <div className="p-3 space-y-3 max-w-[1800px] mx-auto">
      <div className="grid xl:grid-cols-[minmax(0,1fr)_310px] gap-3">
        <Suspense fallback={<div className="hud-stage h-[560px] xl:h-[640px] flex items-center justify-center text-text-3">Loading the 3D view…</div>}>
          <AgentStage focus={focus} trace={trace} offline={offline}
            watching={focus ? WATCHING[focus] : replay.status === "running" ? "Between steps" : `Idle. Replay ${replay.status}`}
            heading={<>
              <h1 className="text-[17px] font-semibold tracking-tight text-text-1">Command Centre</h1>
              <p className="text-[11px] text-text-2 truncate" title={data.replay_input}>Replay of {data.replay_input.split(/[\\/]/).pop()}. Decisions are simulated.</p>
            </>} />
        </Suspense>
        <div className="space-y-3 min-w-0">
          <LiveReplayPanel status={replay} flow={flow} detail={detail} features={features} reload={reload} />
          <AgentTracePanel trace={trace} flow={flow} />
          <ActivePoliciesPanel defense={defense.data} />
        </div>
      </div>
      <div className="grid xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_minmax(0,1fr)] gap-3 items-start">
        <TimelinePanel status={replay} flows={stream.flows} selected={flow?.flow_id ?? null} onSelect={inspect} />
        <FlowDetailsPanel detail={detail} features={features} />
        <ThreatGraphPanel defense={defense.data} />
      </div>
      <PacingNote />
      <ReplayInputNotice input={data.replay_input} />
      {replay.cursor === 0 && (
        <Notice tone="info" title="No replay has run yet">
          Counts stay at zero until flows are replayed. <Link className="underline text-accent" to="/replay">Open Replay Lab</Link> to start one.
        </Notice>
      )}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Replayed flows" value={fmt(c.total)} hint={`of ${fmt(replay.total)} (${pct(replay.progress, 1)})`} />
        <Stat label="Replay state" value={<span className="text-lg capitalize">{replay.status}</span>} hint={replay.inference_flows_per_second ? `${fmt(replay.inference_flows_per_second)} flows/s pipeline throughput` : undefined} />
        <Stat label="Agents agree" value={c.total ? pct(agree / c.total, 1) : "–"} hint={`${fmt(agree)} agree, ${fmt(disagree)} disagree`} />
        <Stat label="Campaign hypotheses" value={fmt(replay.campaigns)} hint={`${fmt(replay.investigations)} investigations, ${fmt(replay.investigation_triggers)} triggers`} />
      </div>
      <AgentStatus />
      <div className={`grid gap-3 ${replay.defense_available ? "lg:grid-cols-4" : "lg:grid-cols-3"}`}>
        <Panel title="Final class">
          <BarList items={CLASSES.map((name) => ({ label: name, value: c.classes[name] ?? 0, color: CLASS_COLOR[name] }))} total={Math.max(c.total, 1)} />
        </Panel>
        <Panel title="Simulated policy decisions">
          <div className="space-y-3">
            {(["ALLOW", "BLOCK", "REVIEW"] as const).map((d) => (
              <div key={d} className="flex items-center justify-between">
                <DecisionBadge decision={d} />
                <span className="num text-lg font-medium">{fmt(c.decisions[d] ?? 0)}</span>
              </div>
            ))}
          </div>
        </Panel>
        {replay.defense_available && (
          <Panel title="Agent 5 actions" hint="Replay actions from active policies">
            <div className="space-y-3">
              {(["ALLOW", "BLOCK"] as const).map((d) => (
                <div key={d} className="flex items-center justify-between">
                  <DecisionBadge decision={d} />
                  <span className="num text-lg font-medium">{fmt(c.defense[d] ?? 0)}</span>
                </div>
              ))}
              <p className="text-xs text-text-3">{fmt(replay.policies_activated)} policies activated, {fmt(replay.active_policies)} active, {fmt(replay.correlation_groups)} Agent 4 groups.</p>
            </div>
          </Panel>
        )}
        <Panel title="Evidence status">
          {Object.keys(c.evidence).length ? (
            <BarList items={Object.entries(c.evidence).sort((a, b) => b[1] - a[1]).map(([label, value]) => ({ label, value }))} total={Math.max(c.total, 1)} />
          ) : <Empty title="No flows replayed" />}
        </Panel>
      </div>
      <IncidentTimeline incidents={incidents.items} total={incidents.total} scope={incidents.scope} />
      {data.campaign_mode !== "endpoint" && (
        <Panel title="Data limits on this partition">
          <ul className="list-disc ml-4 text-text-2 space-y-1 text-xs">{data.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
        </Panel>
      )}
      <p className="text-[11px] text-text-3"><ClassTag name="BENIGN" /> colours are fixed per class across every screen.</p>
    </div>
  );
}
