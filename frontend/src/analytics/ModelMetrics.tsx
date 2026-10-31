import { Component, type ReactNode } from "react";
import { BarList, CLASS_COLOR, Notice, Page, Panel, Stat, TableWrap, fmt, pct, tdCls, thCls } from "../components/ui";
import { useApi } from "../services/api";
import { CLASSES } from "../services/types";
import { AnomalyAnalytics, type Agent2Report } from "./AnomalyAnalytics";
import { ConfusionMatrix, type ConfusionData } from "./ConfusionMatrix";

interface ClassReport {
  rows: number; accuracy: number; macro_f1: number; balanced_accuracy: number;
  binary_attack_false_positive_rate: number; binary_attack_false_negative_rate: number;
  per_class: Record<string, { precision: number; recall: number; f1: number; support: number; false_positive_rate: number; false_negative_rate: number }>;
  confusion_matrix: ConfusionData;
}
interface PolicyOutcome { counts: Record<string, number>; attacks_blocked_rate: number; attacks_allowed_rate: number; attacks_reviewed_rate: number; benign_allowed_rate: number; benign_blocked_rate: number; benign_reviewed_rate: number }
interface Connected {
  input: string; rows: number; labelled: boolean; model_version: string; anomaly_detector: string;
  by_class: Record<string, { flows: number; flagged: number; median_anomaly_score: number }>;
  evidence: Record<string, number>; agent_agreement_rate: number; anomaly_flagged: number; anomaly_threshold: number;
  confidence_min: number; confidence_median: number; low_confidence_flows: number;
  evaluation: ClassReport | null;
  anomaly_evaluation: { roc_auc: number; recall: number; false_positive_rate: number } | null;
}
interface Metrics {
  connected: Connected;
  evaluation_report: {
    model_version: string; anomaly_detector: string; agent_agreement_rate: number;
    agent1: ClassReport; system: ClassReport;
    agent1_excluding_rows_also_in_training: ClassReport & { excluded_rows: number };
    agent2: Agent2Report;
    ablation: Record<string, { macro_f1: number; accuracy: number; policy: PolicyOutcome } | string>;
    evaluation_modes?: Record<string, {
      accuracy: number; macro_f1: number; false_positive_rate?: number; false_negative_rate?: number;
      agent_agreement_rate?: number | null; latency_ms_per_1k_flows?: number; replay_analysis_flows_per_second?: number;
      policy: PolicyOutcome & { counts: Record<string, number> };
    }>;
    investigation: { triggered: number; investigated: number; verdicts: Record<string, number> };
    latency: Record<string, number>;
  } | null;
  training_report: {
    model_comparison_selection_split: Record<string, { macro_f1: number; accuracy: number; balanced_accuracy: number; fit_seconds: number; inference_ms_per_1k_flows: number }>;
    selected_model: string; feature_importance: { feature: string; importance: number }[];
    record_order_validation: ClassReport;
    destination_port_study: { with_port_macro_f1: number; without_port: { macro_f1: number }; use_port: boolean };
    calibration_study: { raw: { ece_15_bins: number }; temperature_scaled: { ece_15_bins: number }; chosen_temperature: number };
  } | null;
  leakage_audit: { cumulative_top_feature_knockout: { removed: number; macro_f1: number }[] } | null;
  dataset: Record<string, { rows: number; label_distribution: Record<string, number> }> | null;
}

const MODE_ROWS = [
  ["mode_a_classifier_alone", "A: classifier alone"],
  ["mode_b_with_anomaly_and_fusion", "B: + anomaly and fusion"],
  ["mode_c_complete_system", "C: complete system, batch"],
  ["mode_c_causal_replay", "C: complete system, causal replay"],
] as const;

const LAYER_NAMES: Record<string, string> = {
  agent1_alone: "Agent 1 alone", agent1_plus_agent2: "+ Agent 2 anomaly", plus_campaign_intelligence: "+ Campaign intelligence", plus_investigation: "+ Investigation",
};

/** A damaged report must not take the whole screen down with it. */
class ReportGuard extends Component<{ children: ReactNode }, { failed: string | null }> {
  state = { failed: null as string | null };
  static getDerivedStateFromError(error: Error) { return { failed: error.message }; }
  render() {
    if (this.state.failed) return <Notice title="These saved reports could not be shown">{this.state.failed}. Rebuild them with scripts/run_all.sh. Results on your connected data above are not affected.</Notice>;
    return this.props.children;
  }
}

/** Results on the data connected right now. Accuracy needs labels, so an unlabelled file says so instead. */
function ConnectedResults({ c }: { c: Connected }) {
  const e = c.evaluation;
  return (
    <>
      {e ? (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3" data-testid="connected-accuracy">
            <Stat label="Accuracy" value={e.accuracy.toFixed(5)} hint={`${fmt(e.rows)} labelled records`} />
            <Stat label="Macro-F1" value={e.macro_f1.toFixed(5)} />
            <Stat label="Balanced accuracy" value={e.balanced_accuracy.toFixed(5)} />
            <Stat label="False-positive rate" value={pct(e.binary_attack_false_positive_rate, 4)} hint="Benign called attack" />
            <Stat label="False-negative rate" value={pct(e.binary_attack_false_negative_rate, 4)} hint="Attack called benign" />
          </div>
          <div className="grid lg:grid-cols-2 gap-3">
            <Panel title="Per-class performance"><PerClass report={e} /></Panel>
            <Panel title="Confusion matrix" hint="Outlined cells are errors. Shading is logarithmic."><ConfusionMatrix matrix={e.confusion_matrix} /></Panel>
          </div>
          {c.anomaly_evaluation && <p className="text-xs text-text-2 num">A2 on this file: ROC-AUC {c.anomaly_evaluation.roc_auc.toFixed(4)}, recall {pct(c.anomaly_evaluation.recall)} at a {pct(c.anomaly_evaluation.false_positive_rate)} false-alarm rate.</p>}
        </>
      ) : (
        <Notice tone="info" title="Accuracy can't be measured on this file: it has no labels">
          <p data-testid="no-labels">Accuracy compares predictions with true answers, and this file doesn't contain them. For the hidden set, only the organisers can score it: upload the three files on their <a className="underline text-accent" href="https://c-thon.vercel.app/cyber/L2" target="_blank" rel="noreferrer">stage page</a>, and the score appears there. Below is what the agents actually did. To see accuracy here, connect the labelled validation file instead.</p>
        </Notice>
      )}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="Records" value={fmt(c.rows)} />
        <Stat label="A1 and A2 agree" value={pct(c.agent_agreement_rate, 1)} />
        <Stat label="A2 flagged" value={fmt(c.anomaly_flagged)} hint={`At percentile ${c.anomaly_threshold}, not a probability`} />
        <Stat label="A1 lowest confidence" value={c.confidence_min.toFixed(4)} hint={`${fmt(c.low_confidence_flows)} flows below 0.90`} />
      </div>
      <div className="grid lg:grid-cols-2 gap-3">
        <Panel title="What A1 called, and what A2 flagged" hint="Calls, not checked against truth unless the file has labels.">
          <TableWrap>
            <table className="w-full num" data-testid="connected-classes">
              <thead><tr>{["A1 called", "Flows", "A2 flagged", "A2 median score"].map((h) => <th key={h} className={thCls}>{h}</th>)}</tr></thead>
              <tbody>{Object.entries(c.by_class).map(([name, r]) => (
                <tr key={name}>
                  <td className={tdCls} style={{ color: CLASS_COLOR[name] }}>{name}</td>
                  <td className={tdCls}>{fmt(r.flows)}</td>
                  <td className={tdCls}>{fmt(r.flagged)}</td>
                  <td className={tdCls}>{r.median_anomaly_score.toFixed(3)}</td>
                </tr>
              ))}</tbody>
            </table>
          </TableWrap>
        </Panel>
        <Panel title="A3 evidence">
          <BarList items={Object.entries(c.evidence).sort((a, b) => b[1] - a[1]).map(([label, value]) => ({ label, value }))} total={Math.max(c.rows, 1)} />
        </Panel>
      </div>
    </>
  );
}

function PerClass({ report }: { report: ClassReport }) {
  return (
    <TableWrap>
      <table className="w-full num">
        <thead><tr>{["Class", "Precision", "Recall", "F1", "FPR", "FNR", "Support"].map((h) => <th key={h} className={thCls}>{h}</th>)}</tr></thead>
        <tbody>
          {CLASSES.map((c) => {
            const r = report.per_class[c];
            return (
              <tr key={c}>
                <td className={tdCls}>{c}</td>
                <td className={tdCls}>{r.precision.toFixed(4)}</td>
                <td className={tdCls}>{r.recall.toFixed(4)}</td>
                <td className={tdCls}>{r.f1.toFixed(4)}</td>
                <td className={tdCls}>{r.false_positive_rate?.toFixed(5) ?? "–"}</td>
                <td className={tdCls}>{r.false_negative_rate?.toFixed(5) ?? "–"}</td>
                <td className={tdCls}>{fmt(r.support)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableWrap>
  );
}

export function ModelMetrics() {
  const { data, error } = useApi<Metrics>("/metrics");
  if (error) return <Page title="Model Intelligence"><Notice tone="crit" title="Could not load metrics">{error}</Notice></Page>;
  if (!data) return <Page title="Model Intelligence"><p className="text-text-3">Loading…</p></Page>;
  if (!data.connected) return <Page title="Model Intelligence"><Notice tone="crit" title="The backend is out of date">Restart the app: press Ctrl+C in its terminal, then run ./scripts/run_local.sh.</Notice></Page>;
  return (
    <Page title="Model Intelligence" subtitle={`Results on the connected data: ${data.connected.input}. Model: ${data.connected.model_version}.`}>
      <ConnectedResults c={data.connected} />
      <details className="rounded-lg border border-ink-700 bg-ink-850 p-4" data-testid="reference-results">
        <summary className="cursor-pointer font-medium text-text-1">How the saved models were tested before this run <span className="text-text-3 font-normal">(separate labelled records, not your connected data)</span></summary>
        <div className="mt-4 space-y-3"><ReportGuard><ReferenceResults data={data} /></ReportGuard></div>
      </details>
    </Page>
  );
}

function ReferenceResults({ data }: { data: Metrics }) {
  const ev = data.evaluation_report;
  const tr = data.training_report;
  if (!ev || !tr) return <Notice title="Reports not generated yet">Run scripts/train_models.py, scripts/calibrate_fusion.py and scripts/evaluate.py.</Notice>;
  const sys = ev.system;

  const dist = data.dataset?.eval.label_distribution ?? {};

  return (
    <>
      <p className="text-xs text-text-2 num">Measured on {fmt(sys.rows)} labelled validation records, which were not used for tuning. Model: {ev.model_version}.</p>
      <Notice title="Near-perfect scores describe this capture, not networks in general">
        <p>Each attack class in this dataset comes from tools with almost constant TCP fingerprints. Removing the 25 strongest features still left macro-F1 at {data.leakage_audit ? data.leakage_audit.cumulative_top_feature_knockout.at(-1)!.macro_f1.toFixed(4) : "–"}, so no single feature is leaking the label.</p>
        <p>Fitting on early records and testing on late ones lowered macro-F1 to {tr.record_order_validation.macro_f1.toFixed(4)}, with DOS recall at {tr.record_order_validation.per_class.DOS.recall.toFixed(4)}. Expect lower scores on traffic from elsewhere.</p>
      </Notice>
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <Stat label="Accuracy" value={sys.accuracy.toFixed(5)} />
        <Stat label="Macro-F1" value={sys.macro_f1.toFixed(5)} hint={`${ev.agent1_excluding_rows_also_in_training.macro_f1.toFixed(5)} without ${fmt(ev.agent1_excluding_rows_also_in_training.excluded_rows)} rows also in training`} />
        <Stat label="Balanced accuracy" value={sys.balanced_accuracy.toFixed(5)} />
        <Stat label="False-positive rate" value={pct(sys.binary_attack_false_positive_rate, 4)} hint="Benign called attack" />
        <Stat label="False-negative rate" value={pct(sys.binary_attack_false_negative_rate, 4)} hint="Attack called benign" />
      </div>
      <div className="grid lg:grid-cols-2 gap-3">
        <Panel title="Per-class performance"><PerClass report={sys} /></Panel>
        <Panel title="Confusion matrix" hint="Outlined cells are errors. Shading is logarithmic.">
          <ConfusionMatrix matrix={sys.confusion_matrix} />
        </Panel>
      </div>
      <div className="grid lg:grid-cols-2 gap-3">
        <Panel title="Model comparison" hint="Internal selection split, not the validation records">
          <TableWrap>
            <table className="w-full num">
              <thead><tr>{["Model", "Macro-F1", "Accuracy", "Fit s", "ms / 1k flows"].map((h) => <th key={h} className={thCls}>{h}</th>)}</tr></thead>
              <tbody>
                {Object.entries(tr.model_comparison_selection_split).sort((a, b) => b[1].macro_f1 - a[1].macro_f1).map(([name, m]) => (
                  <tr key={name}>
                    <td className={tdCls}>{name}{name === tr.selected_model && <span className="text-accent ml-1.5 text-[11px]">selected</span>}</td>
                    <td className={tdCls}>{m.macro_f1.toFixed(5)}</td>
                    <td className={tdCls}>{m.accuracy.toFixed(5)}</td>
                    <td className={tdCls}>{m.fit_seconds}</td>
                    <td className={tdCls}>{m.inference_ms_per_1k_flows}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          <p className="text-xs text-text-3 mt-3">
            Destination Port: macro-F1 {tr.destination_port_study.with_port_macro_f1.toFixed(5)} with it, {tr.destination_port_study.without_port.macro_f1.toFixed(5)} without. {tr.destination_port_study.use_port ? "Kept." : "Dropped because it added nothing."}{" "}
            Calibration error (ECE) {tr.calibration_study.raw.ece_15_bins.toFixed(5)} raw, {tr.calibration_study.temperature_scaled.ece_15_bins.toFixed(5)} at temperature {tr.calibration_study.chosen_temperature}.
          </p>
        </Panel>
        <Panel title="Feature importance" hint={`Top 12 from ${tr.selected_model}`}>
          <BarList items={tr.feature_importance.slice(0, 12).map((f) => ({ label: f.feature, value: +f.importance.toFixed(2) }))} />
        </Panel>
      </div>
      <AnomalyAnalytics report={ev.agent2} detector={ev.anomaly_detector} />
      <Panel title="Ablation: what each layer changes" hint="Classification does not move, because final_class stays with Agent 1. The layers change how decisions are routed, at the cost of more review.">
        <TableWrap>
          <table className="w-full num">
            <thead><tr>{["Layers", "Macro-F1", "Attacks blocked", "Attacks auto-allowed", "Benign auto-allowed", "Benign blocked", "Benign sent to review"].map((h) => <th key={h} className={thCls}>{h}</th>)}</tr></thead>
            <tbody>
              {Object.entries(ev.ablation).flatMap(([name, layer]) => typeof layer === "string" ? [] : [(
                <tr key={name}>
                  <td className={tdCls}>{LAYER_NAMES[name] ?? name}</td>
                  <td className={tdCls}>{layer.macro_f1.toFixed(5)}</td>
                  <td className={tdCls}>{pct(layer.policy.attacks_blocked_rate)}</td>
                  <td className={tdCls}>{pct(layer.policy.attacks_allowed_rate, 4)}</td>
                  <td className={tdCls}>{pct(layer.policy.benign_allowed_rate)}</td>
                  <td className={tdCls}>{pct(layer.policy.benign_blocked_rate, 4)}</td>
                  <td className={tdCls}>{pct(layer.policy.benign_reviewed_rate)}</td>
                </tr>
              )])}
            </tbody>
          </table>
        </TableWrap>
      </Panel>
      {ev.evaluation_modes && (
        <Panel title="Evaluation modes" hint="The same official records through each configuration. Mode C causal is the live replay, which only ever looks backwards.">
          <TableWrap>
            <table className="w-full num">
              <thead><tr>{["Mode", "Accuracy", "Macro-F1", "False-positive rate", "False-negative rate", "Agent agreement", "Allow / block / review", "Speed"].map((h) => <th key={h} className={thCls}>{h}</th>)}</tr></thead>
              <tbody>
                {MODE_ROWS.map(([key, label]) => {
                  const mode = ev.evaluation_modes![key];
                  return (
                    <tr key={key}>
                      <td className={tdCls}>{label}</td>
                      <td className={tdCls}>{mode.accuracy.toFixed(5)}</td>
                      <td className={tdCls}>{mode.macro_f1.toFixed(5)}</td>
                      <td className={tdCls}>{pct(mode.false_positive_rate ?? null, 4)}</td>
                      <td className={tdCls}>{pct(mode.false_negative_rate ?? null, 4)}</td>
                      <td className={tdCls}>{mode.agent_agreement_rate == null ? "n/a" : pct(mode.agent_agreement_rate, 1)}</td>
                      <td className={tdCls}>{fmt(mode.policy.counts.ALLOW)} / {fmt(mode.policy.counts.BLOCK)} / {fmt(mode.policy.counts.REVIEW)}</td>
                      <td className={tdCls}>{mode.replay_analysis_flows_per_second ? `${fmt(mode.replay_analysis_flows_per_second)} flows/s` : `${mode.latency_ms_per_1k_flows?.toFixed(2)} ms per 1k flows`}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableWrap>
        </Panel>
      )}
      <div className="grid lg:grid-cols-2 gap-3">
        <Panel title="Validation class distribution">
          <BarList items={CLASSES.map((c) => ({ label: c, value: dist[c] ?? 0, color: CLASS_COLOR[c] }))} />
        </Panel>
        <Panel title="Operations">
          <dl className="grid grid-cols-2 gap-3 text-xs">
            <div><dt className="text-text-3">Agent agreement</dt><dd className="num text-base">{pct(ev.agent_agreement_rate, 1)}</dd></div>
            <div><dt className="text-text-3">Investigation triggers</dt><dd className="num text-base">{fmt(ev.investigation.triggered)}</dd></div>
            <div><dt className="text-text-3">Agent 1 latency</dt><dd className="num text-base">{ev.latency.agent1_ms_per_1k_flows.toFixed(2)} ms / 1k</dd></div>
            <div><dt className="text-text-3">Agent 2 latency</dt><dd className="num text-base">{ev.latency.agent2_ms_per_1k_flows.toFixed(2)} ms / 1k</dd></div>
            <div><dt className="text-text-3">Pipeline throughput</dt><dd className="num text-base">{fmt(Math.round(ev.latency.scoring_campaign_fusion_policy_flows_per_second))} flows/s</dd></div>
            <div><dt className="text-text-3">Investigation verdicts</dt><dd className="text-text-2">{Object.entries(ev.investigation.verdicts).map(([k, v]) => `${k}: ${fmt(v)}`).join(", ")}</dd></div>
          </dl>
        </Panel>
      </div>
    </>
  );
}
