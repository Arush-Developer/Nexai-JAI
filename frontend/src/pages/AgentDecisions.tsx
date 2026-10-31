import { Fragment, useState } from "react";
import { CLASS_COLOR, Btn, DecisionBadge, Empty, Notice, Page, Panel, TableWrap, Tag, fmt, tdCls, thCls } from "../components/ui";
import { useApi } from "../services/api";
import type { DecisionPage, DecisionRow } from "../services/types";

const VIEWS = [
  ["all", "All records"], ["blocked", "Blocked"], ["attack_allowed", "Attack calls allowed"], ["conflict", "A1 and A2 disagree"], ["grouped", "In an A4 group"],
] as const;
const PAGE = 50;

/** One record, agent by agent, in the order the pipeline reached it. */
function Walkthrough({ row }: { row: DecisionRow }) {
  const steps: [string, string, string][] = [
    ["A1 Threat Detection", `Called it ${row.threat_class} at ${(row.threat_confidence * 100).toFixed(1)}% confidence.`, "#4da3ff"],
    ["A2 Anomaly Detection", `Scored ${row.anomaly_score.toFixed(3)}, a percentile among benign flows and not a probability of attack. ${row.is_anomaly ? "Flagged as anomalous." : "Not flagged."}`, "#2ee6b8"],
    ["A3 Evidence Fusion", `A1 and A2 ${row.agreement === "AGREE" ? "agree" : "disagree"}. Status ${row.evidence_status}, final class ${row.final_class}.`, "#b57bff"],
    ["A4 Temporal Correlation", row.defense_action === "BLOCK" ? "Skipped: a blocked flow adds no new evidence." : row.correlation_group_id ? `Added to group ${row.correlation_group_id}.` : row.defense_action ? "Not in a group: it was not suspicious, or is the first from its source." : "Off: this input has no endpoints.", "#ffb04d"],
    ["A5 Adaptive Defense", row.decision_reason ?? "Off: this input has no endpoints.", "#ff5c6c"],
  ];
  return (
    <ol className="space-y-1.5 text-xs" data-testid="decision-detail">
      {steps.map(([agent, text, color]) => (
        <li key={agent} className="grid sm:grid-cols-[170px_1fr] gap-x-2">
          <span className="font-medium" style={{ color }}>{agent}</span>
          <span className="text-text-2 !whitespace-normal">{text}</span>
        </li>
      ))}
    </ol>
  );
}

export function AgentDecisions() {
  const [view, setView] = useState<string>("all");
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const query = `/decisions?limit=${PAGE}&offset=${offset}&view=${view}${search.trim() ? `&search=${encodeURIComponent(search.trim())}` : ""}`;
  const { data, error } = useApi<DecisionPage>(query, 2000);
  const counts = data?.counts;
  return (
    <Page title="Agent Decisions" subtitle="What every agent decided for each record replayed so far, and why. Newest first. All blocks are simulated.">
      {error && <Notice tone="crit" title="Could not load decisions">{error}</Notice>}
      <Panel>
        <div className="flex flex-wrap items-center gap-2 mb-3">
          {VIEWS.map(([key, label]) => (
            <Btn key={key} tone={view === key ? "primary" : "default"} onClick={() => { setView(key); setOffset(0); setOpen(null); }}>
              {label} <span className="num opacity-75">{fmt(counts?.[key as keyof typeof counts] ?? 0)}</span>
            </Btn>
          ))}
          <input aria-label="Filter by record or source" placeholder="Filter by record or source IP" value={search} onChange={(e) => { setSearch(e.target.value); setOffset(0); }}
            className="ml-auto bg-ink-800 border border-ink-600 rounded-md px-2 py-1.5 w-56 text-text-1" />
        </div>
        {!data?.items.length ? <Empty title="Nothing here yet">Press play in Live Replay on the Command Centre.</Empty> : (
          <TableWrap>
            <table className="w-full text-xs num" data-testid="decision-rows">
              <thead><tr>{["#", "Record", "Source → destination", "A1 class", "Conf.", "A2 score", "A3 status", "A4 group", "A5 action", "Policy", "Why"].map((h) => <th key={h} className={thCls}>{h}</th>)}</tr></thead>
              <tbody>
                {data.items.map((d) => (
                  <Fragment key={d.flow_id}>
                    <tr onClick={() => setOpen(open === d.flow_id ? null : d.flow_id)} className={`cursor-pointer hover:bg-ink-800 ${d.defense_action === "BLOCK" ? "bg-crit/[0.05]" : ""}`}>
                      <td className={`${tdCls} text-text-3`}>{fmt(d.position)}</td>
                      <td className={`${tdCls} text-accent`}>{d.flow_id}</td>
                      <td className={tdCls}>{d.source_ip ? `${d.source_ip} → ${d.destination_ip}:${d.destination_port ?? "?"}` : <span className="text-text-3">Not in this input</span>}</td>
                      <td className={tdCls} style={{ color: CLASS_COLOR[d.threat_class] }}>{d.threat_class}</td>
                      <td className={tdCls}>{d.threat_confidence.toFixed(3)}</td>
                      <td className={tdCls}>{d.anomaly_score.toFixed(3)}{d.is_anomaly && <span className="text-warn ml-1" title="Flagged anomalous">▲</span>}</td>
                      <td className={`${tdCls} text-text-2`}>{d.evidence_status}</td>
                      <td className={tdCls}>{d.correlation_group_id ?? <span className="text-text-3">–</span>}</td>
                      <td className={tdCls}>{d.defense_action ? <DecisionBadge decision={d.defense_action} /> : <Tag>Off</Tag>}</td>
                      <td className={tdCls}>{d.applied_policy_id || <span className="text-text-3">NONE</span>}</td>
                      <td className={`${tdCls} text-text-2 max-w-[340px] truncate`} title={d.decision_reason ?? undefined}>{d.decision_reason ?? "Agents 4 and 5 are off for this input."}</td>
                    </tr>
                    {open === d.flow_id && <tr><td colSpan={11} className="bg-ink-800/60 p-3"><Walkthrough row={d} /></td></tr>}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
        {data && data.total > PAGE && (
          <div className="flex items-center justify-between mt-3 text-xs text-text-2 num">
            <span>{fmt(offset + 1)} to {fmt(Math.min(offset + PAGE, data.total))} of {fmt(data.total)}</span>
            <span className="flex gap-2">
              <Btn disabled={!offset} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Newer</Btn>
              <Btn disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>Older</Btn>
            </span>
          </div>
        )}
      </Panel>
    </Page>
  );
}
