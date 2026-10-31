"""The Day 2 brief's twelve required tests. SYNTHETIC fixtures: records and addresses here are invented."""
import pandas as pd
import pytest

from backend.agents.defense_agent import ALLOW, BLOCK, DefenseConfig
from backend.agents.temporal_agent import CausalityError, TemporalConfig, _Observation
from backend.replay.causal_replay import CausalReplayEngine
from backend.services.level2 import CLASSIFICATION_COLUMNS, POLICY_COLUMNS, REPLAY_COLUMNS, validate_outputs

ATTACKER, OTHER = "10.0.0.11", "10.0.0.99"


def engine(boundary="inclusive", ttl=2, total=None, **defense):
    settings = dict(min_attack_flows_multi_signal=2, ttl_records=ttl, expiry_boundary=boundary)
    return CausalReplayEngine(TemporalConfig(window_records=50), DefenseConfig(**{**settings, **defense}), total)


def suspicious(run, i, src=ATTACKER, dst="10.0.0.1", cls="DOS", conf=0.99, status="MULTI_SIGNAL_ATTACK", port=80.0):
    return run.step(i, f"R{i + 1:03d}", src, dst, port, cls, conf, 0.99, status == "MULTI_SIGNAL_ATTACK", status)


def scenario(run):
    """R001-R002 attacker, R003 other source, R004-R005 attacker again."""
    steps = [suspicious(run, 0), suspicious(run, 1)]
    steps.append(suspicious(run, 2, src=OTHER, cls="BENIGN", conf=0.99, status="AGREED_BENIGN"))
    steps += [suspicious(run, 3), suspicious(run, 4)]
    return steps


def files(run=None, boundary="inclusive"):
    """Valid classification, policy and replay frames from a real run of the scenario, plus ids and sources."""
    run = run or engine(boundary, total=5)
    scenario(run)
    run.finish(4, "R005")
    ids = [a["record_id"] for a in run.action_log]
    classification = pd.DataFrame({"record_id": ids, "predicted_class": "DOS", "anomaly_score": 0.9,
                                   "evidence_status": "MULTI_SIGNAL_ATTACK"}, columns=CLASSIFICATION_COLUMNS)
    policy = pd.DataFrame([{c: p[c] for c in POLICY_COLUMNS} for p in run.defense.public_policies()],
                          columns=POLICY_COLUMNS)
    replay = pd.DataFrame(run.action_log)[REPLAY_COLUMNS]
    return classification, policy, replay, ids, [a["source_ip"] for a in run.action_log]


def test_01_first_suspicious_record_with_no_active_policy_is_allowed():
    step = suspicious(engine(), 0)
    assert step["action"] == ALLOW and step["applied_policy_id"] == "" and step["activated_policy"] is None


def test_02_repeated_behaviour_updates_a_group_and_activates_after_that_record():
    run = engine()
    assert suspicious(run, 0)["group"] is None
    step = suspicious(run, 1)
    assert step["action"] == ALLOW
    assert step["group"]["member_record_ids"] == ["R001", "R002"]
    assert step["activated_policy"]["activated_after_record_id"] == "R002"


def test_03_a_different_source_is_allowed():
    steps = scenario(engine())
    assert steps[2]["action"] == ALLOW and steps[2]["applied_policy_id"] == ""


def test_04_matching_source_during_an_active_policy_is_blocked_with_its_policy_id():
    steps = scenario(engine())
    assert steps[3]["action"] == BLOCK
    assert steps[3]["applied_policy_id"] == steps[1]["activated_policy"]["policy_id"]


def test_05_record_after_expiry_is_no_longer_blocked_by_that_policy():
    run = engine()
    steps = scenario(run)
    assert steps[4]["action"] == ALLOW and steps[4]["applied_policy_id"] == ""
    assert run.defense.policies[0]["status"] == "EXPIRED"


def test_06_a_policy_never_affects_its_own_triggering_record():
    run = engine()
    step = suspicious(run, 0), suspicious(run, 1)
    assert step[1]["activated_policy"]["policy_id"] == "POL-00001"
    assert run.action_log[1]["action"] == ALLOW and run.action_log[1]["applied_policy_id"] == ""
    assert run.defense.policies[0]["blocked_records"] == 0


def test_07_block_referencing_a_nonexistent_policy_fails_validation():
    classification, policy, replay, ids, sources = files()
    replay.loc[replay["action"] == BLOCK, "applied_policy_id"] = "POL-99999"
    assert any("references no known policy" in p for p in validate_outputs(classification, policy, replay, ids, sources))


def test_08_allow_with_an_applied_policy_id_fails_validation():
    classification, policy, replay, ids, sources = files()
    replay.loc[0, "applied_policy_id"] = "POL-00001"
    assert any("must have an empty applied_policy_id" in p
               for p in validate_outputs(classification, policy, replay, ids, sources))


def test_09_a_future_record_in_correlation_history_fails_the_causality_check():
    run = engine()
    suspicious(run, 0)
    run.temporal.assert_causal(0)
    future = _Observation(99, "R100", ATTACKER, "10.0.0.1", 80.0, "DOS", True, True)
    run.temporal._windows[("SOURCE_IP", ATTACKER)].add(future)
    with pytest.raises(CausalityError, match="R100"):
        run.temporal.assert_causal(0)
    # The same check guards every activation, so the planted record stops the replay outright.
    with pytest.raises(CausalityError, match="R100"):
        suspicious(run, 1)


def test_09b_records_arriving_out_of_order_are_refused():
    run = engine()
    suspicious(run, 5)
    with pytest.raises(CausalityError, match="arrived after"):
        suspicious(run, 4)


def test_10_a_blocked_row_missing_from_classification_fails_validation():
    classification, policy, replay, ids, sources = files()
    blocked = replay.loc[replay["action"] == BLOCK, "record_id"].iloc[0]
    trimmed = classification[classification["record_id"] != blocked]
    assert any("classification.csv does not cover every record" in p
               for p in validate_outputs(trimmed, policy, replay, ids, sources))


def test_11_an_unsupported_policy_scope_fails_validation():
    classification, policy, replay, ids, sources = files()
    policy["scope_type"] = "DESTINATION_IP"
    assert any("must use scope_type SOURCE_IP" in p for p in validate_outputs(classification, policy, replay, ids, sources))


@pytest.mark.parametrize("boundary, action_at_boundary", [("inclusive", BLOCK), ("exclusive", ALLOW)])
def test_12_expiry_at_the_boundary_record_matches_the_configured_convention(boundary, action_at_boundary):
    run = engine(boundary)
    steps = scenario(run)
    policy = run.defense.policies[0]
    assert policy["expires_after_record_id"] == "R004"
    assert steps[3]["action"] == action_at_boundary
    # Under "exclusive" R004 is analysed, so the still-attacking source can earn a new policy. Never the old one.
    assert steps[4]["applied_policy_id"] != policy["policy_id"] and policy["status"] == "EXPIRED"


@pytest.mark.parametrize("boundary", ["inclusive", "exclusive"])
def test_12b_validator_accepts_its_own_convention_and_rejects_the_other(boundary):
    classification, policy, replay, ids, sources = files(boundary=boundary)
    assert not validate_outputs(classification, policy, replay, ids, sources, boundary)
    other = "exclusive" if boundary == "inclusive" else "inclusive"
    assert validate_outputs(classification, policy, replay, ids, sources, other)


def test_unknown_expiry_convention_is_refused():
    with pytest.raises(ValueError, match="expiry_boundary"):
        DefenseConfig(expiry_boundary="sometimes")


def test_a_blocked_record_adds_no_evidence_and_cannot_trigger_a_policy():
    run = engine(ttl=5)
    suspicious(run, 0), suspicious(run, 1)
    group = run.temporal.groups[0]
    observed = run.temporal.suspicious_observed
    step = suspicious(run, 2)
    assert step["action"] == BLOCK and step["group"] is None and step["activated_policy"] is None
    assert group["member_record_ids"] == ["R001", "R002"] and run.temporal.suspicious_observed == observed
    assert run.temporal.source_evidence(ATTACKER)["flows_in_window"] == 2


def test_stop_prevents_new_policies_and_active_ones_still_run_to_expiry():
    run = engine(ttl=3)
    suspicious(run, 0), suspicious(run, 1)
    run.stop("operator")
    assert suspicious(run, 2)["action"] == BLOCK
    suspicious(run, 3, src=OTHER), suspicious(run, 4, src=OTHER)
    assert suspicious(run, 5, src=OTHER)["activated_policy"] is None
    assert len(run.defense.policies) == 1
    assert run.state()["stopped"] and any(e["event"] == "STOPPED" for e in run.defense.log)


def test_replay_is_deterministic():
    def go():
        run = engine(total=5)
        scenario(run)
        run.finish(4, "R005")
        return run.action_log, run.defense.public_policies(), run.temporal.public_groups(), run.defense.log
    assert go() == go()


def test_no_policy_is_activated_when_no_later_record_exists():
    run = engine(total=2)
    suspicious(run, 0)
    assert suspicious(run, 1)["activated_policy"] is None
    run.finish(1, "R002")
    assert not run.defense.policies and run.defense.log[-1]["event"] == "SKIPPED_NO_LATER_RECORD"


def test_expiry_is_clamped_to_the_last_record_of_the_replay():
    run = engine(ttl=1000, total=5)
    scenario(run)
    run.finish(4, "R005")
    assert run.defense.policies[0]["expires_after_record_id"] == "R005"


def test_state_machine_reports_everything_the_brief_asks_it_to_maintain():
    run = engine(total=5)
    scenario(run)
    state = run.state()
    assert state["position"] == 4 and state["record_id"] == "R005"
    assert state["suspicious_flows_observed"] == 3 and state["temporal_groups"] == 1
    assert state["expired_policy_ids"] == ["POL-00001"] and state["active_policy_ids"] == []
    assert state["applied_policy_ids"] == ["POL-00001"] and state["actions"] == {ALLOW: 4, BLOCK: 1}


def test_a_policy_overlap_or_blocked_trigger_is_caught():
    classification, policy, replay, ids, sources = files()
    replay.loc[1, ["action", "applied_policy_id"]] = [BLOCK, "POL-00001"]
    problems = validate_outputs(classification, policy, replay, ids, sources)
    assert any("triggered by a record that was itself blocked" in p for p in problems)
    assert any("outside its active range" in p for p in problems)


def test_wrong_columns_and_bad_scores_are_caught():
    classification, policy, replay, ids, sources = files()
    assert "columns" in validate_outputs(classification.drop(columns="anomaly_score"), policy, replay, ids, sources)[0]
    assert any("anomaly_score" in p for p in validate_outputs(classification.assign(anomaly_score="n/a"), policy, replay, ids, sources))
    assert any("evidence_status" in p for p in validate_outputs(classification.assign(evidence_status="MADE_UP"), policy, replay, ids, sources))


@pytest.mark.parametrize("boundary, ttl, last_blocked", [("inclusive", 3, "R005"), ("exclusive", 4, "R006")])
def test_the_final_brief_sequence_r001_to_r005_then_expiry(boundary, ttl, last_blocked):
    """R001 ALLOW; R002 ALLOW and activates after itself; R003 other source; R004, R005 BLOCK; then expiry."""
    run = engine(boundary, ttl=ttl)
    steps = scenario(run)
    assert [s["action"] for s in steps] == [ALLOW, ALLOW, ALLOW, BLOCK, BLOCK]
    policy = steps[1]["activated_policy"]
    assert policy["activated_after_record_id"] == "R002" and [s["applied_policy_id"] for s in steps[3:]] == [policy["policy_id"]] * 2
    assert all(s["applied_policy_id"] == "" for s in steps[:3])
    if boundary == "exclusive":
        # The record named as the expiry is not blocked under this convention, but it does end the policy.
        after = suspicious(run, 5)
        assert after["action"] == ALLOW and policy["expires_after_record_id"] == last_blocked
    else:
        assert policy["expires_after_record_id"] == last_blocked
        assert suspicious(run, 5)["applied_policy_id"] != policy["policy_id"]
    assert policy["status"] == "EXPIRED" and policy["blocked_records"] == 2
