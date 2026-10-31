// Decides which agent the orchestrator watches, from real replay events.
// Agents score whole batches in milliseconds, far too fast to see. So this steps through the newest flow's real
// results, one agent at a time, at a pace a person can follow. Every value shown comes from the event stream.
import { useCallback, useEffect, useRef, useState } from "react";
import type { PolicyEvent, ReplayFlowEvent } from "../services/types";
import type { AgentId } from "./agents";

export const STEP_MS = 800;

export type TraceState = "waiting" | "working" | "done" | "offline";
export interface TraceRow { state: TraceState; value: string }
export type Trace = Record<AgentId, TraceRow>;
export type TraceFlow = Pick<ReplayFlowEvent, "flow_id" | "threat_class" | "confidence" | "anomaly_score" | "is_anomaly" | "final_class" | "evidence_status" | "defense_action" | "applied_policy_id" | "correlation_group_id">;

interface Step { agent: AgentId; value: string; policyFor?: string }

const OFFLINE_NOTE = "Needs source_ip";

const blankTrace = (defense: boolean): Trace => ({
  A1: { state: "waiting", value: "" },
  A2: { state: "waiting", value: "" },
  A3: { state: "waiting", value: "" },
  A4: defense ? { state: "waiting", value: "" } : { state: "offline", value: OFFLINE_NOTE },
  A5: defense ? { state: "waiting", value: "" } : { state: "offline", value: OFFLINE_NOTE },
});

/** What each agent really did for this flow. A blocked flow never reaches Agent 4, so that step is skipped. */
export function stepsForFlow(flow: TraceFlow, defense: boolean): Step[] {
  const steps: Step[] = [
    { agent: "A1", value: `${flow.threat_class} ${(flow.confidence * 100).toFixed(0)}%` },
    { agent: "A2", value: `${flow.anomaly_score.toFixed(2)} ${flow.is_anomaly ? "Anomalous" : "Normal"}` },
    { agent: "A3", value: `${flow.evidence_status}: ${flow.final_class}` },
  ];
  if (!defense || !flow.defense_action) return steps;
  if (flow.defense_action === "BLOCK") {
    steps.push({ agent: "A5", value: `BLOCK by ${flow.applied_policy_id}` });
  } else {
    steps.push({ agent: "A4", value: flow.correlation_group_id ? `Group ${flow.correlation_group_id}` : "No group" });
    steps.push({ agent: "A5", value: "ALLOW, no policy" });
  }
  return steps;
}

export function useAgentFocus(flows: ReplayFlowEvent[], policies: PolicyEvent[], status: string | undefined, defense: boolean) {
  const [focus, setFocus] = useState<AgentId | null>(null);
  const [flow, setFlow] = useState<TraceFlow | null>(null);
  const [trace, setTrace] = useState<Trace>(() => blankTrace(defense));
  const queue = useRef<Step[]>([]);
  const lastFlow = useRef<string | null>(null);
  const lastPolicy = useRef<number | null>(null);
  const live = useRef({ flows, policies, status, defense });
  live.current = { flows, policies, status, defense };

  const begin = useCallback((next: TraceFlow) => {
    const { defense: on } = live.current;
    lastFlow.current = next.flow_id;
    setFlow(next);
    setTrace(blankTrace(on));
    queue.current = stepsForFlow(next, on);
  }, []);

  useEffect(() => { if (!queue.current.length) setTrace((t) => (flow ? t : blankTrace(defense))); }, [defense, flow]);

  useEffect(() => {
    const timer = setInterval(() => {
      const { flows: latest, policies: activated, status: state } = live.current;
      const running = state === "running";
      const policy = activated[0];
      if (policy && policy.seq !== lastPolicy.current) {
        // Policies are rare and matter most, so they jump the queue, but ones from before this page opened do not.
        if (running && lastPolicy.current !== null) {
          queue.current = [
            { agent: "A4", value: `Group ${policy.policy.group_id}` },
            { agent: "A5", value: `${policy.policy.policy_id} activated`, policyFor: policy.policy.scope_value },
          ];
        }
        lastPolicy.current = policy.seq;
      } else if (lastPolicy.current === null) {
        lastPolicy.current = -1;
      }
      if (!queue.current.length && running && latest[0] && latest[0].flow_id !== lastFlow.current) begin(latest[0]);
      const step = queue.current.shift();
      if (!step) {
        setFocus(null);
        setTrace((t) => Object.fromEntries(Object.entries(t).map(([k, r]) => [k, r.state === "working" ? { ...r, state: "done" } : r])) as Trace);
        return;
      }
      setFocus(step.agent);
      setTrace((t) => {
        const next = { ...t };
        for (const id of Object.keys(next) as AgentId[]) if (next[id].state === "working") next[id] = { ...next[id], state: "done" };
        next[step.agent] = { state: "working", value: step.value };
        return next;
      });
    }, STEP_MS);
    return () => clearInterval(timer);
  }, [begin]);

  return { focus, flow, trace, inspect: begin };
}
