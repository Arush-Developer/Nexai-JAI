"""All five agents over real official eval flows with SYNTHETIC endpoints, which the pack does not have.

These tests prove behaviour and file rules. They say nothing about hidden-set performance.
"""
import time

import numpy as np
import pandas as pd
import pytest

from backend.config import load_config, resolve
from backend.preprocessing.data_processor import Partition, ReplayOrderError, load_flows, replay_order
from backend.replay.replay_engine import ReplayEngine
from backend.investigation.audit import AuditLog
from backend.investigation.tools import find_related_flows, inspect_source_activity
from backend.agents.defense_agent import DefenseConfig
from backend.services.level2 import (
    check_prefix_invariance, read_outputs, replay_scored, run_level2, score_records, validate_outputs,
    validate_submission, write_outputs)
from backend.services.model_service import Pipeline
from scripts.rehearse_level2 import synthetic_endpoints

pytestmark = pytest.mark.real_data
ROWS = 12_000


@pytest.fixture(scope="module")
def pipeline():
    return Pipeline.load()


@pytest.fixture(scope="module")
def flows():
    cfg = load_config()["data"]
    raw = pd.read_parquet(resolve(cfg["eval_parquet"])).iloc[:ROWS].reset_index(drop=True)
    endpoints = synthetic_endpoints(raw[cfg["label_column"]])
    return raw.drop(columns=[cfg["label_column"]]).assign(
        source_ip=endpoints["source_ip"], destination_ip=endpoints["destination_ip"], seq=np.arange(len(raw)))


def as_partition(frame, tmp_path, name="flows.parquet"):
    path = tmp_path / name
    frame.to_parquet(path, index=False)
    return load_flows(path)


@pytest.fixture(scope="module")
def run(pipeline, flows, tmp_path_factory):
    partition = as_partition(flows, tmp_path_factory.mktemp("level2"))
    return partition, run_level2(pipeline, partition)


def test_outputs_follow_the_briefs_rules_after_a_file_round_trip(run, tmp_path):
    partition, result = run
    paths = write_outputs(result, tmp_path)
    meta = partition.metadata.iloc[replay_order(partition.metadata)]
    read, problems = read_outputs(tmp_path)
    assert not problems
    assert not validate_outputs(read["classification"], read["policy"], read["replay"], meta["flow_id"], meta["source_ip"])
    # On disk, the files carry the organisers' template headers.
    assert (tmp_path / "classification.csv").read_text().splitlines()[0] == "record_id,predicted_class,anomaly_score,evidence_status"
    assert (tmp_path / "replay.csv").read_text().splitlines()[0] == "record_id,action,active_policy_id"
    assert (tmp_path / "policies.csv").read_text().splitlines()[0] == "policy_id,activated_after_record_id,scope_type,scope_value,expires_after_record_id"
    assert len(read["policy"]) and (read["replay"]["action"] == "BLOCK").any()


def test_future_records_cannot_change_earlier_decisions(pipeline, flows, run, tmp_path):
    """The causality proof: cut the stream, or scramble everything after the cut, and compare the prefix."""
    _, full = run
    cut = 7000
    truncated = run_level2(pipeline, as_partition(flows.iloc[:cut], tmp_path, "cut.parquet"))
    tail = flows.iloc[cut:].sample(frac=1, random_state=3)
    tail = tail.assign(source_ip="203.0.113.250", seq=np.arange(cut, len(flows)))
    scrambled = run_level2(pipeline, as_partition(pd.concat([flows.iloc[:cut], tail]), tmp_path, "tail.parquet"))
    for other in (truncated, scrambled):
        for column in ("action", "applied_policy_id"):
            assert full["replay"][column].iloc[:cut].tolist() == other["replay"][column].iloc[:cut].tolist()
    assert full["classification"].iloc[:cut].equals(truncated["classification"].iloc[:cut])


def test_labels_in_the_input_do_not_change_anything(pipeline, flows, run, tmp_path):
    labelled = flows.assign(label="BENIGN")
    other = run_level2(pipeline, as_partition(labelled, tmp_path, "labelled.parquet"))
    assert other["replay"].equals(run[1]["replay"]) and other["classification"].equals(run[1]["classification"])


def test_file_order_conflicting_with_seq_is_refused_then_followed_only_when_reported(pipeline, flows, run, tmp_path):
    shuffled = as_partition(flows.sample(frac=1, random_state=11), tmp_path, "shuffled.parquet")
    with pytest.raises(ReplayOrderError, match="differs from the seq column"):
        run_level2(pipeline, shuffled)
    other = run_level2(pipeline, shuffled, on_order_conflict="report")
    assert other["order_report"]["ambiguities"]
    assert other["replay"]["record_id"].tolist() == run[1]["replay"]["record_id"].tolist()
    assert other["replay"]["action"].tolist() == run[1]["replay"]["action"].tolist()


@pytest.mark.parametrize("break_it, expected", [
    (lambda c, p, r: r.assign(applied_policy_id=""), "BLOCK references no known policy"),
    (lambda c, p, r: r.assign(action="ALLOW"), "must have an empty applied_policy_id"),
    (lambda c, p, r: r.iloc[1:], "does not cover every record"),
    (lambda c, p, r: pd.concat([r, r.iloc[:1]]), "repeats record ids"),
])
def test_checker_catches_broken_replay_files(run, break_it, expected):
    partition, result = run
    meta = partition.metadata.iloc[replay_order(partition.metadata)]
    broken = break_it(result["classification"], result["policy"], result["replay"].copy())
    problems = validate_outputs(result["classification"], result["policy"], broken, meta["flow_id"], meta["source_ip"])
    assert any(expected in p for p in problems)


def test_checker_catches_a_block_before_activation(run):
    partition, result = run
    meta = partition.metadata.iloc[replay_order(partition.metadata)]
    replay = result["replay"].copy()
    policy = result["policy"].iloc[0]
    target = replay.index[replay["record_id"] == policy["activated_after_record_id"]][0]
    replay.loc[target, ["action", "applied_policy_id"]] = ["BLOCK", policy["policy_id"]]
    problems = validate_outputs(result["classification"], result["policy"], replay, meta["flow_id"], meta["source_ip"])
    assert any("outside its active range" in p for p in problems)


def test_missing_endpoints_are_refused_rather_than_guessed(pipeline, flows, tmp_path):
    partition = as_partition(flows.drop(columns=["source_ip"]), tmp_path, "nosource.parquet")
    with pytest.raises(ValueError, match="source_ip"):
        run_level2(pipeline, partition)


def test_live_replay_makes_the_same_decisions_as_the_export(pipeline, run):
    partition, result = run
    analysis = pipeline.analyse(partition)
    engine = ReplayEngine(pipeline, analysis, AuditLog(), persist=False)
    engine.start(speed=100_000, batch_size=700, max_investigations_per_batch=0)
    engine._thread.join(timeout=60)
    frame = engine.replayed_frame()
    assert engine.status == "completed" and len(frame) == ROWS
    assert frame["flow_id"].tolist() == result["replay"]["record_id"].tolist()
    assert frame["defense_action"].tolist() == result["replay"]["action"].tolist()
    assert frame["applied_policy_id"].tolist() == result["replay"]["applied_policy_id"].tolist()
    policies = engine.defense.defense.public_policies()
    assert [p["policy_id"] for p in policies] == result["policy"]["policy_id"].tolist()
    assert any(e["type"] == "policy" for e in engine.events_since(0, limit=20_000))


def test_resuming_after_a_mid_batch_stop_gives_the_same_result_as_an_uninterrupted_run(pipeline, run):
    partition, result = run
    analysis = pipeline.analyse(partition)
    engine = ReplayEngine(pipeline, analysis, AuditLog(), persist=False)
    engine.start(speed=3000, batch_size=2000, max_investigations_per_batch=0)
    while engine.cursor < 500:
        time.sleep(0.01)
    engine.stop()
    assert engine.cursor < ROWS
    engine.start(speed=100_000, batch_size=2000, max_investigations_per_batch=0, resume_saved=True)
    engine._thread.join(timeout=60)
    frame = engine.replayed_frame()
    assert engine.status == "completed" and len(frame) == ROWS and engine.counts["total"] == ROWS
    assert frame["flow_id"].tolist() == result["replay"]["record_id"].tolist()
    assert frame["defense_action"].tolist() == result["replay"]["action"].tolist()


def test_replay_tools_cannot_see_later_records(pipeline, run):
    partition, _ = run
    analysis = pipeline.analyse(partition)
    engine = ReplayEngine(pipeline, analysis, AuditLog(), persist=False)
    store = engine.store
    anchor = 5000
    row = int(store.sorted_rows[anchor])
    near = store.neighbours(row, 100)
    assert store.rank_of[near].max() == anchor and store.rank_of[near].min() == anchor - 100
    related = find_related_flows(store, str(store.flow_ids[row]), 100)
    positions = [store.rank_of[store.row(f)] for f in related["evidence"]["related_flow_ids"]]
    assert not positions or max(positions) < anchor
    source = inspect_source_activity(store, flow_id=str(store.flow_ids[row]), time_window=500)
    assert all(store.rank_of[store.row(f)] <= anchor for f in source["evidence"]["flow_ids"])
    # The whole-set store used for offline analysis does look both ways; the replay one must not.
    assert analysis.store.rank_of[analysis.store.neighbours(row, 100)].max() > anchor


def test_synthetic_addresses_are_kept_whole():
    labels = pd.Series(["BENIGN"] * 50 + ["DOS", "PORT_SCAN", "BRUTE_FORCE", "DDOS"] * 10)
    endpoints = synthetic_endpoints(labels)
    assert set(endpoints["source_ip"][labels == "DOS"]) == {"198.51.100.20"}
    assert set(endpoints["source_ip"][labels == "PORT_SCAN"]) == {"198.51.100.30"}
    assert set(endpoints["destination_ip"][labels == "DDOS"]) == {"192.0.2.103"}


def test_partition_helper_keeps_endpoints_out_of_model_features(run):
    partition, _ = run
    assert isinstance(partition, Partition)
    assert not {"source_ip", "destination_ip", "seq"} & set(partition.features.columns)


def test_every_blocked_record_keeps_its_independent_prediction(run):
    _, result = run
    blocked = result["replay"].loc[result["replay"]["action"] == "BLOCK", "record_id"]
    assert len(blocked)
    rows = result["classification"].set_index("record_id").loc[blocked]
    assert rows["predicted_class"].notna().all() and rows["anomaly_score"].notna().all()
    assert rows["evidence_status"].notna().all()
    assert len(result["classification"]) == ROWS and result["classification"]["record_id"].is_unique


def test_scoring_does_not_depend_on_other_records_in_the_batch(pipeline, flows, tmp_path, run):
    """One record alone must score exactly as it does among thousands, so no batch-wide statistic is used."""
    _, full = run
    single = score_records(pipeline, as_partition(flows.iloc[[42]], tmp_path, "one.parquet")).classification.iloc[0]
    assert single.equals(full["classification"].iloc[42])


def test_replay_changes_nothing_in_classification(run):
    _, result = run
    before = result["scored"].classification.copy()
    replay_scored(result["scored"])
    assert result["scored"].classification.equals(before)


def test_prefix_invariance_passes_on_a_real_run_and_detects_tampering(run):
    _, result = run
    assert not check_prefix_invariance(result["scored"], result)
    tampered = {**result, "replay": result["replay"].assign(action="ALLOW", applied_policy_id="")}
    assert any("causality" in p for p in check_prefix_invariance(result["scored"], tampered))


def test_files_on_disk_pass_the_submission_check_and_tampered_files_do_not(run, flows, tmp_path):
    _, result = run
    input_path = tmp_path / "input.parquet"
    flows.to_parquet(input_path, index=False)
    write_outputs(result, tmp_path)
    assert not validate_submission(tmp_path, input_path)
    frames, problems = read_outputs(tmp_path)
    assert not problems and set(frames) == {"classification", "policy", "replay"}
    replay_path = tmp_path / "replay.csv"
    lines = replay_path.read_text().splitlines()
    replay_path.write_text("\n".join(lines[:-1]) + "\n")
    assert any("does not cover every record" in p for p in validate_submission(tmp_path, input_path))
    replay_path.write_text("wrong,header,here\n" + "\n".join(lines[1:]) + "\n")
    assert any("header" in p for p in validate_submission(tmp_path, input_path))


def test_exclusive_expiry_also_produces_valid_files_on_real_data(pipeline, run):
    partition, _ = run
    other = run_level2(pipeline, partition, defense=DefenseConfig(expiry_boundary="exclusive"))
    meta = partition.metadata.iloc[replay_order(partition.metadata)]
    assert not validate_outputs(other["classification"], other["policy"], other["replay"], meta["flow_id"],
                                meta["source_ip"], "exclusive")
    assert (other["replay"]["action"] == "BLOCK").any()


def test_group_members_come_from_real_records_and_never_from_blocked_ones(run):
    _, result = run
    actions = dict(zip(result["replay"]["record_id"], result["replay"]["action"]))
    position = {r: i for i, r in enumerate(result["replay"]["record_id"])}
    for group in result["groups"]:
        members = group["member_record_ids"]
        assert all(actions[m] == "ALLOW" for m in members)
        assert [position[m] for m in members] == sorted(position[m] for m in members)
        first = group["supporting_evidence"][0]
        assert len(members) >= 2 or (first["agent2_flagged"] and first["agent1_class"] != "BENIGN" and first["agent1_confidence"] >= 0.9)
    log = {e["policy_id"]: e for e in result["log"] if e["event"] == "ACTIVATED"}
    for p in result["policies"]:
        assert position[p["activated_after_record_id"]] == log[p["policy_id"]]["position"]
        assert position[p["expires_after_record_id"]] > position[p["activated_after_record_id"]]


def test_timestamped_input_flows_through_and_a_conflict_is_refused(pipeline, flows, tmp_path):
    stamps = pd.date_range("2017-07-07 10:00:00", periods=500, freq="s").strftime("%Y-%m-%d %H:%M:%S")
    clean = flows.iloc[:500].assign(timestamp=stamps)
    result = run_level2(pipeline, as_partition(clean, tmp_path, "time.parquet"))
    assert result["order_report"]["timestamps_checked"] and not result["order_report"]["ambiguities"]
    assert "timestamp" not in result["scored"].features.columns
    broken = clean.assign(timestamp=stamps[::-1])
    with pytest.raises(ReplayOrderError, match="earlier than the record before"):
        run_level2(pipeline, as_partition(broken, tmp_path, "back.parquet"))


def test_stop_during_a_real_replay_prevents_later_activations(pipeline, run):
    _, result = run
    first = result["policy"]["activated_after_record_id"].iloc[0]

    def stop_at_first_policy(i, step, engine):
        if step["activated_policy"]:
            engine.stop("test")

    engine = replay_scored(result["scored"], on_step=stop_at_first_policy)
    assert [p["activated_after_record_id"] for p in engine.defense.policies] == [first]
