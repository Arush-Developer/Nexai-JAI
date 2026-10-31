"""Level 2 outputs: classification.csv, policies.csv and replay.csv, plus a checker for their rules.

Two logically separate steps. score_records rates every flow on its own, so it can be batched without
seeing other flows, and every record gets a prediction even if replay later blocks it. replay_scored then
runs Agents 4 and 5 one record at a time in the supplied order, through the same engine as the live replay.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace
from pathlib import Path

import numpy as np
import pandas as pd

from backend.agents.defense_agent import ALLOW, BLOCK, EXCLUSIVE, DefenseConfig
from backend.agents.fusion_agent import (
    AGREED_BENIGN, ANOMALY_ONLY, CLASSIFIER_ONLY, MULTI_SIGNAL_ATTACK, UNCERTAIN)
from backend.agents.temporal_agent import TemporalConfig
from backend.config import load_config
from backend.preprocessing.data_processor import (
    Partition, check_replay_order, load_flows, parse_timestamps, replay_order)
from backend.replay.causal_replay import CausalReplayEngine

CLASSIFICATION_COLUMNS = ["record_id", "predicted_class", "anomaly_score", "evidence_status"]
POLICY_COLUMNS = ["policy_id", "activated_after_record_id", "scope_type", "scope_value", "expires_after_record_id",
                  "policy_action"]
REPLAY_COLUMNS = ["record_id", "action", "applied_policy_id"]
LOGICAL_COLUMNS = {"classification": CLASSIFICATION_COLUMNS, "policy": POLICY_COLUMNS, "replay": REPLAY_COLUMNS}
EVIDENCE_STATUSES = {AGREED_BENIGN, MULTI_SIGNAL_ATTACK, CLASSIFIER_ONLY, ANOMALY_ONLY, UNCERTAIN}


def _known(cls, values: dict) -> dict:
    allowed = {f.name for f in fields(cls)}
    return {k: v for k, v in values.items() if k in allowed}


def temporal_config(level2: dict | None = None) -> TemporalConfig:
    level2 = load_config().get("level2", {}) if level2 is None else level2
    return TemporalConfig(**_known(TemporalConfig, level2.get("temporal", {})))


def defense_config(level2: dict | None = None) -> DefenseConfig:
    level2 = load_config().get("level2", {}) if level2 is None else level2
    return DefenseConfig(**_known(DefenseConfig, level2.get("defense", {})))


def order_settings(level2: dict | None = None) -> dict:
    level2 = load_config().get("level2", {}) if level2 is None else level2
    return {"on_conflict": "error", "dayfirst": False, **level2.get("replay_order", {})}


def csv_schema(level2: dict | None = None) -> dict[str, dict[str, str]]:
    """Logical column to the name written in each file. A column configured as null is left out of the file."""
    level2 = load_config().get("level2", {}) if level2 is None else level2
    names = level2.get("columns", {})
    schema = {file: {c: names.get(file, {}).get(c, c) for c in columns} for file, columns in LOGICAL_COLUMNS.items()}
    return {file: {c: name for c, name in columns.items() if name} for file, columns in schema.items()}


def output_file(name: str, level2: dict | None = None) -> str:
    """The name the organisers expect for a logical output: classification, policy or replay."""
    level2 = load_config().get("level2", {}) if level2 is None else level2
    return level2.get("files", {}).get(name, f"{name}.csv")


def official_frame(name: str, frame: pd.DataFrame) -> pd.DataFrame:
    """One output as the organisers' validator wants it: their column names and their values."""
    level2 = load_config().get("level2", {})
    columns = csv_schema(level2)[name]
    frame = frame[list(columns)].copy()
    if name == "classification" and level2.get("evidence_status_values"):
        frame["evidence_status"] = frame["evidence_status"].map(level2["evidence_status_values"])
    if name == "replay" and level2.get("no_policy_value"):
        frame["applied_policy_id"] = frame["applied_policy_id"].replace("", level2["no_policy_value"]).fillna(level2["no_policy_value"])
    return frame.rename(columns=columns)


def replay_inputs(meta: pd.DataFrame, dayfirst: bool = False) -> dict[str, list]:
    """The observed fields Agents 4 and 5 may read, as plain per-record lists in the order given."""
    n = len(meta)

    def numbers(column: str) -> list:
        if column not in meta:
            return [None] * n
        return [None if v != v else v for v in pd.to_numeric(meta[column], errors="coerce").to_numpy(dtype=float).tolist()]

    has_time = "timestamp" in meta
    seconds = parse_timestamps(meta["timestamp"], dayfirst) if has_time else None
    return {
        "record_id": meta["flow_id"].astype(str).tolist(),
        "source": meta["source_ip"].astype(str).tolist(),
        "destination": meta["destination_ip"].astype(str).tolist(),
        "port": numbers("destination_port"),
        "source_port": numbers("source_port"),
        "timestamp": [None if s != s else s for s in seconds.tolist()] if has_time else [None] * n,
        "observed_at": meta["timestamp"].astype(str).tolist() if has_time else [None] * n,
    }


@dataclass
class ScoredRecords:
    """Agents 1-3 for every record, in replay order, plus observed endpoints. It holds no labels."""

    inputs: dict[str, list]
    final_class: np.ndarray
    confidence: np.ndarray
    scores: np.ndarray
    is_anomaly: np.ndarray
    evidence_status: np.ndarray
    classification: pd.DataFrame
    features: pd.DataFrame
    order: np.ndarray
    order_report: dict

    def __len__(self) -> int:
        return len(self.classification)


def _score_in_order(pipeline, partition: Partition, on_order_conflict: str | None, on_agent=None) -> tuple:
    """Agents 1-3 over every record in replay order. on_agent(agent, state) reports each stage as it really runs."""
    notify = on_agent or (lambda agent, state: None)
    settings = order_settings()
    report = check_replay_order(partition.metadata, on_order_conflict or settings["on_conflict"], settings["dayfirst"])
    order = replay_order(partition.metadata)
    features = partition.features.iloc[order].reset_index(drop=True)
    meta = partition.metadata.iloc[order].reset_index(drop=True)
    notify("A1", "running")
    proba = pipeline.threat.predict_proba(features)
    notify("A1", "done")
    notify("A2", "running")
    scores = pipeline.anomaly.score(features)
    notify("A2", "done")
    notify("A3", "running")
    fusion = pipeline.fuse_level1(proba, scores)
    notify("A3", "done")
    classification = pd.DataFrame({"record_id": meta["flow_id"].astype(str).tolist(),
                                   "predicted_class": fusion.final_class, "anomaly_score": np.round(scores, 6),
                                   "evidence_status": fusion.evidence_status})
    return meta, features, order, report, scores, fusion, classification, settings


def score_classification(pipeline, partition: Partition, on_order_conflict: str | None = None, on_agent=None) -> tuple:
    """Agents 1-3 alone, for an input with no endpoints. Returns (classification, order report)."""
    *_, report, _, _, classification, _ = _score_in_order(pipeline, partition, on_order_conflict, on_agent)
    return classification, report


def score_records(pipeline, partition: Partition, on_order_conflict: str | None = None, on_agent=None) -> ScoredRecords:
    """Independent per-record scoring. Labels are never read, even if the file carries them."""
    missing = {"source_ip", "destination_ip"} - set(partition.metadata.columns)
    if missing:
        raise ValueError(f"Agents 4 and 5 need endpoint columns; missing: {sorted(missing)}")
    meta, features, order, report, scores, fusion, classification, settings = _score_in_order(
        pipeline, partition, on_order_conflict, on_agent)
    return ScoredRecords(replay_inputs(meta, settings["dayfirst"]), fusion.final_class, fusion.threat_confidence, scores,
                         fusion.is_anomaly, fusion.evidence_status, classification, features, order, report)


def replay_scored(scored: ScoredRecords, temporal: TemporalConfig | None = None, defense: DefenseConfig | None = None,
                  limit: int | None = None, on_step=None) -> CausalReplayEngine:
    """Stateful replay. on_step(i, step, engine) runs after each record, for a demo or a STOP request."""
    n = len(scored) if limit is None else min(limit, len(scored))
    temporal = temporal or temporal_config()
    if scored.order_report["ambiguities"]:
        temporal = replace(temporal, on_timestamp_regression="report")
    engine = CausalReplayEngine(temporal, defense or defense_config(), total_records=n)
    inputs = scored.inputs
    for i in range(n):
        step = engine.step(i, inputs["record_id"][i], inputs["source"][i], inputs["destination"][i], inputs["port"][i],
                           scored.final_class[i], float(scored.confidence[i]), float(scored.scores[i]),
                           bool(scored.is_anomaly[i]), scored.evidence_status[i], inputs["timestamp"][i],
                           inputs["observed_at"][i], inputs["source_port"][i])
        if on_step is not None:
            on_step(i, step, engine)
    if n:
        engine.finish(n - 1, inputs["record_id"][n - 1])
    return engine


def replay_outputs(engine: CausalReplayEngine) -> dict:
    return {
        "replay": pd.DataFrame(engine.action_log, columns=["record_id", "action", "applied_policy_id"]),
        "policy": pd.DataFrame([{c: p[c] for c in POLICY_COLUMNS} for p in engine.defense.public_policies()],
                               columns=POLICY_COLUMNS),
        "policies": engine.defense.public_policies(),
        "log": engine.defense.log,
        "groups": engine.temporal.public_groups(),
        "state": engine.state(),
    }


def run_level2(pipeline, partition: Partition, temporal: TemporalConfig | None = None,
               defense: DefenseConfig | None = None, on_order_conflict: str | None = None, on_step=None) -> dict:
    """Run all five agents. Labels are never read, even if the file carries them."""
    scored = score_records(pipeline, replace(partition, labels=None), on_order_conflict)
    engine = replay_scored(scored, temporal, defense, on_step=on_step)
    return {"classification": scored.classification, **replay_outputs(engine), "order": scored.order,
            "order_report": scored.order_report, "scored": scored,
            "expiry_boundary": engine.defense.config.expiry_boundary}


def check_prefix_invariance(scored: ScoredRecords, result: dict, temporal: TemporalConfig | None = None,
                            defense: DefenseConfig | None = None, cuts=(0.25, 0.5, 0.75)) -> list[str]:
    """Rerun replay on prefixes of the stream. Any change to an earlier decision means a future record leaked in."""
    problems = []
    for share in cuts:
        cut = int(len(scored) * share)
        if cut < 2:
            continue
        short = replay_outputs(replay_scored(scored, temporal, defense, limit=cut))
        # The last prefix record is excluded: an exclusive boundary may clamp a policy's expiry onto it.
        keep = cut - 1
        for column in ("action", "applied_policy_id"):
            if short["replay"][column].iloc[:keep].tolist() != result["replay"][column].iloc[:keep].tolist():
                problems.append(f"causality: {column} changed for records before cut {cut} when later records were removed")
        first = result["policy"].set_index("policy_id")["activated_after_record_id"]
        for p in short["policy"].itertuples(index=False):
            if first.get(p.policy_id) != p.activated_after_record_id:
                problems.append(f"causality: {p.policy_id} differs when records after {cut} are removed")
    return problems


def _coverage_problems(name: str, frame: pd.DataFrame, ids: list[str]) -> list[str]:
    problems = []
    got = frame["record_id"].astype(str)
    if got.duplicated().any():
        problems.append(f"{name}.csv repeats record ids")
    if set(got) != set(ids):
        problems.append(f"{name}.csv does not cover every record exactly once")
    return problems


def validate_classification(classification: pd.DataFrame, record_ids) -> list[str]:
    """The classification.csv rules alone. Also the whole check when an input has no endpoints."""
    if list(classification.columns) != CLASSIFICATION_COLUMNS:
        return [f"classification.csv has columns {list(classification.columns)}, expected {CLASSIFICATION_COLUMNS}"]
    problems = _coverage_problems("classification", classification, [str(r) for r in record_ids])
    classes = set(load_config()["labels"]["classes"])
    if not set(classification["predicted_class"]) <= classes:
        problems.append("classification.csv has a class outside the five benchmark classes")
    scores = pd.to_numeric(classification["anomaly_score"], errors="coerce")
    if not (np.isfinite(scores) & scores.between(0, 1)).all():
        problems.append("classification.csv has an anomaly_score that is missing or outside 0 to 1")
    official = set(load_config().get("level2", {}).get("evidence_status_values", {}).values())
    if not set(classification["evidence_status"]) <= EVIDENCE_STATUSES | official:
        problems.append("classification.csv has an evidence_status Agent 3 does not produce")
    return problems


def validate_outputs(classification: pd.DataFrame, policy: pd.DataFrame, replay: pd.DataFrame, record_ids,
                     sources, expiry_boundary: str | None = None) -> list[str]:
    """Check the brief's rules. Returns every problem found; an empty list means the files are consistent.

    record_ids and sources must be in official replay order. Frames use the logical column names.
    """
    for name, frame in (("classification", classification), ("policy", policy), ("replay", replay)):
        if list(frame.columns) != LOGICAL_COLUMNS[name]:
            return [f"{name}.csv has columns {list(frame.columns)}, expected {LOGICAL_COLUMNS[name]}"]
    boundary = expiry_boundary or defense_config().expiry_boundary
    ids = [str(r) for r in record_ids]
    position = {r: i for i, r in enumerate(ids)}
    source_of = dict(zip(ids, map(str, sources)))
    problems = validate_classification(classification, ids) + _coverage_problems("replay", replay, ids)
    if policy["policy_id"].duplicated().any():
        problems.append("policy.csv repeats policy ids")
    if len(policy) and (set(policy["scope_type"]) != {"SOURCE_IP"} or set(policy["policy_action"]) != {BLOCK}):
        problems.append("policy.csv must use scope_type SOURCE_IP and policy_action BLOCK")
    if len(replay["record_id"]) and not set(replay["action"]) <= {ALLOW, BLOCK}:
        problems.append("replay.csv action must be ALLOW or BLOCK")
    by_record = dict(zip(replay["record_id"].astype(str), replay["action"]))

    def covered(p, at: int) -> bool:
        start, end = position[str(p.activated_after_record_id)], position[str(p.expires_after_record_id)]
        return start < at < end or (at == end and boundary != EXCLUSIVE)

    policies = {p.policy_id: p for p in policy.itertuples(index=False)}
    valid: list = []
    for p in policies.values():
        if str(p.activated_after_record_id) not in position or str(p.expires_after_record_id) not in position:
            problems.append(f"{p.policy_id}: activation or expiry names an unknown record")
        elif position[str(p.expires_after_record_id)] <= position[str(p.activated_after_record_id)]:
            problems.append(f"{p.policy_id}: expires at or before its activation")
        elif source_of[str(p.activated_after_record_id)] != str(p.scope_value):
            problems.append(f"{p.policy_id}: triggering record is not from the policy's source")
        else:
            valid.append(p)
            if by_record.get(str(p.activated_after_record_id)) == BLOCK:
                problems.append(f"{p.policy_id}: triggered by a record that was itself blocked")
    for r in replay.itertuples(index=False):
        record = str(r.record_id)
        applied = "" if pd.isna(r.applied_policy_id) else str(r.applied_policy_id)
        if r.action == ALLOW:
            if applied:
                problems.append(f"{record}: ALLOW must have an empty applied_policy_id")
            continue
        p = policies.get(applied)
        if p is None:
            problems.append(f"{record}: BLOCK references no known policy")
        elif source_of.get(record) != str(p.scope_value):
            problems.append(f"{record}: BLOCK by {applied} from a different source")
        elif str(p.activated_after_record_id) in position and str(p.expires_after_record_id) in position and (
                record not in position or not covered(p, position[record])):
            problems.append(f"{record}: BLOCK by {applied} outside its active range")
    # Every flow from a source inside an active range must be blocked, or a policy was not applied.
    for p in valid:
        start, end = position[str(p.activated_after_record_id)], position[str(p.expires_after_record_id)]
        leaked = [r for r in ids[start + 1:end + 1] if covered(p, position[r]) and source_of[r] == str(p.scope_value)
                  and by_record.get(r) != BLOCK]
        if leaked:
            problems.append(f"{p.policy_id}: {len(leaked)} matching records were not blocked while it was active")
    return problems


def write_outputs(result: dict, out_dir: Path) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    schema = csv_schema()
    paths = {}
    for name in LOGICAL_COLUMNS:
        paths[name] = out_dir / output_file(name)
        official_frame(name, result[name]).to_csv(paths[name], index=False)
    logs = {"state": result.get("state"), "order_report": result.get("order_report"),
            "expiry_boundary": result.get("expiry_boundary"), "policies": result["policies"],
            "activation_and_expiry_log": result["log"], "correlation_groups": result["groups"]}
    paths["evidence"] = out_dir / "agent4_agent5_evidence.json"
    paths["evidence"].write_text(json.dumps(logs, indent=1, default=str))
    return paths


def write_classification(classification: pd.DataFrame, out_dir: Path) -> Path:
    """classification.csv alone, for an input with no endpoints. No replay or policy file is implied."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / output_file("classification")
    official_frame("classification", classification).to_csv(path, index=False)
    return path


def read_outputs(out_dir: Path, names: tuple[str, ...] | None = None) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Read the files as they would be submitted, and map them back to the logical column names."""
    schema, frames, problems = csv_schema(), {}, []
    for name, columns in schema.items():
        if names is not None and name not in names:
            continue
        path = Path(out_dir) / output_file(name)
        if not path.exists():
            problems.append(f"{path.name} is missing")
            continue
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        if list(frame.columns) != list(columns.values()):
            problems.append(f"{path.name} header is {list(frame.columns)}, expected {list(columns.values())}")
            continue
        frame = frame.rename(columns={official: logical for logical, official in columns.items()})
        if name == "policy" and "policy_action" not in frame:
            frame = frame.assign(policy_action=BLOCK)  # The organisers' file omits it: every policy here blocks.
        none = load_config().get("level2", {}).get("no_policy_value")
        if name == "replay" and none:
            frame["applied_policy_id"] = frame["applied_policy_id"].replace(none, "")
        frames[name] = frame
    return frames, problems


def stage_page_problems(out_dir: Path, meta: pd.DataFrame) -> list[str]:
    """The stage page's published validator, applied to the raw files. meta must be in official replay order.

    Separate from validate_outputs on purpose: this recomputes every replay row from the policy ledger, exactly as
    the page describes, so a disagreement between the two checks would itself be a warning.
    """
    level2 = load_config().get("level2", {})
    read = lambda name: pd.read_csv(Path(out_dir) / output_file(name, level2), dtype=str, keep_default_na=False)  # noqa: E731
    classification, replay, policies = read("classification"), read("replay"), read("policy")
    ids = meta["flow_id"].astype(str).tolist()
    index = {r: i for i, r in enumerate(ids)}
    problems: list[str] = []
    for name, frame in (("classification", classification), ("replay", replay)):
        if sorted(frame["record_id"]) != sorted(ids):
            problems.append(f"{output_file(name, level2)} must have exactly one row per hidden record_id")
    bad = set(classification["predicted_class"]) - {"BENIGN", "BRUTE_FORCE", "DOS", "DDOS", "PORT_SCAN"}
    if bad:
        problems.append(f"predicted_class has values the validator refuses: {sorted(bad)}")
    bad = set(classification["evidence_status"]) - {"AGREE", "CONFLICT", "UNCERTAIN"}
    if bad:
        problems.append(f"evidence_status has values the validator refuses: {sorted(bad)}")
    scores = pd.to_numeric(classification["anomaly_score"], errors="coerce")
    if not scores.between(0, 1).all():
        problems.append("anomaly_score must be a number from 0 to 1")
    bad = set(replay["action"]) - {ALLOW, BLOCK}
    if bad:
        problems.append(f"action has values the validator refuses: {sorted(bad)}")
    if policies["policy_id"].duplicated().any():
        problems.append("policy_id must be unique")
    source = meta["source_ip"].astype(str).tolist()
    destination = meta["destination_ip"].astype(str).tolist()
    observed = {"SOURCE_IP": set(source), "DESTINATION_IP": set(destination),
                "SRC_DST_PAIR": {f"{s}|{d}" for s, d in zip(source, destination)}}
    ledger = []
    for p in policies.itertuples(index=False):
        if p.scope_type not in observed:
            problems.append(f"{p.policy_id}: scope_type {p.scope_type} is not allowed")
        elif p.scope_value not in observed[p.scope_type]:
            problems.append(f"{p.policy_id}: {p.scope_value} is not an exact observed endpoint")
        elif p.activated_after_record_id not in index or p.expires_after_record_id not in index:
            problems.append(f"{p.policy_id}: names a record that is not in the hidden set")
        else:
            ledger.append((p.policy_id, index[p.activated_after_record_id], index[p.expires_after_record_id], p.scope_type, p.scope_value))
    submitted = dict(zip(replay["record_id"], zip(replay["action"], replay["active_policy_id"])))
    for i, record in enumerate(ids):
        flow = {"SOURCE_IP": source[i], "DESTINATION_IP": destination[i], "SRC_DST_PAIR": f"{source[i]}|{destination[i]}"}
        matches = [pid for pid, start, end, kind, value in ledger if start < i <= end and flow[kind] == value]
        expected = (BLOCK, min(matches)) if matches else (ALLOW, "NONE")
        if record in submitted and submitted[record] != expected:
            problems.append(f"{record}: the ledger gives {expected[0]} {expected[1]}, but replay.csv says {submitted[record][0]} {submitted[record][1]}")
    return problems


def validate_submission(out_dir: Path, input_path: Path, expiry_boundary: str | None = None) -> list[str]:
    """Check the files on disk against the input they claim to describe. Reads no labels."""
    frames, problems = read_outputs(out_dir)
    if problems:
        return problems
    meta = load_flows(input_path).metadata
    meta = meta.iloc[replay_order(meta)]
    return validate_outputs(frames["classification"], frames["policy"], frames["replay"], meta["flow_id"],
                            meta["source_ip"], expiry_boundary) + stage_page_problems(out_dir, meta)
