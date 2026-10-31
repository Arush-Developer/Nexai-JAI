"""Tune Agent 5's thresholds on labelled eval flows with SYNTHETIC endpoints.

The pack has no endpoints, so the only thing measured is how thresholds behave under invented address
layouts. Choices are made on the first half of eval and reported on the second half. Labels score the
results here and never enter the replay. Models are not touched. Re-check once the hidden set's address
layout can be inspected (without labels), because these numbers depend on the layout.

Writes data/reports/level2_tuning.json.
"""
from __future__ import annotations

import itertools
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backend.config import load_config, resolve  # noqa: E402
from backend.preprocessing.data_processor import load_flows  # noqa: E402
from backend.services.level2 import (  # noqa: E402
    ScoredRecords, defense_config, replay_outputs, replay_scored, score_records, temporal_config)
from backend.services.model_service import Pipeline  # noqa: E402
from scripts.rehearse_level2 import synthetic_endpoints  # noqa: E402

SCENARIOS = {"per_class": 7, "single_attacker": 11}
GRID = {
    "min_attack_flows_multi_signal": [2, 3, 5],
    "min_attack_flows_classifier_only": [3, 5, 10],
    "min_attack_share": [0.5, 0.6, 0.8],
    "ttl_records": [500, 2000, 8000],
}
BENIGN_COST = 5.0  # One wrongly blocked benign flow is weighed against five blocked attack flows.


def slice_scored(s: ScoredRecords, start: int, stop: int) -> ScoredRecords:
    picked = range(start, stop)
    return replace(s, inputs={k: [v[i] for i in picked] for k, v in s.inputs.items()},
                   final_class=s.final_class[start:stop], confidence=s.confidence[start:stop],
                   scores=s.scores[start:stop], is_anomaly=s.is_anomaly[start:stop],
                   evidence_status=s.evidence_status[start:stop],
                   classification=s.classification.iloc[start:stop].reset_index(drop=True))


def measure(scored: ScoredRecords, truth: np.ndarray, temporal, defense) -> dict:
    out = replay_outputs(replay_scored(scored, temporal, defense))
    blocked = out["replay"]["action"].to_numpy() == "BLOCK"
    attack = truth != "BENIGN"
    return {"attack_blocked": float(blocked[attack].mean()), "benign_blocked": float(blocked[~attack].mean()),
            "benign_blocked_flows": int(blocked[~attack].sum()), "policies": len(out["policy"]),
            "groups": len(out["groups"])}


def utility(m: dict) -> float:
    return m["attack_blocked"] - BENIGN_COST * m["benign_blocked"]


def main() -> None:
    cfg = load_config()["data"]
    raw = pd.read_parquet(resolve(cfg["eval_parquet"]))
    labels = raw[cfg["label_column"]]
    pipeline = Pipeline.load()
    temporal, base = temporal_config(), defense_config()
    tune, hold, truth = {}, {}, {}
    for name, seed in SCENARIOS.items():
        ends = synthetic_endpoints(labels, seed, name)
        path = resolve(cfg["processed_dir"]) / f"tuning_{name}.parquet"
        raw.drop(columns=[cfg["label_column"]]).assign(source_ip=ends["source_ip"], destination_ip=ends["destination_ip"],
                                                       seq=np.arange(len(raw))).to_parquet(path, index=False)
        scored = score_records(pipeline, load_flows(path))
        path.unlink()
        truth[name] = labels.to_numpy()[scored.order]
        cut = len(scored) // 2
        tune[name], hold[name] = slice_scored(scored, 0, cut), slice_scored(scored, cut, len(scored))
    cut = len(labels) // 2
    rows = []
    for values in itertools.product(*GRID.values()):
        defense = replace(base, **dict(zip(GRID, values)))
        scores = {n: measure(tune[n], truth[n][:cut], temporal, defense) for n in SCENARIOS}
        rows.append({"settings": dict(zip(GRID, values)), "worst_utility": min(utility(m) for m in scores.values()),
                     "tune": scores})
    rows.sort(key=lambda r: -r["worst_utility"])
    best = rows[0]
    # The settings in use may sit outside this grid, after a change made on purpose. Report them as they are.
    current = next((r for r in rows if all(r["settings"][k] == getattr(base, k) for k in GRID)), None)
    if current is None:
        current = {"settings": {k: getattr(base, k) for k in GRID}, "worst_utility": None, "tune": None,
                   "note": "outside the grid, so not scored by this run"}
    report = {
        "warning": "SYNTHETIC endpoints on real flows. Compares thresholds under invented address layouts only; "
                   "not a prediction of hidden-set performance.",
        "objective": f"best worst-scenario value of attack_blocked - {BENIGN_COST} * benign_blocked, on the first half of eval",
        "grid_size": len(rows),
        "chosen": best["settings"], "chosen_tune": best["tune"],
        "chosen_holdout": {n: measure(hold[n], truth[n][cut:], temporal, replace(base, **best["settings"])) for n in SCENARIOS},
        "previous": current["settings"], "previous_tune": current["tune"],
        "previous_holdout": {n: measure(hold[n], truth[n][cut:], temporal, base) for n in SCENARIOS},
        "top_5": [{"settings": r["settings"], "worst_utility": r["worst_utility"]} for r in rows[:5]],
    }
    (resolve(load_config()["data"]["reports_dir"]) / "level2_tuning.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
