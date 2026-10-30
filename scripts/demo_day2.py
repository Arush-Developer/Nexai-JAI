"""Live Day 2 demonstration: runs all five agents and narrates what happens as the replay reaches it.

Every value printed comes from this execution. Nothing is pre-recorded. Without --input it uses the
official eval flows with the rehearsal's SYNTHETIC endpoints, and says so, because the participant pack
has no endpoints. Point --input at the organisers' file once it is released. Labels are never read.

Usage: .venv/bin/python scripts/demo_day2.py [--input PATH] [--out-dir data/processed/demo] [--pause 0]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from backend.config import load_config  # noqa: E402
from backend.preprocessing.data_processor import load_flows  # noqa: E402
from backend.services.level2 import (  # noqa: E402
    check_prefix_invariance, replay_outputs, replay_scored, score_records, validate_submission, write_outputs)
from backend.services.model_service import Pipeline  # noqa: E402

REHEARSAL = ROOT / "data/processed/rehearsal/flows.parquet"


class Narrator:
    """Prints one of each story beat, from the first policy onwards, while replay runs."""

    def __init__(self, pipeline, scored, pause: float):
        self.pipeline, self.scored, self.pause = pipeline, scored, pause
        self.policy: dict | None = None
        self.group_shown = self.block_shown = self.other_shown = self.expiry_shown = False
        self.previous: dict | None = None

    def banner(self, n: int, title: str) -> None:
        print(f"\n[{n}] {title}")
        time.sleep(self.pause)

    def agents(self, i: int) -> None:
        s = self.scored
        record, inputs = s.inputs["record_id"][i], s.inputs
        print(f"    record {record} (position {i}): {inputs['source'][i]} -> {inputs['destination'][i]}"
              f" port {inputs['port'][i]:g}")
        print(f"    A1 prediction : {s.final_class[i]} at confidence {s.confidence[i]:.4f}")
        print(f"    A2 anomaly    : score {s.scores[i]:.4f}, flagged={bool(s.is_anomaly[i])}")
        for item in self.pipeline.anomaly.supporting_features(s.features.iloc[[i]], 3)[0]:
            print(f"      evidence    : {item['feature']} = {item['measured_value']:g} "
                  f"against benign median {item['benign_median']:g} (robust z {item['robust_z']})")
        print(f"    A3 fusion     : {s.evidence_status[i]}")

    def __call__(self, i: int, step: dict, engine) -> None:
        source = self.scored.inputs["source"][i]
        record = self.scored.inputs["record_id"][i]
        if step["group"] is not None and not self.group_shown and step["suspicious"]:
            self.group_shown = True
            self.banner(6, "A4 forms its first temporal group")
            self.agents(i)
            g = engine.temporal.public_group(step["group"], 5)
            print(f"    A4 group      : {g['group_id']} members {g['member_record_ids']} ({g['member_count']} so far)")
            print(f"    A4 reason     : {g['correlation_reason']}")
        if step["activated_policy"] and self.policy is None:
            self.policy = step["activated_policy"]
            self.banner(7, "A5 activates a policy; the triggering record itself stays ALLOW")
            self.agents(i)
            print(f"    Action on this record: {step['action']} (applied_policy_id empty)")
            self.banner(8, "Policy id and source scope")
            for k in ("policy_id", "activated_after_record_id", "scope_type", "scope_value", "policy_action", "ttl_records", "group_id"):
                print(f"    {k:26}: {self.policy[k]}")
            print(f"    rationale: {self.policy['rationale']}")
        elif self.policy is not None:
            pid, scope = self.policy["policy_id"], self.policy["scope_value"]
            if not self.block_shown and step["applied_policy_id"] == pid:
                self.block_shown = True
                self.banner(9, "A later record from the same source arrives")
                self.agents(i)
                self.banner(10, "Active policy checked first: it is blocked, and adds no new evidence")
                print(f"    {record}: action {step['action']}, applied_policy_id {step['applied_policy_id']}, "
                      f"new group update {step['group'] is not None}, new policy {step['activated_policy'] is not None}")
            elif self.block_shown and not self.other_shown and source != scope and step["action"] == "ALLOW":
                self.other_shown = True
                self.banner(11, "A record from a different source, while that policy is active")
                print(f"    {record} from {source}: action {step['action']}, applied_policy_id empty")
            if not self.expiry_shown and self.policy["status"] == "EXPIRED":
                self.expiry_shown = True
                self.banner(12, "The policy reaches its expiry boundary")
                print(f"    {pid} expired at {record}: expires_after_record_id "
                      f"{self.policy['expires_after_record_id']}, blocked {self.policy['blocked_records']} records. "
                      f"Boundary convention: {engine.defense.config.expiry_boundary}.")
                print(f"    Later flows from {scope} are analysed again and may earn a new policy.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, default=REHEARSAL)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "data/processed/demo")
    ap.add_argument("--pause", type=float, default=0.0, help="Seconds to wait at each step while presenting.")
    args = ap.parse_args()
    if args.input == REHEARSAL:
        if not REHEARSAL.exists():
            raise SystemExit("Rehearsal flows are missing. Run scripts/rehearse_level2.py first, or pass --input.")
        print("NOTE: real eval flows and real model output, with SYNTHETIC endpoints (the pack has none).")

    pipeline = Pipeline.load()
    print(f"\n[1] Load replay data: {args.input}")
    partition = load_flows(args.input)
    print(f"    {len(partition.metadata):,} records. Endpoint columns: "
          f"{[c for c in partition.metadata.columns if c != 'flow_id']}")
    partition.labels = None
    scored = score_records(pipeline, partition)
    print(f"    Order authority: {scored.order_report['authority']}; ambiguities: {scored.order_report['ambiguities'] or 'none'}")
    print("    Classification is scored for every record first, independently of replay.")

    narrator = Narrator(pipeline, scored, args.pause)
    narrator.banner(2, "Replay begins; current record id is shown at each event below")
    print(f"    First record: {scored.inputs['record_id'][0]}")
    first_suspicious = next((i for i, s in enumerate(scored.evidence_status) if s != "AGREED_BENIGN"), None)
    if first_suspicious is not None:
        narrator.banner(3, "First suspicious record: A1 (step 3), A2 with supporting evidence (step 4), A3 (step 5)")
        narrator.agents(first_suspicious)

    engine = replay_scored(scored, on_step=narrator)
    outputs = replay_outputs(engine)
    result = {"classification": scored.classification, **outputs, "order": scored.order,
              "order_report": scored.order_report, "expiry_boundary": engine.defense.config.expiry_boundary}
    missing = [name for name, done in (("group", narrator.group_shown), ("policy", narrator.policy is not None),
                                       ("block", narrator.block_shown), ("other source", narrator.other_shown),
                                       ("expiry", narrator.expiry_shown)) if not done]

    narrator.banner(13, "All three submission outputs, written and checked from disk")
    paths = write_outputs(result, args.out_dir)
    pd.set_option("display.width", 160)
    for name in ("classification", "policy", "replay"):
        frame = pd.read_csv(paths[name], dtype=str, keep_default_na=False)
        print(f"\n    {paths[name]} ({len(frame):,} rows)")
        print(frame.head(4).to_string(index=False))
        if name == "replay" and (frame["action"] == "BLOCK").any():
            print(frame[frame["action"] == "BLOCK"].head(2).to_string(index=False, header=False))
    state = outputs["state"]
    print(f"\n    Final state: position {state['position']}, {state['suspicious_flows_observed']:,} suspicious flows observed, "
          f"{state['temporal_groups']:,} groups, {len(state['expired_policy_ids'])} expired policies, actions {state['actions']}")
    problems = check_prefix_invariance(scored, result) + validate_submission(args.out_dir, args.input, result["expiry_boundary"])
    print(f"    Rule check: {'passed' if not problems else problems[:10]}")
    if not load_config().get("level2", {}).get("replay_schema_confirmed", False):
        print("    Note: the replay.csv header is provisional until the organisers confirm it.")
    if missing:
        print(f"\nThis input did not produce: {', '.join(missing)}. Nothing was invented to fill the gap.")
    if problems or missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
