"""Loading, cleaning and leakage control for the Cyber flow partitions.

Two representations are kept apart:
  * ML features: the numeric flow statistics only (see FeaturePipeline).
  * Network metadata: record_id plus any endpoint columns present in the file.
Labels are returned separately and never reach a feature matrix.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import StratifiedShuffleSplit

from backend.config import load_config, resolve

PORT_FEATURE = "Destination Port"


@dataclass
class Partition:
    features: pd.DataFrame
    metadata: pd.DataFrame
    labels: pd.Series | None


def map_labels(raw: pd.Series) -> pd.Series:
    """Apply the explicit label mapping. Unknown labels raise rather than being coerced."""
    mapping = load_config()["labels"]["mapping"]
    cleaned = raw.astype("string").str.strip()
    unknown = sorted(set(cleaned.dropna()) - set(mapping))
    if unknown or cleaned.isna().any():
        raise ValueError(f"unmapped labels (confirm with Arush before training): {unknown or 'null values'}")
    return cleaned.astype(str).map(mapping)


def standardise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Strip stray whitespace, and accept "Source IP"-style spellings of the metadata columns."""
    cfg = load_config()["data"]
    meta = {**{c.lower(): c for c in (cfg["id_column"], cfg["label_column"], *cfg["metadata_columns"])},
            **cfg.get("metadata_aliases", {})}
    renamed = {c: meta.get(c.strip().lower().replace(" ", "_"), c.strip()) for c in df.columns}
    # "Destination Port" stays a flow feature; it is copied into the metadata in split_representations.
    renamed = {c: c.strip() if c.strip() == PORT_FEATURE else new for c, new in renamed.items()}
    return df.rename(columns=renamed)


class ReplayOrderError(ValueError):
    """The supplied records do not describe one unambiguous chronological order."""


def parse_timestamps(values: pd.Series, dayfirst: bool = False) -> np.ndarray:
    """Seconds as floats, NaN where unreadable. Numeric columns are taken as already being seconds."""
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().all():
        return numeric.to_numpy(dtype=np.float64)
    parsed = pd.to_datetime(values, errors="coerce", format="mixed", dayfirst=dayfirst, utc=True)
    return (parsed - pd.Timestamp(0, tz="UTC")).dt.total_seconds().to_numpy(dtype=np.float64)


def replay_order(metadata: pd.DataFrame) -> np.ndarray:
    """Row positions in the supplied order: the organisers' seq column if present, otherwise file order.

    Timestamps and record ids never reorder anything. check_replay_order reports any conflict with them.
    """
    if "seq" in metadata:
        return np.argsort(pd.to_numeric(metadata["seq"]).to_numpy(), kind="stable")
    return np.arange(len(metadata))


def check_replay_order(metadata: pd.DataFrame, on_conflict: str = "error", dayfirst: bool = False) -> dict:
    """Validate the supplied order before any record is replayed.

    Duplicate record ids or an unusable seq always raise, because no order can be derived from them.
    Other conflicts raise when on_conflict is "error"; with "report" they are returned as ambiguities
    and replay follows replay_order, so a human decides whether the run can be submitted.
    """
    if on_conflict not in ("error", "report"):
        raise ValueError(f"on_conflict must be 'error' or 'report', not {on_conflict!r}")
    if metadata["flow_id"].duplicated().any():
        repeated = metadata["flow_id"][metadata["flow_id"].duplicated()].astype(str).head(5).tolist()
        raise ReplayOrderError(f"record ids repeat, for example {repeated}")
    ambiguities: list[str] = []
    authority = "file order"
    if "seq" in metadata:
        authority = "seq"
        seq = pd.to_numeric(metadata["seq"], errors="coerce")
        if seq.isna().any() or seq.duplicated().any():
            raise ReplayOrderError(f"seq is unusable: {int(seq.isna().sum())} missing, "
                                   f"{int(seq.duplicated().sum())} repeated")
        if not seq.is_monotonic_increasing:
            ambiguities.append("file row order differs from the seq column; seq is being followed")
    order = replay_order(metadata)
    timestamps_checked = "timestamp" in metadata
    if timestamps_checked:
        seconds = parse_timestamps(metadata["timestamp"], dayfirst)[order]
        unreadable = int(np.isnan(seconds).sum())
        if unreadable:
            ambiguities.append(f"{unreadable} timestamps could not be read")
        known = np.flatnonzero(~np.isnan(seconds))
        backwards = known[1:][np.diff(seconds[known]) < 0]
        if len(backwards):
            first = str(metadata["flow_id"].iat[order[backwards[0]]])
            ambiguities.append(f"{len(backwards)} records have a timestamp earlier than the record before them "
                               f"in {authority}, first at {first}")
    if ambiguities and on_conflict == "error":
        raise ReplayOrderError("; ".join(ambiguities) + ". Ask the organisers which order is authoritative, or "
                               "rerun with on_conflict='report' to proceed with this reported.")
    return {"authority": authority, "records": len(metadata), "timestamps_checked": timestamps_checked,
            "ambiguities": ambiguities}


def split_representations(df: pd.DataFrame) -> Partition:
    cfg = load_config()["data"]
    df = standardise_columns(df)
    id_col, label_col = cfg["id_column"], cfg["label_column"]
    meta_cols = [c for c in cfg["metadata_columns"] if c in df.columns]
    drop = {id_col, label_col, *meta_cols}
    features = df[[c for c in df.columns if c not in drop]]
    metadata = df[[id_col, *meta_cols]].rename(columns={id_col: "flow_id"}).reset_index(drop=True)
    # Destination Port is a flow feature in this pack, and also needed for campaign analysis.
    if "destination_port" not in metadata and PORT_FEATURE in features:
        metadata["destination_port"] = features[PORT_FEATURE].to_numpy()
    if "destination_port" in metadata and PORT_FEATURE not in features:
        features = features.assign(**{PORT_FEATURE: metadata["destination_port"].to_numpy()})
    labels = map_labels(df[label_col]).reset_index(drop=True) if label_col in df else None
    return Partition(features.reset_index(drop=True), metadata, labels)


def read_flow_frame(path) -> pd.DataFrame:
    """Read a CSV or Parquet flow file. Identifiers and addresses stay text, so 007 stays 007."""
    path = Path(path)
    if path.suffix.lower() != ".csv":
        return pd.read_parquet(path)
    cfg = load_config()["data"]
    text = {cfg["id_column"], "source_ip", "destination_ip"}
    names = standardise_columns(pd.read_csv(path, nrows=0))
    raw = pd.read_csv(path, nrows=0).columns
    dtype = {old: str for old, new in zip(raw, names.columns) if new in text}
    return pd.read_csv(path, dtype=dtype, low_memory=False)


def load_flows(path) -> Partition:
    """Load any flow file, labelled or not, such as the hidden set when it is released."""
    return split_representations(read_flow_frame(path))


def load_partition(which: str) -> Partition:
    key = {"training": "training_parquet", "eval": "eval_parquet"}[which]
    return load_flows(resolve(load_config()["data"][key]))


@dataclass
class FeaturePipeline:
    """Cleans features with statistics learned from the data passed to fit() only."""

    exclude: tuple[str, ...] = ()
    columns_: list[str] = field(default_factory=list)
    medians_: np.ndarray | None = None
    dropped_: dict[str, str] = field(default_factory=dict)

    def fit(self, X: pd.DataFrame) -> "FeaturePipeline":
        self.dropped_ = {c: "excluded" for c in self.exclude if c in X}
        kept: list[str] = []
        seen: dict[int, str] = {}
        for col in X.columns:
            if col in self.dropped_:
                continue
            values = self._clean_column(X[col])
            if np.nanmin(values) == np.nanmax(values):
                self.dropped_[col] = "constant in training"
                continue
            digest = int(pd.util.hash_pandas_object(pd.Series(values), index=False).sum())
            if digest in seen:
                self.dropped_[col] = f"duplicate of {seen[digest]}"
                continue
            seen[digest] = col
            kept.append(col)
        self.columns_ = kept
        self.medians_ = np.nanmedian(self._matrix(X), axis=0)
        return self

    @staticmethod
    def _clean_column(col: pd.Series) -> np.ndarray:
        values = col.to_numpy(dtype=np.float64)
        return np.where(np.isfinite(values), values, np.nan)

    def _matrix(self, X: pd.DataFrame) -> np.ndarray:
        missing = [c for c in self.columns_ if c not in X]
        if missing:
            raise ValueError(f"input is missing required feature columns: {missing}")
        values = X[self.columns_].to_numpy(dtype=np.float64)
        values[~np.isfinite(values)] = np.nan
        return values

    def transform(self, X: pd.DataFrame) -> np.ndarray:
        values = self._matrix(X)
        holes = np.isnan(values)
        if holes.any():
            values[holes] = np.take(self.medians_, np.nonzero(holes)[1])
        return values.astype(np.float32)


@dataclass
class InternalSplits:
    fit: np.ndarray
    selection: np.ndarray
    calibration: np.ndarray


def row_groups(features: pd.DataFrame) -> np.ndarray:
    return pd.util.hash_pandas_object(features, index=False).to_numpy()


def internal_splits(features: pd.DataFrame, labels: pd.Series, seed: int = 42,
                    fractions: tuple[float, float] = (0.15, 0.15)) -> InternalSplits:
    """Stratified 70/15/15 split of training data, grouped so identical rows stay together.

    Training holds 24,132 exact-duplicate feature rows; a plain split would let them straddle
    the boundary and inflate holdout scores.
    """
    groups = pd.DataFrame({"g": row_groups(features), "y": labels.to_numpy()})
    uniq = groups.drop_duplicates("g").reset_index(drop=True)
    holdout = fractions[0] + fractions[1]
    outer = StratifiedShuffleSplit(n_splits=1, test_size=holdout, random_state=seed)
    fit_idx, hold_idx = next(outer.split(uniq, uniq["y"]))
    hold = uniq.iloc[hold_idx]
    inner = StratifiedShuffleSplit(n_splits=1, test_size=fractions[1] / holdout, random_state=seed)
    sel_idx, cal_idx = next(inner.split(hold, hold["y"]))
    assign = {g: 0 for g in uniq.iloc[fit_idx]["g"]}
    assign.update({g: 1 for g in hold.iloc[sel_idx]["g"]})
    assign.update({g: 2 for g in hold.iloc[cal_idx]["g"]})
    which = groups["g"].map(assign).to_numpy()
    return InternalSplits(np.flatnonzero(which == 0), np.flatnonzero(which == 1), np.flatnonzero(which == 2))


def record_order_split(metadata: pd.DataFrame, labels: pd.Series, test_fraction: float = 0.3):
    """Per-class split on record_id order: earliest records fit, latest test.

    A proxy for temporal validation. record_id numbering clusters by class (112,710 label runs in
    456,737 rows), but the pack does not say it is chronological, so it is never a model input.
    """
    numeric = metadata["flow_id"].str.extract(r"(\d+)")[0].astype(np.int64).to_numpy()
    test = np.zeros(len(labels), dtype=bool)
    y = labels.to_numpy()
    for cls in np.unique(y):
        idx = np.flatnonzero(y == cls)
        cut = np.quantile(numeric[idx], 1 - test_fraction)
        test[idx[numeric[idx] > cut]] = True
    return np.flatnonzero(~test), np.flatnonzero(test)
