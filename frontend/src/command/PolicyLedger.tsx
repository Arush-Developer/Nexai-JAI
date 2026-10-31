// Exactly what Agent 5 decided and what it did: every policy, its window, its evidence and what it really blocked.
import { ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CLASS_COLOR, DecisionBadge, Empty, TableWrap, Tag, fmt, tdCls, thCls } from "../components/ui";
import { useApi } from "../services/api";
import type { DecisionPage, DefenseState, PolicyEvent, PolicyImpact } from "../services/types";
import { Hud } from "./HudPanels";

const BANNER_MS = 9000;

/** Announces a policy at the moment it activates. Ones from before this page opened are not replayed. */
export function PolicyBanner({ event, running }: { event?: PolicyEvent; running: boolean }) {
  const [shown, setShown] = useState<PolicyEvent | null>(null);
  const [seen, setSeen] = useState<number | null>(null);
  useEffect(() => {
    if (!event || event.seq === seen) return;
    setSeen(event.seq);
    if (!running) return;
    setShown(event);
    const timer = setTimeout(() => setShown(null), BANNER_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [event?.seq, running]);
  if (!shown) return null;
  const { policy } = shown;
  return (
    <div className="hud-tag !border-crit max-w-[640px] !p-3 shadow-[0_0_28px_rgba(248,113,113,0.5)]" role="status" data-testid="policy-banner">
      <p className="flex items-center gap-2 font-semibold text-crit text-[13px]"><ShieldAlert size={15} aria-hidden /> A5 activated {policy.policy_id}: BLOCK {policy.scope_type} {policy.scope_value}</p>
      <p className="text-text-2 mt-1 text-[11px] leading-snug">Starts after {policy.activated_after_record_id}, for {fmt(policy.ttl_records)} records, from A4 group {policy.group_id}. {policy.rationale}</p>
    </div>
  );
}

function PolicyWindow({ item, total }: { item: PolicyImpact; total: number }) {
  const start = item.activated_position ?? 0;
  const to = (position: number) => `${(position / Math.max(total, 1)) * 100}%`;
  const ended = item.blocked_positions[item.blocked_positions.length - 1] ?? start;
  return (
    <div>
      <div className="relative h-7 rounded bg-ink-700/60 overflow-hidden" role="img" aria-label={`${item.policy_id}: active from record ${start} of ${total}, blocked ${item.blocked_records} flows`}>
        <div className="absolute inset-y-0 bg-crit/20 border-x border-crit/60" style={{ left: to(start), width: item.expires_after_record_id ? `calc(100% - ${to(start)})` : to(ended - start) }} />
        {item.blocked_positions.map((p) => <span key={p} className="absolute top-1 bottom-1 w-[2px] bg-crit" style={{ left: to(p) }} />)}
        <span className="absolute top-0 bottom-0 w-[2px] bg-warn" style={{ left: to(start) }} />
      </div>
      <p className="flex justify-between gap-2 text-[11px] text-text-3 mt-1 num">
        <span><span className="text-warn">Trigger</span> {item.activated_after_record_id} (record {fmt(start)} of {fmt(total)})</span>
        <span>{item.expires_after_record_id ? <>Expires after {item.expires_after_record_id}</> : "Still active"}</span>
      </p>
    </div>
  );
}

export function PolicyLedger() {
  const { data } = useApi<{ total: number; cursor: number; items: PolicyImpact[] }>("/defense/impact", 2000);
  const defense = useApi<DefenseState>("/defense?member_limit=0&group_limit=1", 5000);
  const items = data?.items ?? [];
  return (
    <Hud title={`Policy Ledger: ${fmt(items.length)} ${items.length === 1 ? "policy" : "policies"} so far`} actions={<span className="text-[11px] text-text-3">Simulated: no firewall or network is touched</span>}>
      {defense.data && !defense.data.available ? <Empty title="Agent 5 is off for this input">{defense.data.unavailable_reason} Run a file with endpoints in the Data Manager, then use Show in Command Centre.</Empty>
        : !items.length ? <Empty title="No policy yet">Agent 5 activates one only when a source's repeated attack calls justify it. The decision log says how close each source is.</Empty> : (
          <div className="grid gap-3">
            {items.map((item) => (
              <article key={item.policy_id} className="rounded-lg border border-crit/30 bg-crit/[0.03] p-3 space-y-3" data-testid={`policy-${item.policy_id}`}>
                <header className="flex flex-wrap items-center gap-2">
                  <span className="text-[15px] font-semibold num text-text-1">{item.policy_id}</span>
                  <Tag tone="crit">{item.policy_action}</Tag>
                  <span className="num font-medium text-text-1">{item.scope_type} = {item.scope_value}</span>
                  <Tag tone={item.status === "ACTIVE" ? "good" : "neutral"}>{item.status}</Tag>
                  <span className="ml-auto text-xs text-text-2 num">Blocked <span className="text-crit font-semibold text-[15px]">{fmt(item.blocked_records)}</span> flows</span>
                </header>
                <PolicyWindow item={item} total={data?.total ?? 0} />
                <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-3 text-xs">
                  <div>
                    <p className="font-medium text-text-2 mb-1">Why A5 made it</p>
                    <p className="text-text-2 leading-relaxed">{item.rationale}</p>
                    <p className="text-text-3 mt-1 num">From A4 group {item.group_id}. Length {fmt(item.ttl_records)} records.{item.first_blocked_record && ` First blocked ${item.first_blocked_record}, last ${item.last_blocked_record}.`}</p>
                  </div>
                  <div>
                    <p className="font-medium text-text-2 mb-1">What it stopped, by attack phase</p>
                    {item.phases.length ? (
                      <table className="w-full num">
                        <thead><tr><th className={thCls}>Day</th><th className={thCls}>A1 called</th><th className={thCls}>Blocked</th><th className={thCls}>Ports</th></tr></thead>
                        <tbody>{item.phases.map((p) => (
                          <tr key={`${p.day}-${p.cls}`}>
                            <td className={tdCls}>{p.day ?? "No timestamp"}</td>
                            <td className={tdCls}><span style={{ color: CLASS_COLOR[p.cls] }}>{p.cls}</span></td>
                            <td className={tdCls}>{fmt(p.flows)}</td>
                            <td className={tdCls}>{fmt(p.ports)}</td>
                          </tr>
                        ))}</tbody>
                      </table>
                    ) : <p className="text-text-3">No matching flow has arrived yet.</p>}
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
    </Hud>
  );
}

export function RecentDecisions() {
  const { data } = useApi<DecisionPage>("/decisions?limit=8", 1500);
  return (
    <Hud title="Latest Decisions" actions={<Link to="/decisions" className="text-[11px] text-text-3 hover:text-accent">Every record →</Link>}>
      {!data?.items.length ? <Empty title="No decisions yet">Press play in Live Replay.</Empty> : (
        <TableWrap>
          <table className="w-full text-xs num" data-testid="recent-decisions">
            <thead><tr>{["Record", "A1", "A5", "Why"].map((h) => <th key={h} className={thCls}>{h}</th>)}</tr></thead>
            <tbody>
              {data.items.map((d) => (
                <tr key={d.flow_id}>
                  <td className={tdCls}>{d.flow_id}</td>
                  <td className={tdCls} style={{ color: CLASS_COLOR[d.final_class] }}>{d.final_class}</td>
                  <td className={tdCls}>{d.defense_action ? <DecisionBadge decision={d.defense_action} /> : <span className="text-text-3">Off</span>}</td>
                  <td className={`${tdCls} !whitespace-normal text-text-2 min-w-[240px]`}>{d.decision_reason ?? "Agents 4 and 5 are off for this input."}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </Hud>
  );
}
