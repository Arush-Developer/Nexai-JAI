import { CheckCircle2, CircleDashed, FolderOpen, FolderOutput, Loader2, MinusCircle, XCircle } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Btn, Empty, Notice, Page, Panel, Stat, TableWrap, Tag, fmt, tdCls, thCls } from "../components/ui";
import { DATA_CONNECTED, post, useApi } from "../services/api";
import {
  ExistingRunFolder, entriesFromInput, exportRun, folderApiAvailable, pickInputFolder, pickOutputFolder, uploadFiles, type LocalEntry,
} from "../services/localData";
import type { LocalAgentState, LocalInspection, LocalInspectionFile, LocalPolicyEvent, LocalRun, LocalStatus, LocalUpload, LocalValidation } from "../services/types";

const TYPES = [
  { value: "hidden", label: "Day 2 hidden replay data" },
  { value: "validation", label: "Labelled validation data" },
  { value: "training", label: "Training data (inspect only)" },
];
const AGENTS: Record<string, string> = {
  A1: "A1 Threat Detection", A2: "A2 Anomaly Detection", A3: "A3 Evidence Fusion", A4: "A4 Temporal Correlation", A5: "A5 Adaptive Defence",
};
const STAGES: Record<string, string> = {
  queued: "Queued", loading: "Loading the selected input", scoring: "Agents 1 to 3 scoring every record",
  replay: "Chronological replay: Agents 4 and 5", causality: "Rerunning on prefixes to prove no future record leaked in",
  writing: "Writing output files", validating: "Reading the files back and checking every rule",
  completed: "Completed", failed: "Failed", not_started: "Not started",
};

const size = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
};

function Yes({ on, children }: { on: boolean; children: React.ReactNode }) {
  return <Tag tone={on ? "good" : "warn"}>{children}: {on ? "yes" : "no"}</Tag>;
}

function FileReport({ file, included, toggle, disabled }: { file: LocalInspectionFile; included: boolean; toggle: () => void; disabled: boolean }) {
  return (
    <li className="rounded-md border border-ink-700 p-3 space-y-2">
      <label className="flex items-start gap-2 font-medium min-w-0">
        <input type="checkbox" className="mt-1" checked={included} disabled={disabled} onChange={toggle} aria-label={`Include ${file.display}`} />
        <span className="break-all">{file.display}</span>
        <Tag tone={file.errors.length ? "crit" : "good"}>{file.errors.length ? "invalid" : "valid"}</Tag>
        <Tag>{file.role === "ordering" ? "ordering file" : file.role === "flows" ? "flow file" : "unrecognised"}</Tag>
      </label>
      {file.columns > 0 && (
        <>
          <p className="text-xs text-text-2 num">
            {fmt(file.records)} records, {fmt(file.columns)} columns, {fmt(file.feature_count)} recognised features, {fmt(file.missing_values)} missing values,
            {" "}{fmt(file.positive_infinity)} +inf, {fmt(file.negative_infinity)} -inf, {fmt(file.duplicate_records)} duplicate records, {fmt(file.duplicate_columns?.length ?? 0)} duplicate column groups.
          </p>
          <div className="flex flex-wrap gap-1">
            <Yes on={!!file.record_id_column}>Record ID</Yes>
            <Yes on={!!file.timestamp_column}>Timestamp</Yes>
            <Yes on={!!file.source_ip_available}>Source IP</Yes>
            <Yes on={!!file.destination_ip_available}>Destination IP</Yes>
            <Yes on={!!file.source_port_available}>Source port</Yes>
            <Yes on={!!file.destination_port_available}>Destination port</Yes>
            <Yes on={!!file.protocol_available}>Protocol</Yes>
            <Tag tone={file.label_column ? "accent" : "neutral"}>Label column: {file.label_column ?? "none"}</Tag>
          </div>
          {file.timestamp_order?.available && (
            <p className="text-xs text-text-3 num">Timestamps {file.timestamp_order.first} to {file.timestamp_order.last}. {fmt(file.timestamp_order.unreadable)} unreadable, {fmt(file.timestamp_order.backward_steps)} step backwards.</p>
          )}
          {!!file.unrecognised_columns?.length && <p className="text-xs text-text-3 break-words">Unrecognised columns, not used by any agent: {file.unrecognised_columns.join(", ")}</p>}
          <details className="text-xs text-text-3">
            <summary className="cursor-pointer">Columns and types ({file.columns})</summary>
            <p className="mt-1 break-words">{file.column_names?.map((c) => `${c} (${file.dtypes?.[c]})`).join(", ")}</p>
          </details>
        </>
      )}
      {file.errors.map((e) => <p key={e} className="text-xs text-crit break-words">{e}</p>)}
      {file.warnings.map((w) => <p key={w} className="text-xs text-warn break-words">{w}</p>)}
    </li>
  );
}

function AgentCard({ id, agent }: { id: string; agent?: LocalAgentState }) {
  const state = agent?.state ?? "waiting";
  const Icon = state === "done" ? CheckCircle2 : state === "running" ? Loader2 : state === "unavailable" ? MinusCircle : CircleDashed;
  return (
    <div className="rounded-md border border-ink-700 p-3 min-w-0" data-testid={`agent-${id}`}>
      <p className="text-xs text-text-3 flex items-center gap-1.5">
        <Icon size={13} aria-hidden className={state === "running" ? "animate-spin text-accent" : state === "done" ? "text-good" : state === "unavailable" ? "text-warn" : ""} />
        {AGENTS[id]}
      </p>
      <p className="font-medium capitalize">{state}</p>
      <p className="text-xs text-text-2 break-words">{agent?.detail}</p>
    </div>
  );
}

export function DataManager() {
  const { data: status, error: statusError } = useApi<LocalStatus>("/local-data/status");
  const navigate = useNavigate();
  const connection = useApi<{ status: string }>("/health", 3000);
  const [opening, setOpening] = useState(false);
  const [connectedRun, setConnectedRun] = useState<string | null>(null);
  const apiAvailable = useMemo(folderApiAvailable, []);
  const fallbackInput = useRef<HTMLInputElement>(null);

  const [folder, setFolder] = useState<string | null>(null);
  const [entries, setEntries] = useState<LocalEntry[]>([]);
  const [datasetType, setDatasetType] = useState("");
  const [upload, setUpload] = useState<LocalUpload | null>(null);
  const [uploadedFrom, setUploadedFrom] = useState("");
  const [uploadShare, setUploadShare] = useState<number | null>(null);
  const [inspection, setInspection] = useState<LocalInspection | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [acceptOrder, setAcceptOrder] = useState(false);
  const [acceptRepeat, setAcceptRepeat] = useState(false);

  const [run, setRun] = useState<LocalRun | null>(null);
  const [agents, setAgents] = useState<Record<string, LocalAgentState>>({});
  const [progress, setProgress] = useState<{ processed: number; total: number; stage: string; groups?: number; policies?: number; active?: number; expired?: number } | null>(null);
  const [policyEvents, setPolicyEvents] = useState<LocalPolicyEvent[]>([]);
  const stream = useRef<EventSource | null>(null);

  const [outputDir, setOutputDir] = useState<FileSystemDirectoryHandle | null>(null);
  const [exportState, setExportState] = useState<Record<string, string>>({});
  const [exporting, setExporting] = useState(false);
  const [exportedTo, setExportedTo] = useState<string | null>(null);
  const [existingFolder, setExistingFolder] = useState<string | null>(null);
  const [validation, setValidation] = useState<LocalValidation | null>(null);
  const [validating, setValidating] = useState(false);

  useEffect(() => () => stream.current?.close(), []);

  const chosen = entries.filter((e) => e.selected);
  const signature = `${datasetType}|${chosen.map((e) => `${e.relativePath}:${e.file.size}:${e.file.lastModified}`).join("|")}`;
  const stale = upload !== null && uploadedFrom !== signature;
  const running = run?.state === "queued" || run?.state === "running";
  const job = !stale ? inspection?.job : undefined;
  const finished = run?.state === "completed";

  const resetRun = () => {
    stream.current?.close();
    setRun(null); setProgress(null); setAgents({}); setPolicyEvents([]); setValidation(null);
    setExportState({}); setExportedTo(null); setExistingFolder(null);
  };

  const connected = (name: string, found: LocalEntry[]) => {
    setFolder(name); setEntries(found); setUpload(null); setInspection(null); setProblem(null); resetRun();
    setAcceptOrder(false); setAcceptRepeat(false);
  };

  const connectFolder = async () => {
    setProblem(null);
    if (!apiAvailable) { fallbackInput.current?.click(); return; }
    try {
      const picked = await pickInputFolder();
      if (picked) connected(picked.name, picked.entries);
    } catch (e) { setProblem(`Could not read that folder: ${(e as Error).message}`); }
  };

  const inspect = async (include?: string[]) => {
    setProblem(null); setInspecting(true);
    try {
      let current = upload;
      if (!current || stale) {
        resetRun();
        setUploadShare(0);
        current = await uploadFiles(chosen, datasetType, setUploadShare);
        setUpload(current); setUploadedFrom(signature);
        include = undefined;
      }
      setInspection(await post<LocalInspection>("/local-data/inspect", { run_id: current.run_id, include: include ?? null }));
    } catch (e) { setProblem((e as Error).message); setInspection(null); }
    finally { setInspecting(false); setUploadShare(null); }
  };

  const toggleFile = (id: string) => {
    const now = new Set(job?.selected ?? []);
    if (now.has(id)) now.delete(id); else now.add(id);
    inspect([...now]);
  };

  const refresh = useCallback(async (runId: string) => {
    const response = await fetch(`/api/local-data/runs/${runId}`);
    const view = (await response.json()) as LocalRun;
    setRun(view);
    if (view.validation) setValidation(view.validation);
    if (view.agents && Object.keys(view.agents).length) setAgents(view.agents);
    // A finished run becomes what every screen shows, so nothing else needs connecting.
    if (view.state === "completed") {
      await post(`/local-data/runs/${runId}/open-in-dashboard`).then(() => { setConnectedRun(runId); window.dispatchEvent(new Event(DATA_CONNECTED)); }).catch((e: Error) => setProblem(e.message));
    }
  }, []);

  const startRun = async () => {
    if (!upload) return;
    setProblem(null); resetRun();
    try {
      const started = await post<LocalRun>("/local-data/run", {
        run_id: upload.run_id, include: job?.selected ?? null,
        on_order_conflict: acceptOrder ? "report" : null, allow_duplicate: acceptRepeat,
      });
      setRun(started);
      const source = new EventSource(`/api/local-data/runs/${upload.run_id}/events`);
      stream.current = source;
      source.addEventListener("agent", (e) => {
        const { agent, state, detail } = JSON.parse((e as MessageEvent).data);
        setAgents((a) => ({ ...a, [agent]: { state, detail } }));
      });
      source.addEventListener("stage", (e) => {
        const { stage } = JSON.parse((e as MessageEvent).data);
        setProgress((p) => ({ processed: p?.processed ?? 0, total: p?.total ?? started.total, ...p, stage }));
      });
      source.addEventListener("progress", (e) => {
        const body = JSON.parse((e as MessageEvent).data);
        setProgress(body);
        if (body.agents) setAgents(body.agents);
      });
      source.addEventListener("policy", (e) => {
        const event = JSON.parse((e as MessageEvent).data) as LocalPolicyEvent;
        if (event.event === "ACTIVATED" || event.event === "EXPIRED") setPolicyEvents((list) => [event, ...list].slice(0, 200));
      });
      source.addEventListener("closed", () => { source.close(); void refresh(upload.run_id); });
      source.onerror = () => { source.close(); void refresh(upload.run_id); };
    } catch (e) { setProblem((e as Error).message); }
  };

  const chooseOutput = async () => {
    setProblem(null);
    try {
      const picked = await pickOutputFolder();
      if (picked) { setOutputDir(picked); setExportState({}); setExportedTo(null); setExistingFolder(null); }
    } catch (e) { setProblem(`Could not open that folder: ${(e as Error).message}`); }
  };

  const doExport = async (overwrite: boolean) => {
    if (!outputDir || !run) return;
    setProblem(null); setExporting(true); setExistingFolder(null); setExportedTo(null);
    const names = run.outputs.map((f) => f.name);
    setExportState(Object.fromEntries(names.map((n) => [n, "waiting"])));
    try {
      await exportRun(outputDir, run.run_id, names, overwrite, (name, state) => setExportState((s) => ({ ...s, [name]: state })));
      setExportedTo(`${outputDir.name}/${run.run_id}`);
    } catch (e) {
      if (e instanceof ExistingRunFolder) { setExistingFolder(run.run_id); setExportState({}); }
      else setProblem(`Export failed, so not every file was saved: ${(e as Error).message}`);
    } finally { setExporting(false); }
  };

  const showInCommandCentre = async () => {
    setProblem(null); setOpening(true);
    try {
      if (connectedRun !== run!.run_id) await post(`/local-data/runs/${run!.run_id}/open-in-dashboard`);
      window.dispatchEvent(new Event(DATA_CONNECTED));
      navigate("/command");
    } catch (e) { setProblem((e as Error).message); setOpening(false); }
  };

  const downloadZip = () => {
    const link = document.createElement("a");
    link.href = `/api/local-data/runs/${run!.run_id}/archive`;
    link.download = `${run!.run_id}_outputs.zip`;
    link.click();
  };

  const validate = async () => {
    if (!run) return;
    setProblem(null); setValidating(true);
    try { setValidation(await post<LocalValidation>(`/local-data/runs/${run.run_id}/validate`)); }
    catch (e) { setProblem((e as Error).message); }
    finally { setValidating(false); }
  };

  const canRun = !!job?.runnable && !!status?.ready && !running && !finished && (!job.order_conflict || acceptOrder)
    && (!upload?.duplicate_of.length || acceptRepeat || !!inspection);
  const processed = progress?.processed ?? run?.processed ?? 0;
  const total = progress?.total || run?.total || job?.records || 0;

  return (
    <Page title="Data Manager" subtitle="Choose your own local folders. Files go only from this browser to the backend on this computer, and nothing is uploaded anywhere else.">
      {connection.data?.status === "no_data" && !run && (
        <Notice tone="info" title="Start here: no data is connected">Nothing is preloaded. Connect a folder and run the pipeline, and every screen will then show results from your data.</Notice>
      )}
      {connectedRun && <Notice tone="info" title={`Every screen now shows ${connectedRun}`}>Model Lab shows its results. Accuracy appears only if the file has labels to compare with.</Notice>}
      {statusError && <Notice tone="crit" title="Backend unreachable">{statusError}</Notice>}
      {status && !status.ready && <Notice tone="crit" title="Saved models are not ready">{status.error}</Notice>}
      {problem && <Notice tone="crit" title="That did not work">{problem}</Notice>}

      {status?.ready && (
        <Panel title="Model readiness" hint="Reported by the backend from the saved artifacts.">
          <div className="grid sm:grid-cols-2 lg:grid-cols-5 gap-3 text-xs">
            {Object.entries(status.agents).map(([id, detail]) => (
              <div key={id} className="min-w-0"><p className="text-text-3">{AGENTS[id]}</p><p className="text-text-1 break-words">{detail}</p></div>
            ))}
          </div>
        </Panel>
      )}

      <Panel title="Section A: input source" hint={apiAvailable ? "Uses the browser's folder picker. Your folder's full path is never seen by this app." : "This browser has no folder API, so the operating system's folder picker is used instead."}
        actions={<Btn tone="primary" onClick={connectFolder}><FolderOpen size={14} aria-hidden /> CONNECT LOCAL INPUT FOLDER</Btn>}>
        <input ref={fallbackInput} data-testid="folder-fallback" type="file" multiple className="hidden"
          {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
          onChange={(e) => { if (e.target.files?.length) { const p = entriesFromInput(e.target.files); connected(p.name, p.entries); } e.target.value = ""; }} />
        {!folder ? <Empty title="No folder connected">Connect the folder that holds the organisers' CSV, ZIP or Parquet files.</Empty> : (
          <div className="space-y-3">
            <p className="flex flex-wrap items-center gap-2"><Tag tone="good">Connected</Tag><span className="font-medium break-all" data-testid="input-folder">{folder}</span><span className="text-text-3 text-xs">{fmt(entries.length)} data files found</span></p>
            {entries.length === 0 ? <Empty title="No CSV, ZIP or Parquet files in this folder" /> : (
              <TableWrap>
                <table className="w-full">
                  <thead><tr><th className={thCls}>Use</th><th className={thCls}>File</th><th className={thCls}>Location</th><th className={thCls}>Type</th><th className={thCls}>Size</th><th className={thCls}>Validation</th></tr></thead>
                  <tbody>
                    {entries.map((entry) => {
                      const uploaded = !stale && upload?.files.find((f) => f.relative_path === entry.relativePath);
                      const reports = uploaded && inspection ? inspection.files.filter((f) => f.id === uploaded.file_id || f.id.startsWith(`${uploaded.file_id}.`)) : [];
                      const bad = reports.some((r) => r.errors.length);
                      return (
                        <tr key={entry.id}>
                          <td className={tdCls}><input type="checkbox" checked={entry.selected} aria-label={`Select ${entry.relativePath}`}
                            onChange={() => setEntries((list) => list.map((e) => e.id === entry.id ? { ...e, selected: !e.selected } : e))} /></td>
                          <td className={tdCls}>{entry.file.name}</td>
                          <td className={`${tdCls} text-text-3`}>{entry.relativePath.includes("/") ? entry.relativePath.slice(0, entry.relativePath.lastIndexOf("/")) : "."}</td>
                          <td className={tdCls}>{entry.kind}</td>
                          <td className={`${tdCls} num`}>{size(entry.file.size)}</td>
                          <td className={tdCls}>{reports.length ? <Tag tone={bad ? "crit" : "good"}>{bad ? "invalid" : "valid"}</Tag> : <Tag>{entry.selected ? "not inspected" : "not selected"}</Tag>}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </TableWrap>
            )}
            <div className="flex flex-wrap items-end gap-3">
              <label className="text-xs text-text-2">What are the selected files?
                <select aria-label="Dataset type" value={datasetType} onChange={(e) => setDatasetType(e.target.value)} className="block mt-1 bg-ink-800 border border-ink-600 rounded-md px-2 py-1.5 text-text-1">
                  <option value="">Choose a dataset type</option>
                  {TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </label>
              <Btn tone="primary" disabled={!chosen.length || !datasetType || inspecting || running || !status?.ready} onClick={() => inspect()}>
                {inspecting && <Loader2 size={14} className="animate-spin" aria-hidden />} INSPECT DATA
              </Btn>
              {uploadShare !== null && <span className="text-xs text-text-2 num" role="status">{uploadShare < 1 ? `Uploading to the local backend: ${(uploadShare * 100).toFixed(0)}%` : "Upload received. Checking its hash, archive and schema…"}</span>}
              {stale && <Tag tone="warn">Selection changed: inspect again</Tag>}
            </div>
          </div>
        )}
      </Panel>

      {inspection && job && (
        <Panel title="Inspection report" hint={`Read from the real contents of ${inspection.run_id}. File names decide nothing.`}>
          <div className="space-y-3">
            {job.records > 0 && (
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <Stat label="Records in this job" value={fmt(job.records)} />
                <Stat label="Features" value={fmt(job.feature_count)} hint={job.models_compatible ? "Compatible with the saved models" : "Not compatible"} />
                <Stat label="Agents 4 and 5" value={job.a4_a5_available ? "Available" : "Off"} hint={job.a4_a5_available ? "Endpoint columns are in this input" : job.a4_a5_missing.join("; ")} />
                <Stat label="Correlation window" value={job.temporal?.basis === "seconds" ? `${status?.window_seconds} s` : "Records"} hint={job.temporal?.basis === "seconds" ? "From this input's timestamps" : "Not a time window"} />
              </div>
            )}
            <ul className="space-y-2">
              {inspection.files.map((f) => <FileReport key={f.id} file={f} included={job.selected.includes(f.id)} disabled={inspecting || running || finished} toggle={() => toggleFile(f.id)} />)}
            </ul>
            {inspection.uploads.map((u) => (
              <p key={u.file_id} className="text-xs text-text-3 break-all">{u.name}: SHA-256 {u.sha256}.{u.skipped_members.length > 0 && ` Skipped, never unpacked or run: ${u.skipped_members.join(", ")}.`}</p>
            ))}
            {job.errors.length > 0 && <Notice tone="crit" title="This input cannot be run yet">{job.errors.map((e) => <p key={e} className="break-words">{e}</p>)}</Notice>}
            {job.warnings.length > 0 && <Notice tone="warn" title="Please read before running">{job.warnings.map((w) => <p key={w} className="break-words">{w}</p>)}</Notice>}
            {job.order_conflict && (
              <label className="flex gap-2 text-xs text-text-2"><input type="checkbox" checked={acceptOrder} onChange={(e) => setAcceptOrder(e.target.checked)} /> Proceed with the replay-order problems above recorded. The run will not be reported as ready to submit.</label>
            )}
            {!!upload?.duplicate_of.length && (
              <label className="flex gap-2 text-xs text-text-2"><input type="checkbox" checked={acceptRepeat} onChange={(e) => setAcceptRepeat(e.target.checked)} /> Process these identical files again, even if {upload.duplicate_of.join(", ")} already ran.</label>
            )}
            <Btn tone="primary" disabled={!canRun} onClick={startRun}>RUN DAY 2 PIPELINE</Btn>
            {inspection.dataset_type === "training" && <p className="text-xs text-text-3">Training data can only be inspected here.</p>}
          </div>
        </Panel>
      )}

      {run && (
        <Panel title={`Pipeline: ${run.run_id}`} hint={STAGES[running ? progress?.stage ?? run.stage : run.stage] ?? run.stage}>
          <div className="space-y-3">
            {run.error && <Notice tone="crit" title="The run failed. No output is ready">{run.error}</Notice>}
            <div>
              <div className="h-2 rounded-full bg-ink-700 overflow-hidden" role="progressbar" aria-label="Records processed" aria-valuemin={0} aria-valuemax={total} aria-valuenow={processed}>
                <div className="h-full bg-accent transition-[width] duration-200" style={{ width: `${total ? (processed / total) * 100 : 0}%` }} />
              </div>
              <p className="text-xs text-text-2 num mt-1" data-testid="run-progress">{fmt(processed)} of {fmt(total)} records in replay order. State: {run.state}.</p>
            </div>
            <div className="grid sm:grid-cols-2 lg:grid-cols-5 gap-3">{Object.keys(AGENTS).map((id) => <AgentCard key={id} id={id} agent={agents[id]} />)}</div>
            {progress?.policies !== undefined && (
              <p className="text-xs text-text-2 num">{fmt(progress.groups)} Agent 4 groups. {fmt(progress.policies)} Agent 5 policies: {fmt(progress.active)} active, {fmt(progress.expired)} expired.</p>
            )}
            {run.mode === "classification_only" && <Notice tone="warn" title="Agents 4 and 5 did not run">This input has no usable endpoints and none were invented, so only classification.csv exists.</Notice>}
            {policyEvents.length > 0 && (
              <div>
                <p className="text-xs font-medium text-text-2 mb-1">Policy activity, newest first (simulated: no firewall or network is touched)</p>
                <TableWrap>
                  <div className="max-h-56 overflow-y-auto">
                    <table className="w-full text-xs" data-testid="policy-events">
                      <thead><tr><th className={thCls}>Event</th><th className={thCls}>Policy</th><th className={thCls}>Source</th><th className={thCls}>At record</th><th className={thCls}>Detail</th></tr></thead>
                      <tbody>
                        {policyEvents.map((p, i) => (
                          <tr key={`${p.policy_id}-${p.event}-${i}`}>
                            <td className={tdCls}><Tag tone={p.event === "ACTIVATED" ? "crit" : "neutral"}>{p.event}</Tag></td>
                            <td className={`${tdCls} num`}>{p.policy_id}</td>
                            <td className={`${tdCls} num`}>{p.scope_value}</td>
                            <td className={`${tdCls} num`}>{p.record_id}</td>
                            <td className={tdCls}>{p.event === "ACTIVATED" ? `For ${fmt(p.ttl_records)} replay positions` : `Blocked ${fmt(p.blocked_records)}, expires after ${p.expires_after_record_id}`}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </TableWrap>
              </div>
            )}
            {run.summary && (
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <Stat label="Records" value={fmt(run.summary.records)} hint={`${run.summary.seconds} s`} />
                <Stat label="Policies" value={fmt(run.summary.policies ?? 0)} />
                <Stat label="Allowed / blocked" value={<span className="text-base">{fmt(run.summary.actions?.ALLOW ?? 0)} / {fmt(run.summary.actions?.BLOCK ?? 0)}</span>} />
                <Stat label="Correlation groups" value={fmt(run.summary.groups ?? 0)} />
              </div>
            )}
            {run.summary?.tradeoff && (
              <div className="rounded-md border border-ink-700 p-3 text-xs space-y-2" data-testid="tradeoff">
                <p className="font-medium text-text-1">Containment against collateral</p>
                <p className="text-text-2 num">Of {fmt(run.summary.tradeoff.called_attack)} flows Agent 1 called attacks, {fmt(run.summary.tradeoff.called_attack_blocked)} were blocked and {fmt(run.summary.tradeoff.called_attack_allowed)} were allowed. {fmt(run.summary.tradeoff.called_benign_blocked)} of {fmt(run.summary.tradeoff.called_benign)} flows it called benign were blocked: these are the possible false blocks.</p>
                <TableWrap>
                  <table className="w-full num">
                    <thead><tr><th className={thCls}>Agent 1 called</th><th className={thCls}>Allowed</th><th className={thCls}>Blocked</th></tr></thead>
                    <tbody>{Object.entries(run.summary.tradeoff.by_class).map(([name, n]) => <tr key={name}><td className={tdCls}>{name}</td><td className={tdCls}>{fmt(n.allow)}</td><td className={tdCls}>{fmt(n.block)}</td></tr>)}</tbody>
                  </table>
                </TableWrap>
                <p className="text-text-3">Based on {run.summary.tradeoff.basis}. Labels stay with the organisers, so true false-positive rates cannot be measured on the hidden set.</p>
              </div>
            )}
            {!!run.summary?.policy_options?.length && (
              <div className="rounded-md border border-ink-700 p-3 text-xs space-y-2" data-testid="policy-options">
                <p className="font-medium text-text-1">Why this many policies? The same file with other policy lengths</p>
                <TableWrap>
                  <table className="w-full num">
                    <thead><tr><th className={thCls}>Policy length (records)</th><th className={thCls}>Policies</th><th className={thCls}>Attack calls blocked</th><th className={thCls}>Attack calls allowed</th><th className={thCls}>Benign calls blocked</th></tr></thead>
                    <tbody>{run.summary.policy_options.map((o) => (
                      <tr key={o.ttl_records} className={o.submitted ? "bg-accent/10" : ""}>
                        <td className={tdCls}>{fmt(o.ttl_records)}{o.submitted && " (used)"}</td>
                        <td className={tdCls}>{fmt(o.policies)}</td>
                        <td className={tdCls}>{fmt(o.called_attack_blocked)}</td>
                        <td className={tdCls}>{fmt(o.called_attack_allowed)}</td>
                        <td className={tdCls}>{fmt(o.called_benign_blocked)}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </TableWrap>
                <p className="text-text-3">Shorter policies mean more of them, but each expiry lets a source through until Agent 5 has the evidence again. Counted by Agent 1's calls, not truth. For explanation only: it was not used to choose the length.</p>
              </div>
            )}
            {finished && <Btn tone="primary" disabled={opening} onClick={showInCommandCentre}>{opening && <Loader2 size={14} className="animate-spin" aria-hidden />} SHOW IN COMMAND CENTRE</Btn>}
            {Object.entries(run.preview).map(([name, table]) => (
              <details key={name} className="text-xs">
                <summary className="cursor-pointer font-medium text-text-2">Review {name}: first rows</summary>
                <TableWrap>
                  <table className="w-full mt-1">
                    <thead><tr>{table.columns.map((c) => <th key={c} className={thCls}>{c}</th>)}</tr></thead>
                    <tbody>{table.rows.map((row, i) => <tr key={i}>{row.map((v, j) => <td key={j} className={`${tdCls} num`}>{v}</td>)}</tr>)}</tbody>
                  </table>
                </TableWrap>
                {table.rows.length === 0 && <p className="text-text-3 mt-1">Header only: this file has no rows.</p>}
              </details>
            ))}
          </div>
        </Panel>
      )}

      {finished && run && run.submission.length > 0 && (
        <Panel title="Upload these files to the organisers" hint="In the names and columns from their templates. Nothing is uploaded from here: you submit them on the stage page."
          actions={<a className="inline-flex items-center gap-1.5 rounded-md border border-accent/50 bg-accent/15 text-accent hover:bg-accent/25 px-3 py-1.5 font-medium whitespace-nowrap" href={`/api/local-data/runs/${run.run_id}/archive?submission_only=true`} download>DOWNLOAD THESE FILES</a>}>
          <div className="space-y-3" data-testid="submission">
            <p className="flex flex-wrap items-center gap-2 font-medium">
              <Tag tone={validation?.submission_ready ? "good" : validation?.rules_passed ? "warn" : "crit"}>
                {validation?.submission_ready ? "Ready to submit" : validation?.rules_passed ? "Rules passed, but check below first" : "Do not submit"}
              </Tag>
              <span className="text-xs text-text-3 font-normal break-all">Also saved in data/outputs/{run.run_id}/</span>
            </p>
            {!validation?.submission_ready && validation && validation.blockers.length > 0 && <ul className="list-disc pl-4 text-xs text-warn">{validation.blockers.map((b) => <li key={b}>{b}</li>)}</ul>}
            <TableWrap>
              <table className="w-full text-xs">
                <thead><tr><th className={thCls}>File</th><th className={thCls}>Rows</th><th className={thCls}>Columns</th><th className={thCls}>Status</th></tr></thead>
                <tbody>
                  {run.submission.map((f) => (
                    <tr key={f.name}>
                      <td className={tdCls}>{f.exists ? <a className="text-accent hover:underline font-medium" href={`/api/local-data/runs/${run.run_id}/files/${f.name}`} download={f.name}>{f.name}</a> : <span className="font-medium">{f.name}</span>}</td>
                      <td className={`${tdCls} num`}>{f.exists ? fmt(f.rows) : "–"}</td>
                      <td className={`${tdCls} text-text-3 !whitespace-normal min-w-[220px]`}>{f.columns.join(", ")}</td>
                      <td className={tdCls}><Tag tone={f.exists ? "good" : "crit"}>{f.exists ? "ready" : "not produced"}</Tag></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          </div>
        </Panel>
      )}

      <Panel title="Section B: output destination" hint="The browser writes each file itself, and reports a file as saved only once the write has finished and its size reads back."
        actions={<Btn tone="primary" disabled={!apiAvailable} onClick={chooseOutput}><FolderOutput size={14} aria-hidden /> CHOOSE OUTPUT FOLDER</Btn>}>
        <div className="space-y-3">
          {!apiAvailable && <Notice tone="info" title="This browser cannot write to a folder">Use Download output ZIP or the file links below. Chrome on localhost can write to a chosen folder.</Notice>}
          <div className="grid sm:grid-cols-2 gap-3 text-xs">
            <p><span className="text-text-3">Destination folder: </span><span className="font-medium" data-testid="output-folder">{outputDir?.name ?? "None chosen"}</span></p>
            <p><span className="text-text-3">Latest run: </span><span className="font-medium num">{run?.run_id ?? "None yet"}</span> {run && <Tag tone={finished ? "good" : run.state === "failed" ? "crit" : "neutral"}>{run.state}</Tag>}</p>
          </div>
          {run && run.outputs.length > 0 && (
            <TableWrap>
              <table className="w-full text-xs" data-testid="export-files">
                <thead><tr><th className={thCls}>Output file</th><th className={thCls}>Size</th><th className={thCls}>Export status</th></tr></thead>
                <tbody>
                  {run.outputs.map((f) => (
                    <tr key={f.name}>
                      <td className={tdCls}><a className="text-accent hover:underline" href={`/api/local-data/runs/${run.run_id}/files/${f.name}`} download={f.name}>{f.name}</a></td>
                      <td className={`${tdCls} num`}>{size(f.size)}</td>
                      <td className={tdCls}><Tag tone={exportState[f.name] === "saved" ? "good" : "neutral"}>{exportState[f.name] ?? "not exported"}</Tag></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
          {existingFolder && (
            <Notice tone="warn" title={`${existingFolder} already exists in that folder`}>
              <p>Nothing was changed. Choose a different folder, or replace its files.</p>
              <Btn tone="danger" className="mt-2" onClick={() => doExport(true)}>Replace the files in {existingFolder}</Btn>
            </Notice>
          )}
          {exportedTo && <Notice tone="info" title="Export finished">The browser confirmed every file in <span className="font-medium">{exportedTo}</span>.</Notice>}
          <div className="flex flex-wrap gap-2">
            <Btn tone="primary" disabled={!finished || !outputDir || exporting} onClick={() => doExport(false)}>{exporting && <Loader2 size={14} className="animate-spin" aria-hidden />} EXPORT ALL OUTPUTS</Btn>
            <Btn disabled={!finished} onClick={downloadZip}>DOWNLOAD OUTPUT ZIP</Btn>
            <Btn disabled={!finished || validating} onClick={validate}>{validating && <Loader2 size={14} className="animate-spin" aria-hidden />} VALIDATE OUTPUTS</Btn>
          </div>
          {validation && (
            <div className="rounded-md border border-ink-700 p-3 space-y-2 text-xs" data-testid="validation">
              <p className="flex flex-wrap items-center gap-2 font-medium">
                {validation.rules_passed ? <CheckCircle2 size={14} className="text-good" aria-hidden /> : <XCircle size={14} className="text-crit" aria-hidden />}
                {validation.rules_passed ? "Every rule passed on the files read back from disk" : `${fmt(validation.problem_count)} rule violations`}
                <Tag tone={validation.submission_ready ? "good" : "warn"}>{validation.submission_ready ? "Ready to submit" : "Not ready to submit"}</Tag>
              </p>
              <p className="text-text-3">Checked {fmt(validation.records_expected)} records at {validation.validated_at}. Labels read: {validation.labels_read ? "yes" : "no"}.</p>
              {validation.problems.slice(0, 20).map((p) => <p key={p} className="text-crit break-words">{p}</p>)}
              {validation.blockers.length > 0 && <ul className="list-disc pl-4 text-warn">{validation.blockers.map((b) => <li key={b}>{b}</li>)}</ul>}
              <details><summary className="cursor-pointer text-text-2">Rules checked</summary><ul className="list-disc pl-4 mt-1 text-text-3">{validation.rules_checked.map((r) => <li key={r}>{r}</li>)}</ul></details>
            </div>
          )}
        </div>
      </Panel>
    </Page>
  );
}
