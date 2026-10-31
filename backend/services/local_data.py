"""The Data Manager's backend: staged uploads, dataset inspection, one pipeline run per upload, and its outputs.

The browser reads the files a person picks and posts them to this local process. Nothing here reads a
browser-selected path, and nothing leaves the machine. Hidden data cannot reach training or calibration:
no path in this module fits, tunes or retrains anything, and those scripts only read the paths in the config.
Labels are dropped before scoring, so a run behaves the same with or without them.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import threading
import time
import zipfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import numpy as np
import pandas as pd

from backend.config import load_config, resolve
from backend.preprocessing.data_processor import (
    ReplayOrderError, check_replay_order, parse_timestamps, read_flow_frame, replay_order, split_representations,
    standardise_columns)
from backend.services import level2
from backend.services.model_service import Pipeline

TRAINING, VALIDATION, HIDDEN = "training", "validation", "hidden"
DATASET_TYPES = (TRAINING, VALIDATION, HIDDEN)
FULL, CLASSIFICATION_ONLY = "full", "classification_only"
DATA_SUFFIXES = {".csv", ".parquet"}
RUN_ID = re.compile(r"^RUN_\d{4}_\d{3,}$")
EXPORT_FILES = ("classification.csv", "policies.csv", "replay.csv", "run_summary.json", "validation_report.json",
                "replay_events.jsonl", "agent4_agent5_evidence.json")
PROGRESS_EVERY = 2000
PREVIEW_ROWS = 5
CHUNK = 1024 * 1024
RULES_CHECKED = [
    "classification.csv has the exact columns, and covers every record exactly once",
    "predicted_class is one of the five classes; anomaly_score is within 0 to 1; evidence_status is one Agent 3 produces",
    "replay.csv covers every record exactly once, with ALLOW or BLOCK only",
    "every ALLOW says NONE in active_policy_id; every BLOCK names a known policy",
    "every BLOCK matches its policy's source, after activation and within expiry",
    "no record from a blocked source escaped an active policy; no policy is triggered by a blocked record",
    "policies.csv ids are unique, with SOURCE_IP scope and BLOCK action only",
    "evidence_status is AGREE, CONFLICT or UNCERTAIN, as the stage page requires",
    "every replay.csv row equals what the stage page's algorithm recomputes from policies.csv",
]


class LocalDataError(ValueError):
    """A request the operator can correct. status is the HTTP code to report it with."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_value(value):
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return str(value)


def to_json(value) -> str:
    return json.dumps(value, indent=1, default=_json_value)


def _safe_name(name: str) -> str:
    """The final path component only, restricted to ordinary characters. Never trusted as a path."""
    leaf = PurePosixPath(name.replace("\\", "/")).name
    return re.sub(r"[^A-Za-z0-9._ -]", "_", leaf).strip(" .") or "unnamed"


def member_problem(info: zipfile.ZipInfo) -> str | None:
    """Why this archive member must not be extracted, if there is a reason."""
    name = info.filename
    path = PurePosixPath(name.replace("\\", "/"))
    if "\x00" in name or path.is_absolute() or re.match(r"^[A-Za-z]:", name) or ".." in path.parts:
        return f"unsafe path {name!r}"
    if stat.S_ISLNK(info.external_attr >> 16):
        return f"symbolic link {name!r}"
    if info.flag_bits & 0x1:
        return f"encrypted member {name!r}"
    return None


def extract_archive(archive: Path, target: Path, limits: dict) -> dict:
    """List an archive, refuse it if any member is unsafe, then copy out its data files and nothing else."""
    extracted, skipped = [], []
    written = 0
    try:
        with zipfile.ZipFile(archive) as z:
            members = z.infolist()
            if len(members) > limits["max_archive_members"]:
                raise LocalDataError(f"{archive.name}: {len(members)} members, limit {limits['max_archive_members']}")
            problems = [p for p in map(member_problem, members) if p]
            if problems:
                raise LocalDataError(f"{archive.name} refused, nothing was extracted: {'; '.join(problems[:5])}")
            declared = sum(m.file_size for m in members if not m.is_dir())
            if declared > limits["max_extracted_bytes"]:
                raise LocalDataError(f"{archive.name} would extract to {declared:,} bytes, "
                                     f"limit {limits['max_extracted_bytes']:,}")
            for member in members:
                if member.is_dir():
                    continue
                path = PurePosixPath(member.filename.replace("\\", "/"))
                junk = "__MACOSX" in path.parts or path.name.startswith(".")
                if junk or path.suffix.lower() not in DATA_SUFFIXES:
                    skipped.append(member.filename)  # Archived programs and documents are never unpacked or run.
                    continue
                if member.compress_size and member.file_size / member.compress_size > limits["max_compression_ratio"]:
                    raise LocalDataError(f"{archive.name} refused: {member.filename} expands more than "
                                         f"{limits['max_compression_ratio']} times")
                out = target.joinpath(*path.parts)
                if not out.resolve().is_relative_to(target.resolve()):
                    raise LocalDataError(f"{archive.name} refused: unsafe path {member.filename!r}")
                out.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as source, out.open("wb") as sink:
                    # Sizes in the header can lie, so the limit is enforced on the bytes actually produced.
                    while chunk := source.read(CHUNK):
                        written += len(chunk)
                        if written > limits["max_extracted_bytes"]:
                            raise LocalDataError(f"{archive.name} refused: extracted data passed the limit of "
                                                 f"{limits['max_extracted_bytes']:,} bytes")
                        sink.write(chunk)
                extracted.append({"member": member.filename, "path": out, "size": out.stat().st_size})
    except zipfile.BadZipFile as exc:
        raise LocalDataError(f"{archive.name} is not a readable ZIP archive: {exc}") from exc
    return {"extracted": extracted, "skipped": skipped}


def _duplicate_columns(frame: pd.DataFrame) -> list[list[str]]:
    groups: dict[int, list[str]] = {}
    for column in frame.columns:
        values = frame[column]
        digest = int(pd.util.hash_pandas_object(values.astype(str) if values.dtype == object else values,
                                                index=False).sum())
        groups.setdefault(digest, []).append(str(column))
    return [names for names in groups.values() if len(names) > 1]


def _time_report(metadata: pd.DataFrame, dayfirst: bool) -> dict:
    if "timestamp" not in metadata:
        return {"available": False, "unreadable": None, "backward_steps": None, "first": None, "last": None}
    order = replay_order(metadata)
    seconds = parse_timestamps(metadata["timestamp"], dayfirst)[order]
    known = seconds[~np.isnan(seconds)]
    stamps = metadata["timestamp"].iloc[order]
    return {"available": len(known) > 0, "unreadable": int(np.isnan(seconds).sum()),
            "backward_steps": int((np.diff(known) < 0).sum()),
            "first": str(stamps.iat[0]) if len(stamps) else None, "last": str(stamps.iat[-1]) if len(stamps) else None}


def inspect_frame(raw: pd.DataFrame, display: str, required: list[str], known: set[str]) -> dict:
    """Describe one file from its actual contents. Never decided by its name, and it supplies no missing values."""
    cfg = load_config()
    data = cfg["data"]
    frame = standardise_columns(raw)
    columns = list(frame.columns)
    id_col, label_col = data["id_column"], data["label_column"]
    meta_cols = [c for c in data["metadata_columns"] if c in frame]
    missing_features = [c for c in required if c not in frame]
    numeric = frame[[c for c in columns if c in known]]
    as_numbers = numeric.apply(pd.to_numeric, errors="coerce")
    invalid = {c: int((as_numbers[c].isna() & numeric[c].notna()).sum()) for c in numeric}
    invalid = {c: n for c, n in invalid.items() if n}
    values = as_numbers.to_numpy(dtype=np.float64) if len(numeric.columns) else np.empty((len(frame), 0))
    row_ids = frame[id_col].astype("string") if id_col in frame else None
    report = {
        "display": display,
        "records": len(frame),
        "columns": len(raw.columns),
        "column_names": [str(c) for c in raw.columns],
        "dtypes": {str(c): str(t) for c, t in raw.dtypes.items()},
        "missing_values": int(frame.isna().sum().sum()),
        "positive_infinity": int(np.isposinf(values).sum()),
        "negative_infinity": int(np.isneginf(values).sum()),
        "duplicate_records": int(numeric.duplicated().sum()) if len(numeric.columns) else 0,
        "duplicate_columns": _duplicate_columns(frame),
        "label_column": label_col if label_col in frame else None,
        "record_id_column": id_col if id_col in frame else None,
        "missing_record_ids": int(row_ids.isna().sum()) if row_ids is not None else None,
        "duplicate_record_ids": int(row_ids.duplicated().sum()) if row_ids is not None else None,
        "timestamp_column": "timestamp" if "timestamp" in frame else None,
        "source_ip_available": "source_ip" in frame,
        "destination_ip_available": "destination_ip" in frame,
        "source_port_available": "source_port" in frame,
        "destination_port_available": "destination_port" in frame or "Destination Port" in frame,
        "protocol_available": "protocol" in frame,
        "seq_available": "seq" in frame,
        "model_required_features": len(required),
        "feature_count": len(numeric.columns),
        "missing_required_features": missing_features,
        "unrecognised_columns": [c for c in columns if c not in known and c not in {id_col, label_col, *meta_cols}],
        "invalid_values": invalid,
        "role": "flows" if set(required) & set(columns) else "ordering" if id_col in frame and meta_cols else "unknown",
        "timestamp_order": None,
        "errors": [],
        "warnings": [],
    }
    if frame.columns.duplicated().any():
        report["errors"].append(f"column names repeat after normalising: "
                                f"{sorted(set(frame.columns[frame.columns.duplicated()]))[:5]}")
    if report["role"] == "unknown":
        report["errors"].append("this file has neither the model's flow features nor a record id with ordering columns")
    if id_col not in frame:
        report["errors"].append(f"missing mandatory column {id_col}")
    elif report["missing_record_ids"] or report["duplicate_record_ids"]:
        report["errors"].append(f"{report['missing_record_ids']} missing and {report['duplicate_record_ids']} "
                                "repeated record ids; every record needs exactly one official id")
    if report["role"] == "flows" and missing_features:
        report["errors"].append(f"missing {len(missing_features)} model-required features, which are never invented: "
                                f"{missing_features[:10]}")
    if invalid:
        report["errors"].append(f"non-numeric values in feature columns: {dict(list(invalid.items())[:10])}")
    holes = int(np.isnan(values).sum()) - sum(invalid.values())
    inf = report["positive_infinity"] + report["negative_infinity"]
    if holes or inf:
        report["warnings"].append(f"{holes} missing and {inf} infinite feature values will take the training median, "
                                  "the same fitted rule the models were built with")
    if id_col in frame and meta_cols:
        order_meta = frame[[id_col, *meta_cols]].rename(columns={id_col: "flow_id"})
        try:
            report["timestamp_order"] = _time_report(order_meta, level2.order_settings()["dayfirst"])
        except (ValueError, TypeError) as exc:
            report["errors"].append(f"seq or timestamps could not be read: {exc}")
    return report


def apply_dataset_rules(report: dict, dataset_type: str) -> None:
    """The file's real schema must agree with the purpose the person chose for it."""
    if report["role"] != "flows":
        return
    labelled = report["label_column"] is not None
    if dataset_type == HIDDEN and labelled:
        report["errors"].append("declared Day 2 hidden replay data, but it has a label column. The hidden set ships "
                                "without labels, so this looks like the wrong file. Choose the right file or type.")
    if dataset_type in (TRAINING, VALIDATION) and not labelled:
        report["errors"].append(f"declared {dataset_type} data, but it has no label column.")


def combine_job(flows: list[pd.DataFrame], ordering: list[pd.DataFrame], problems: list[str]) -> pd.DataFrame | None:
    """One replay input from the chosen files. Incompatible or unordered files are refused, never concatenated."""
    id_col = load_config()["data"]["id_column"]
    if not flows:
        problems.append("no flow file is selected")
        return None
    schema = set(flows[0].columns)
    for frame in flows[1:]:
        if set(frame.columns) != schema:
            problems.append("the selected flow files have different columns, so they cannot be joined: "
                            f"{sorted(schema ^ set(frame.columns))[:8]}")
            return None
    if len(flows) > 1 and "seq" not in schema and not ordering:
        problems.append("several flow files with no seq column: the order across files is unknown. Select one file, "
                        "or also supply the organisers' ordering file.")
        return None
    if len(ordering) > 1:
        problems.append("more than one ordering file is selected")
        return None
    combined = pd.concat(flows, ignore_index=True) if len(flows) > 1 else flows[0]
    if id_col not in combined:
        problems.append(f"the selected flow files have no {id_col} column")
        return None
    if combined[id_col].astype(str).duplicated().any():
        problems.append("the same record id appears more than once across the selected files")
        return None
    if ordering:
        order = ordering[0]
        shared = (set(order.columns) & set(combined.columns)) - {id_col}
        if shared:
            problems.append(f"the ordering file repeats columns the flow file already has: {sorted(shared)}")
            return None
        ids, order_ids = combined[id_col].astype(str), order[id_col].astype(str)
        if set(ids) != set(order_ids) or len(ids) != len(order_ids):
            problems.append("the ordering file does not list exactly the records in the flow file")
            return None
        # Rows take the ordering file's own sequence, unless it states a seq, which replay_order then follows.
        combined = order.assign(**{id_col: order_ids}).merge(combined.assign(**{id_col: ids}), on=id_col, how="left",
                                                            validate="one_to_one")
    return combined


def endpoint_gaps(metadata: pd.DataFrame) -> list[str]:
    """The endpoint columns Agents 4 and 5 need that are absent or wholly empty. Addresses are never invented."""
    gaps = []
    for column in ("source_ip", "destination_ip"):
        if column not in metadata:
            gaps.append(f"{column} column is absent")
        elif not metadata[column].notna().any():
            gaps.append(f"{column} column is empty")
    return gaps


def temporal_note(has_timestamps: bool) -> dict:
    temporal = level2.temporal_config()
    if has_timestamps and temporal.window_seconds:
        return {"basis": "seconds",
                "detail": f"Agent 4 uses a sliding window of {temporal.window_seconds:g} seconds, from this input's timestamps."}
    return {"basis": "records",
            "detail": "No usable timestamps, so Agent 4 cannot do time-window correlation. It falls back to the last "
                      f"{temporal.window_records} replay records, which is not a time window."}


@dataclass
class Job:
    run_id: str
    state: str = "queued"  # queued, running, completed, failed
    stage: str = "queued"
    mode: str | None = None
    processed: int = 0
    total: int = 0
    agents: dict = field(default_factory=lambda: {a: {"state": "waiting", "detail": ""}
                                                  for a in ("A1", "A2", "A3", "A4", "A5")})
    events: list = field(default_factory=list)
    error: str | None = None
    next_seq: int = 0

    def emit(self, kind: str, **body) -> None:
        self.next_seq += 1
        self.events.append({"seq": self.next_seq, "type": kind, **body})

    def public(self) -> dict:
        return {"state": self.state, "stage": self.stage, "mode": self.mode, "processed": self.processed,
                "total": self.total, "agents": self.agents, "error": self.error}


class LocalDataService:
    def __init__(self, pipeline_loader=Pipeline.load, base: Path | None = None):
        self.limits = load_config()["local_data"]
        self.base = Path(base) if base else resolve(".")
        self.staging = self.base / self.limits["staging_dir"]
        self.extracted = self.base / self.limits["extracted_dir"]
        self.outputs = self.base / self.limits["outputs_dir"]
        self._pipeline_loader = pipeline_loader
        self._pipeline: Pipeline | None = None
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}

    # ---- Models ------------------------------------------------------------------------------
    def pipeline(self) -> Pipeline:
        with self._lock:
            if self._pipeline is None:
                self._pipeline = self._pipeline_loader()
            return self._pipeline

    def status(self) -> dict:
        settings = load_config().get("level2", {})
        temporal, defense = level2.temporal_config(), level2.defense_config()
        out = {"ready": False, "error": None, "agents": {}, "limits": self.limits,
               "replay_schema_confirmed": settings.get("replay_schema_confirmed", False),
               "expiry_boundary_confirmed": settings.get("expiry_boundary_confirmed", False),
               "expiry_boundary": defense.expiry_boundary, "window_seconds": temporal.window_seconds,
               "window_records": temporal.window_records}
        try:
            p = self.pipeline()
        except FileNotFoundError as exc:
            out["error"] = (f"Model file not found: {exc.filename or exc}. Models are not in git; run "
                            "scripts/run_all.sh or copy models/ from a teammate.")
            return out
        except Exception as exc:  # A library version mismatch can surface as almost anything when unpickling.
            out["error"] = f"The saved models could not be loaded: {exc}"
            return out
        out["ready"] = True
        out["agents"] = {
            "A1": f"{p.threat.model_version}, {len(p.threat.pipeline.columns_)} features",
            "A2": f"{p.anomaly.detector}, flags at percentile {p.anomaly.threshold}, {len(p.anomaly.pipeline.columns_)} features",
            "A3": "Deterministic evidence rules",
            "A4": "Causal correlation on observed endpoints",
            "A5": "Temporary simulated SOURCE_IP BLOCK policies",
        }
        return out

    def _model_columns(self) -> tuple[list[str], set[str]]:
        p = self.pipeline()
        required = sorted(set(p.threat.pipeline.columns_) | set(p.anomaly.pipeline.columns_))
        return required, set(required) | set(p.threat.pipeline.dropped_) | set(p.anomaly.pipeline.dropped_)

    # ---- Upload ------------------------------------------------------------------------------
    def _new_run_id(self) -> str:
        year = datetime.now().year
        self.staging.mkdir(parents=True, exist_ok=True)
        for number in range(1, 100_000):
            run_id = f"RUN_{year}_{number:03d}"
            try:
                (self.staging / run_id).mkdir()  # Atomic: two uploads can never share a directory.
                return run_id
            except FileExistsError:
                continue
        raise LocalDataError("no free run id", 500)

    def _manifests(self) -> list[dict]:
        if not self.staging.exists():
            return []
        return [json.loads(p.read_text()) for p in sorted(self.staging.glob("RUN_*/manifest.json"))]

    def create_upload(self, dataset_type: str, uploads: list) -> dict:
        """uploads is a list of (client file name, relative path, readable binary stream)."""
        if dataset_type not in DATASET_TYPES:
            raise LocalDataError(f"dataset_type must be one of {DATASET_TYPES}")
        if not uploads:
            raise LocalDataError("no files were sent")
        earlier = self._manifests()
        run_id = self._new_run_id()
        try:
            return self._store_upload(run_id, dataset_type, uploads, earlier)
        except Exception:
            shutil.rmtree(self.staging / run_id, ignore_errors=True)
            shutil.rmtree(self.extracted / run_id, ignore_errors=True)
            raise

    def _store_upload(self, run_id: str, dataset_type: str, uploads: list, earlier: list[dict]) -> dict:
        run_dir = self.staging / run_id
        originals = run_dir / "originals"
        originals.mkdir()
        files, data_files, seen = [], [], set()
        for index, (name, relative, stream) in enumerate(uploads, start=1):
            file_id = f"F{index:03d}"
            safe = _safe_name(name)
            suffix = Path(safe).suffix.lower()
            if suffix not in DATA_SUFFIXES | {".zip"}:
                raise LocalDataError(f"{safe}: only CSV, Parquet and ZIP files are accepted")
            target = originals / f"{file_id}_{safe}"
            digest = hashlib.sha256()
            size = 0
            with target.open("xb") as sink:
                while chunk := stream.read(CHUNK):
                    size += len(chunk)
                    if size > self.limits["max_upload_bytes"]:
                        raise LocalDataError(f"{safe} is larger than the limit of "
                                             f"{self.limits['max_upload_bytes']:,} bytes", 413)
                    digest.update(chunk)
                    sink.write(chunk)
            target.chmod(0o444)  # The received original is preserved as sent.
            sha256 = digest.hexdigest()
            if sha256 in seen:
                raise LocalDataError(f"{safe} was selected twice with identical content")
            seen.add(sha256)
            entry = {"file_id": file_id, "name": safe, "relative_path": str(relative or safe), "size": size,
                     "sha256": sha256, "kind": suffix.lstrip("."), "skipped_members": [],
                     "previous_uploads": [{"run_id": m["run_id"], "dataset_type": m["dataset_type"]} for m in earlier
                                          if any(f["sha256"] == sha256 for f in m["files"])]}
            if suffix == ".zip":
                unpacked = extract_archive(target, self.extracted / run_id / file_id, self.limits)
                entry["skipped_members"] = unpacked["skipped"]
                for number, item in enumerate(unpacked["extracted"], start=1):
                    data_files.append({"id": f"{file_id}.{number:02d}", "display": f"{safe} / {item['member']}",
                                       "path": str(item["path"].relative_to(self.base)), "size": item["size"]})
            else:
                data_files.append({"id": file_id, "display": str(relative or safe),
                                   "path": str(target.relative_to(self.base)), "size": size})
            files.append(entry)
        if not data_files:
            raise LocalDataError("the selected files contain no CSV or Parquet data")
        identity = hashlib.sha256("".join(sorted(seen)).encode()).hexdigest()
        manifest = {"run_id": run_id, "created": now(), "dataset_type": dataset_type, "identity": identity,
                    "files": files, "data_files": data_files,
                    "duplicate_of": [m["run_id"] for m in earlier
                                     if m.get("identity") == identity and m["dataset_type"] == dataset_type]}
        (run_dir / "manifest.json").write_text(to_json(manifest))
        return manifest

    def manifest(self, run_id: str) -> dict:
        if not RUN_ID.match(run_id):
            raise LocalDataError(f"not a run id: {run_id!r}", 404)
        path = self.staging / run_id / "manifest.json"
        if not path.exists():
            raise LocalDataError(f"unknown run {run_id}", 404)
        return json.loads(path.read_text())

    # ---- Inspection --------------------------------------------------------------------------
    def _selected(self, manifest: dict, include: list[str] | None) -> list[dict]:
        known = {d["id"]: d for d in manifest["data_files"]}
        if include is None:
            return list(known.values())
        unknown = [i for i in include if i not in known]
        if unknown:
            raise LocalDataError(f"unknown file ids: {unknown}")
        return [known[i] for i in include]

    def _assemble(self, manifest: dict, include: list[str] | None, problems: list[str]) -> pd.DataFrame | None:
        flows, ordering = [], []
        required, _ = self._model_columns()
        for item in self._selected(manifest, include):
            frame = standardise_columns(read_flow_frame(self.base / item["path"]))
            (flows if set(required) & set(frame.columns) else ordering).append(frame)
        return combine_job(flows, ordering, problems)

    def inspect(self, run_id: str, include: list[str] | None = None) -> dict:
        manifest = self.manifest(run_id)
        dataset_type = manifest["dataset_type"]
        selected = {d["id"] for d in self._selected(manifest, include)}
        out = {"run_id": run_id, "dataset_type": dataset_type, "inspected_at": now(), "models": self.status(),
               "files": [], "job": None, "uploads": manifest["files"]}
        job = {"selected": sorted(selected), "records": 0, "feature_count": 0, "record_ids": False, "timestamps": False,
               "source_ip": False, "destination_ip": False, "source_port": False, "destination_port": False,
               "protocol": False, "missing_columns": [], "invalid_values": {}, "models_compatible": False,
               "errors": [], "warnings": [], "order": None, "order_conflict": False, "mode": None,
               "a4_a5_available": False, "a4_a5_missing": [], "temporal": None, "runnable": False}
        out["job"] = job
        if not out["models"]["ready"]:
            job["errors"].append(out["models"]["error"])
            return out
        required, known = self._model_columns()
        frames = []
        for item in manifest["data_files"]:
            try:
                raw = read_flow_frame(self.base / item["path"])
                report = inspect_frame(raw, item["display"], required, known)
            except Exception as exc:  # A damaged upload is reported to the operator, not raised as a server fault.
                report = {"display": item["display"], "role": "unknown", "records": 0, "columns": 0,
                          "errors": [f"could not be read as flow data: {exc}"], "warnings": []}
                raw = None
            apply_dataset_rules(report, dataset_type)
            report |= {"id": item["id"], "size": item["size"], "included": item["id"] in selected}
            out["files"].append(report)
            if report["included"]:
                frames.append((report, raw))
                job["errors"] += [f"{report['display']}: {e}" for e in report["errors"]]
                job["warnings"] += [f"{report['display']}: {w}" for w in report["warnings"]]
                job["invalid_values"] |= report.get("invalid_values", {})
                job["missing_columns"] += report.get("missing_required_features", [])
        for entry in manifest["files"]:
            for earlier in entry["previous_uploads"]:
                if earlier["dataset_type"] != dataset_type:
                    job["warnings"].append(f"{entry['name']} was also uploaded as {earlier['dataset_type']} data in "
                                           f"{earlier['run_id']}. Check this file's purpose.")
        if manifest["duplicate_of"]:
            job["warnings"].append(f"These files were already uploaded as {manifest['duplicate_of']}.")
        if dataset_type == TRAINING:
            job["warnings"].append("Training data can only be inspected here. The saved models are never refitted by "
                                   "this screen, so training a model stays a deliberate step on the command line.")
        problems: list[str] = []
        unreadable = any(r["role"] == "unknown" for r, _ in frames)
        combined = None
        if frames and not unreadable:
            flows = [standardise_columns(raw) for r, raw in frames if r["role"] == "flows"]
            ordering = [standardise_columns(raw) for r, raw in frames if r["role"] == "ordering"]
            combined = combine_job(flows, ordering, problems)
        elif not frames:
            problems.append("no file is selected")
        job["errors"] += problems
        job["models_compatible"] = bool(frames) and not job["missing_columns"] and not job["invalid_values"]
        if combined is not None and not job["errors"]:
            self._describe_job(job, combined, dataset_type)
        job["runnable"] = not job["errors"] and combined is not None and dataset_type != TRAINING
        (self.staging / run_id / "inspection.json").write_text(to_json(out))
        return out

    def _describe_job(self, job: dict, combined: pd.DataFrame, dataset_type: str) -> None:
        partition = split_representations(combined.drop(columns=[load_config()["data"]["label_column"]], errors="ignore"))
        meta = partition.metadata
        job["records"] = len(meta)
        job["feature_count"] = partition.features.shape[1]
        job["record_ids"] = True
        for column in ("source_ip", "destination_ip", "source_port", "destination_port", "protocol"):
            job[column] = column in meta
        settings = level2.order_settings()
        try:
            job["order"] = check_replay_order(meta, "report", settings["dayfirst"])
        except (ReplayOrderError, ValueError) as exc:
            job["errors"].append(f"replay order: {exc}")
            return
        job["order_conflict"] = bool(job["order"]["ambiguities"]) and settings["on_conflict"] == "error"
        job["warnings"] += [f"replay order: {a}" for a in job["order"]["ambiguities"]]
        times = _time_report(meta, settings["dayfirst"])
        job["timestamps"] = times["available"]
        job["timestamp_order"] = times
        job["temporal"] = temporal_note(times["available"])
        gaps = endpoint_gaps(meta)
        job["a4_a5_missing"] = gaps
        job["a4_a5_available"] = not gaps
        job["mode"] = FULL if not gaps else CLASSIFICATION_ONLY
        if gaps:
            job["warnings"].append(f"Agents 4 and 5 are off because {'; '.join(gaps)}. No addresses are invented, so "
                                   "only classification.csv can be produced. Agents 1 to 3 still run on every record.")
        else:
            empty = int(meta["source_ip"].astype(str).str.strip().str.lower().isin(
                {"", "nan", "none", "nat", "<na>"}).sum())
            if empty:
                job["warnings"].append(f"{empty} records have no source_ip. They cannot match a policy, and are "
                                       "recorded as ALLOW without contributing evidence to Agents 4 and 5.")
        if not times["available"]:
            job["warnings"].append(job["temporal"]["detail"])

    # ---- Pipeline run ------------------------------------------------------------------------
    def start_run(self, run_id: str, include: list[str] | None = None, on_order_conflict: str | None = None,
                  allow_duplicate: bool = False) -> dict:
        manifest = self.manifest(run_id)
        with self._lock:
            current = self._jobs.get(run_id)
            if current is not None and current.state in ("queued", "running"):
                raise LocalDataError(f"{run_id} is already running", 409)
            if (self._summary(run_id) or {}).get("status") == "completed":
                raise LocalDataError(f"{run_id} has already been processed. Its outputs are unchanged.", 409)
            repeats = [r for r in manifest["duplicate_of"] if (self._summary(r) or {}).get("status") == "completed"]
            if repeats and not allow_duplicate:
                raise LocalDataError(f"Identical files were already processed as {repeats}. Confirm to process them again.", 409)
            inspection = self.inspect(run_id, include)
            job_report = inspection["job"]
            if manifest["dataset_type"] == TRAINING:
                raise LocalDataError("Training data can only be inspected here; this screen never refits the models.", 409)
            if not job_report["runnable"]:
                raise LocalDataError("This input failed inspection: " + "; ".join(job_report["errors"]))
            if job_report["order_conflict"] and on_order_conflict != "report":
                raise LocalDataError("The replay order is ambiguous: " + "; ".join(job_report["order"]["ambiguities"])
                                     + " Ask the organisers, or confirm to proceed with this reported.", 409)
            job = Job(run_id, mode=job_report["mode"], total=job_report["records"])
            self._jobs[run_id] = job
        request = {"include": include, "on_order_conflict": on_order_conflict, "mode": job.mode,
                   "started": now(), "temporal": job_report["temporal"]}
        threading.Thread(target=self._execute, args=(job, manifest, request), daemon=True).start()
        return self.run_view(run_id)

    def _execute(self, job: Job, manifest: dict, request: dict) -> None:
        started = time.perf_counter()
        out_dir = self.outputs / job.run_id
        try:
            shutil.rmtree(out_dir, ignore_errors=True)  # Only a failed attempt at this same run can be here.
            out_dir.mkdir(parents=True)
            job.state, job.stage = "running", "loading"
            job.emit("stage", stage="loading")
            problems: list[str] = []
            combined = self._assemble(manifest, request["include"], problems)
            if combined is None:
                raise LocalDataError("; ".join(problems))
            # Labels are removed here, before any agent runs. Nothing below can read them.
            partition = replace(split_representations(combined), labels=None)
            conflict = request["on_order_conflict"]
            if job.mode == FULL:
                result = self._run_full(job, partition, conflict, out_dir)
            else:
                result = self._run_classification_only(job, partition, conflict, out_dir)
            job.stage = "validating"
            job.emit("stage", stage="validating")
            summary = self._build_summary(job, manifest, request, result, time.perf_counter() - started)
            (out_dir / "run_summary.json").write_text(to_json(summary))
            events = [e for e in job.events if e["type"] in ("policy", "stage")]
            (out_dir / "replay_events.jsonl").write_text("".join(json.dumps(e, default=_json_value) + "\n" for e in events))
            report = self.validate(job.run_id)
            job.state, job.stage = "completed", "completed"
            job.emit("done", state="completed", rules_passed=report["rules_passed"])
        except Exception as exc:  # Every failure must reach the operator, with no outputs presented as ready.
            job.state, job.stage, job.error = "failed", "failed", str(exc)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "run_summary.json").write_text(to_json({"run_id": job.run_id, "status": "failed", "error": str(exc)}))
            job.emit("done", state="failed", error=str(exc))

    def _agent(self, job: Job, agent: str, state: str, detail: str = "") -> None:
        job.agents[agent] = {"state": state, "detail": detail}
        job.emit("agent", agent=agent, state=state, detail=detail)

    def _on_agent(self, job: Job, stage: str):
        def report(agent: str, state: str) -> None:
            job.stage = stage
            self._agent(job, agent, state, f"{job.total:,} records" if state == "done" else "")
        return report

    def _run_full(self, job: Job, partition, conflict: str | None, out_dir: Path) -> dict:
        drained = [0]

        def drain(engine) -> None:
            log = engine.defense.log
            for entry in log[drained[0]:]:
                job.emit("policy", **entry)
            drained[0] = len(log)

        def on_step(i, step, engine) -> None:
            job.processed = i + 1
            drain(engine)
            if job.processed % PROGRESS_EVERY == 0 or job.processed == job.total:
                self._replay_progress(job, engine)

        pipeline = self.pipeline()
        job.stage = "scoring"
        scored = level2.score_records(pipeline, partition, conflict, self._on_agent(job, "scoring"))
        job.stage = "replay"
        job.emit("stage", stage="replay")
        for agent in ("A4", "A5"):
            self._agent(job, agent, "running")
        engine = level2.replay_scored(scored, on_step=on_step)
        drain(engine)
        self._replay_progress(job, engine)
        for agent in ("A4", "A5"):
            job.agents[agent]["state"] = "done"
            self._agent(job, agent, "done", job.agents[agent]["detail"])
        result = {"classification": scored.classification, **level2.replay_outputs(engine), "order": scored.order,
                  "order_report": scored.order_report, "scored": scored,
                  "expiry_boundary": engine.defense.config.expiry_boundary}
        job.stage = "causality"
        job.emit("stage", stage="causality")
        causality = level2.check_prefix_invariance(scored, result)
        job.stage = "writing"
        job.emit("stage", stage="writing")
        level2.write_outputs(result, out_dir)
        return {"mode": FULL, "tradeoff": self._tradeoff(scored.classification, result["replay"]), "records": len(scored), "order_report": scored.order_report, "causality": causality,
                "state": result["state"], "policies": len(result["policy"]), "groups": len(result["groups"]),
                "classes": scored.classification["predicted_class"].value_counts().to_dict(),
                "statuses": scored.classification["evidence_status"].value_counts().to_dict(),
                "actions": result["replay"]["action"].value_counts().to_dict(),
                "expiry_boundary": result["expiry_boundary"],
                "timestamp_regressions": engine.temporal.timestamp_regressions[:50],
                "unattributed_records": sum(s.strip().lower() in {"", "nan", "none", "nat", "<na>"}
                                            for s in scored.inputs["source"])}

    @staticmethod
    def _tradeoff(classification: pd.DataFrame, replay: pd.DataFrame) -> dict:
        """Containment against collateral, by Agent 1's call. The hidden set has no labels, so this is not truth."""
        calls = classification.set_index("record_id")["predicted_class"].reindex(replay["record_id"].astype(str)).to_numpy()
        blocked = replay["action"].to_numpy() == "BLOCK"
        attack = calls != "BENIGN"
        by_class = {str(c): {"allow": int(((calls == c) & ~blocked).sum()), "block": int(((calls == c) & blocked).sum())}
                    for c in sorted(set(calls))}
        return {"basis": "Agent 1's predicted class, not ground truth", "by_class": by_class,
                "called_attack": int(attack.sum()), "called_attack_blocked": int((attack & blocked).sum()),
                "called_attack_allowed": int((attack & ~blocked).sum()),
                "called_benign": int((~attack).sum()), "called_benign_blocked": int((~attack & blocked).sum())}

    @staticmethod
    def _replay_progress(job: Job, engine) -> None:
        defense, temporal = engine.defense, engine.temporal
        job.agents["A4"]["detail"] = f"{len(temporal.groups):,} groups, {temporal.suspicious_observed:,} suspicious flows"
        job.agents["A5"]["detail"] = (f"{len(defense.policies):,} policies, {defense.active_count:,} active, "
                                      f"{len(defense.expired_policies):,} expired")
        blocked = sum(a["action"] == "BLOCK" for a in engine.action_log[-PROGRESS_EVERY:])
        job.emit("progress", stage=job.stage, processed=job.processed, total=job.total,
                 groups=len(temporal.groups), policies=len(defense.policies), active=defense.active_count,
                 expired=len(defense.expired_policies), blocked_recent=blocked, agents=job.agents)

    def _run_classification_only(self, job: Job, partition, conflict: str | None, out_dir: Path) -> dict:
        reason = "Unavailable: this input has no usable source_ip and destination_ip"
        job.stage = "scoring"
        classification, report = level2.score_classification(self.pipeline(), partition, conflict,
                                                             self._on_agent(job, "scoring"))
        job.processed = len(classification)
        for agent in ("A4", "A5"):
            self._agent(job, agent, "unavailable", reason)
        job.stage = "writing"
        job.emit("stage", stage="writing")
        level2.write_classification(classification, out_dir)
        return {"mode": CLASSIFICATION_ONLY, "records": len(classification), "order_report": report, "causality": [],
                "classes": classification["predicted_class"].value_counts().to_dict(),
                "statuses": classification["evidence_status"].value_counts().to_dict()}

    def _build_summary(self, job: Job, manifest: dict, request: dict, result: dict, seconds: float) -> dict:
        settings = load_config().get("level2", {})
        return {"run_id": job.run_id, "status": "completed", "dataset_type": manifest["dataset_type"],
                "mode": result["mode"], "records": result["records"], "started": request["started"],
                "finished": now(), "seconds": round(seconds, 2), "input_files": manifest["files"],
                "included_files": request["include"] or [d["id"] for d in manifest["data_files"]],
                "input_identity_sha256": manifest["identity"], "temporal": request["temporal"],
                "labels_read": False, "models_refitted": False, "causality_problems": result["causality"],
                "replay_schema_confirmed": settings.get("replay_schema_confirmed", False),
                "expiry_boundary_confirmed": settings.get("expiry_boundary_confirmed", False),
                **{k: v for k, v in result.items() if k not in ("mode", "records", "causality")}}

    # ---- Results ------------------------------------------------------------------------------
    def _summary(self, run_id: str) -> dict | None:
        path = self.outputs / run_id / "run_summary.json"
        return json.loads(path.read_text()) if path.exists() else None

    def validate(self, run_id: str) -> dict:
        """Read the written files back from disk, and check them against the input they describe."""
        manifest = self.manifest(run_id)
        summary = self._summary(run_id)
        if summary is None or summary.get("status") != "completed":
            raise LocalDataError(f"{run_id} has no completed outputs to validate", 409)
        out_dir = self.outputs / run_id
        assembly: list[str] = []
        combined = self._assemble(manifest, summary["included_files"], assembly)
        if combined is None:
            raise LocalDataError("; ".join(assembly))
        meta = split_representations(combined).metadata
        meta = meta.iloc[replay_order(meta)]
        mode = summary["mode"]
        problems = list(summary.get("causality_problems", []))
        if mode == FULL:
            frames, read_problems = level2.read_outputs(out_dir)
            problems += read_problems or level2.validate_outputs(
                frames["classification"], frames["policy"], frames["replay"], meta["flow_id"], meta["source_ip"],
                summary.get("expiry_boundary"))
            if not read_problems:
                problems += level2.stage_page_problems(out_dir, meta)
        else:
            frames, read_problems = level2.read_outputs(out_dir, ("classification",))
            problems += read_problems or level2.validate_classification(frames["classification"], meta["flow_id"])
        settings = load_config().get("level2", {})
        blockers = []
        if problems:
            blockers.append(f"{len(problems)} rule violations")
        if manifest["dataset_type"] != HIDDEN:
            blockers.append(f"this is a {manifest['dataset_type']} run, not the hidden replay set")
        if mode != FULL:
            blockers.append("Agents 4 and 5 did not run, so policies.csv and replay.csv do not exist")
        if not settings.get("replay_schema_confirmed", False):
            blockers.append("the replay.csv header is not confirmed by the organisers")
        if not settings.get("expiry_boundary_confirmed", False):
            blockers.append(f"the {summary.get('expiry_boundary') or 'configured'} expiry convention is not confirmed by the organisers")
        if (summary.get("order_report") or {}).get("ambiguities"):
            blockers.append("the replay order has unresolved ambiguities")
        report = {"run_id": run_id, "validated_at": now(), "mode": mode, "records_expected": len(meta),
                  "rules_passed": not problems, "problems": problems[:200], "problem_count": len(problems),
                  "rules_checked": RULES_CHECKED if mode == FULL else RULES_CHECKED[:2],
                  "labels_read": summary.get("labels_read", False), "submission_ready": not blockers,
                  "blockers": blockers}
        (out_dir / "validation_report.json").write_text(to_json(report))
        return report

    def dashboard_input(self, run_id: str):
        """A completed run's input without labels, and its name, for the dashboard screens to replay."""
        manifest = self.manifest(run_id)
        summary = self._summary(run_id)
        if summary is None or summary.get("status") != "completed":
            raise LocalDataError(f"{run_id} has not completed", 409)
        problems: list[str] = []
        combined = self._assemble(manifest, summary["included_files"], problems)
        if combined is None:
            raise LocalDataError("; ".join(problems))
        names = [f["name"] for f in manifest["files"]]
        return replace(split_representations(combined), labels=None), f"{run_id} ({', '.join(names)})"

    def output_path(self, run_id: str, name: str) -> Path:
        self.manifest(run_id)
        if name not in EXPORT_FILES:
            raise LocalDataError(f"{name} is not an output file", 404)
        path = self.outputs / run_id / name
        if not path.exists():
            raise LocalDataError(f"{name} was not produced by {run_id}", 404)
        return path

    def submission(self, run_id: str) -> list[dict]:
        """The files the organisers ask for, in their names and headers, and whether each one exists."""
        schema = level2.csv_schema()
        items = []
        for name, columns in schema.items():
            file = level2.output_file(name)
            path = self.outputs / run_id / file
            rows = None
            if path.exists():
                with path.open() as handle:
                    rows = max(sum(1 for _ in handle) - 1, 0)
            items.append({"name": file, "exists": path.exists(), "rows": rows, "size": path.stat().st_size if path.exists() else None,
                          "columns": list(columns.values())})
        return items

    def archive(self, run_id: str, submission_only: bool = False) -> Path:
        self.manifest(run_id)
        files = [f for f in self.submission(run_id) if f["exists"]] if submission_only else self.list_outputs(run_id)
        if not files:
            raise LocalDataError(f"{run_id} has no outputs", 404)
        path = self.outputs / f"{run_id}_{'submission' if submission_only else 'outputs'}.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for item in files:
                z.write(self.outputs / run_id / item["name"], f"{run_id}/{item['name']}")
        return path

    def list_outputs(self, run_id: str) -> list[dict]:
        out_dir = self.outputs / run_id
        return [{"name": name, "size": (out_dir / name).stat().st_size} for name in EXPORT_FILES
                if (out_dir / name).exists()]

    def _preview(self, run_id: str) -> dict:
        previews = {}
        for name in ("classification", "policy", "replay"):
            file = level2.output_file(name)
            path = self.outputs / run_id / file
            if path.exists():
                frame = pd.read_csv(path, dtype=str, keep_default_na=False, nrows=PREVIEW_ROWS)
                previews[file] = {"columns": list(frame.columns), "rows": frame.values.tolist()}
        return previews

    def run_view(self, run_id: str) -> dict:
        manifest = self.manifest(run_id)
        job = self._jobs.get(run_id)
        summary = self._summary(run_id)
        finished = job is None or job.state in ("completed", "failed")
        report_path = self.outputs / run_id / "validation_report.json"
        if job is not None:
            progress = job.public()
        elif summary is not None:  # The run finished before this server started.
            progress = {"state": summary["status"], "stage": summary["status"], "mode": summary.get("mode"),
                        "processed": summary.get("records", 0), "total": summary.get("records", 0), "agents": {},
                        "error": summary.get("error")}
        else:
            progress = {"state": "not_started", "stage": "not_started", "mode": None, "processed": 0, "total": 0,
                        "agents": {}, "error": None}
        view = {"run_id": run_id, "dataset_type": manifest["dataset_type"], **progress,
                "outputs": [], "preview": {}, "summary": None, "validation": None, "submission": []}
        if finished and summary is not None and summary.get("status") == "completed":
            view |= {"outputs": self.list_outputs(run_id), "submission": self.submission(run_id), "preview": self._preview(run_id), "summary": summary,
                     "validation": json.loads(report_path.read_text()) if report_path.exists() else None}
        return view

    def events_since(self, run_id: str, seq: int) -> list[dict]:
        job = self._jobs.get(run_id)
        return [e for e in job.events[seq:]] if job else []

    def is_finished(self, run_id: str) -> bool:
        job = self._jobs.get(run_id)
        return job is None or job.state in ("completed", "failed")
