"""SYNTHETIC flows: Agent 4 against each pattern the Day 2 brief lists."""
import pandas as pd
import pytest

from backend.agents.temporal_agent import CausalityError, TemporalConfig, TemporalCorrelationAgent
from backend.preprocessing.data_processor import ReplayOrderError, check_replay_order


def agent(**config):
    return TemporalCorrelationAgent(TemporalConfig(**{"window_records": 50, "converging_min_sources": 3, **config}))


def flow(a, i, src="1.1.1.1", dst="9.9.9.9", port=80.0, cls="DOS", conf=0.99, anomaly=True, status="MULTI_SIGNAL_ATTACK",
         ts=None, at=None):
    return a.observe(i, f"R{i}", src, dst, port, cls, conf, anomaly, status, 0.99, ts, at, 55000.0)


def test_same_source_and_destination_are_grouped_with_the_required_output():
    a = agent()
    flow(a, 0, port=80.0, at="2017-07-07 10:00:00")
    group = flow(a, 1, port=443.0, at="2017-07-07 10:00:05")
    published = a.public_groups()[0]
    assert group["group_id"] == published["group_id"]
    assert published["member_record_ids"] == ["R0", "R1"]
    assert published["source_ips"] == ["1.1.1.1"] and published["destination_ips"] == ["9.9.9.9"]
    assert published["first_observed"] == "2017-07-07 10:00:00" and published["last_observed"] == "2017-07-07 10:00:05"
    assert published["evidence"]["pattern"] == "repeated suspicious flows between one source and one destination"
    assert "2 suspicious flows" in published["correlation_reason"]
    evidence = published["supporting_evidence"]
    assert [e["record_id"] for e in evidence] == ["R0", "R1"]
    assert evidence[0]["agent1_class"] == "DOS" and evidence[0]["agent3_status"] == "MULTI_SIGNAL_ATTACK"


def test_multiple_sources_on_a_common_destination_form_a_group_with_earlier_flows_as_members():
    a = agent()
    for i, src in enumerate(["1.1.1.1", "2.2.2.2", "3.3.3.3"]):
        flow(a, i, src=src)
    group = a.converging_group("9.9.9.9")
    assert group["scope_type"] == "DESTINATION_IP" and group["member_record_ids"] == ["R0", "R1", "R2"]
    assert a.public_group(group)["source_ips"] == ["1.1.1.1", "2.2.2.2", "3.3.3.3"]
    assert group["evidence"]["pattern"] == "many sources converging on one destination"


def test_repeated_activity_on_one_destination_port():
    a = agent()
    flow(a, 0, dst="9.9.9.1"), flow(a, 1, dst="9.9.9.2")
    assert a.source_evidence("1.1.1.1")["pattern"] == "repeated flows to one service port"


def test_many_destination_ports_from_one_source():
    a = agent(scan_min_ports=5)
    for i in range(5):
        flow(a, i, port=float(1000 + i), cls="PORT_SCAN")
    assert a.source_evidence("1.1.1.1")["pattern"] == "one source touching many destination ports"
    assert a.public_groups()[0]["destination_ports"] == [1000.0, 1001.0, 1002.0, 1003.0, 1004.0]


def test_benign_ports_do_not_make_a_source_look_like_a_scanner():
    a = agent(scan_min_ports=5)
    for i in range(10):
        flow(a, i, port=float(1000 + i), cls="BENIGN", anomaly=False, status="AGREED_BENIGN")
    flow(a, 10), flow(a, 11)
    assert a.source_evidence("1.1.1.1")["distinct_destination_ports"] == 1


def test_unrelated_flows_do_not_share_a_group():
    a = agent()
    flow(a, 0, src="1.1.1.1", dst="9.9.9.1"), flow(a, 1, src="2.2.2.2", dst="9.9.9.2")
    flow(a, 2, src="1.1.1.1", dst="9.9.9.1"), flow(a, 3, src="2.2.2.2", dst="9.9.9.2")
    assert [g["member_record_ids"] for g in a.public_groups()] == [["R0", "R2"], ["R1", "R3"]]


def test_expired_record_window_drops_old_flows_and_opens_a_new_group():
    a = agent(window_records=5)
    flow(a, 0), flow(a, 1)
    assert flow(a, 10) is None
    assert a.source_evidence("1.1.1.1")["flows_in_window"] == 1
    flow(a, 11)
    assert [g["member_record_ids"] for g in a.public_groups()] == [["R0", "R1"], ["R10", "R11"]]


def test_expired_time_window_applies_even_when_few_records_have_passed():
    a = agent(window_records=1000, window_seconds=60.0)
    flow(a, 0, ts=0.0), flow(a, 1, ts=30.0)
    assert a.source_evidence("1.1.1.1")["flows_in_window"] == 2
    flow(a, 2, ts=95.0)
    assert a.source_evidence("1.1.1.1")["flows_in_window"] == 1
    flow(a, 3, ts=96.0)
    assert [g["member_record_ids"] for g in a.public_groups()] == [["R0", "R1"], ["R2", "R3"]]


def test_with_timestamps_the_window_is_seconds_and_the_record_count_does_not_apply():
    a = agent(window_records=2, window_seconds=60.0)
    for i in range(5):
        flow(a, i, ts=float(i))
    evidence = a.source_evidence("1.1.1.1")
    assert evidence["flows_in_window"] == 5 and evidence["window_basis"] == "seconds"
    assert "60 seconds" in a.public_groups()[0]["correlation_reason"]


def test_without_timestamps_the_evidence_never_claims_a_time_window():
    a = agent(window_records=3, window_seconds=60.0)
    for i in range(5):
        flow(a, i)
    evidence = a.source_evidence("1.1.1.1")
    assert evidence["flows_in_window"] == 3 and evidence["window_basis"] == "records"
    reason = a.public_groups()[0]["correlation_reason"]
    assert "3 records" in reason and "seconds" not in reason


def test_a_flow_exactly_on_the_time_limit_is_still_inside():
    a = agent(window_records=1000, window_seconds=60.0)
    flow(a, 0, ts=0.0), flow(a, 1, ts=60.0)
    assert a.source_evidence("1.1.1.1")["flows_in_window"] == 2


def test_one_suspicious_flow_is_insufficient_evidence_for_a_group():
    a = agent()
    assert flow(a, 0) is None and not a.public_groups()


def test_benign_flows_never_form_a_group():
    a = agent()
    for i in range(10):
        assert flow(a, i, cls="BENIGN", anomaly=False, status="AGREED_BENIGN") is None
    assert not a.public_groups() and a.suspicious_observed == 0


def test_uncertain_agent3_status_counts_as_suspicious():
    a = agent()
    flow(a, 0, cls="BENIGN", conf=0.5, anomaly=False, status="UNCERTAIN")
    assert a.suspicious_observed == 1


def test_non_monotonic_timestamps_raise_by_default():
    a = agent()
    flow(a, 0, ts=100.0)
    with pytest.raises(CausalityError, match="earlier than the record before"):
        flow(a, 1, ts=50.0)


def test_non_monotonic_timestamps_can_be_reported_and_cannot_revive_old_flows():
    a = agent(window_records=1000, window_seconds=60.0, on_timestamp_regression="report")
    flow(a, 0, ts=0.0), flow(a, 1, ts=100.0)
    flow(a, 2, ts=10.0)
    assert a.timestamp_regressions == ["R2"]
    assert a.source_evidence("1.1.1.1")["flows_in_window"] == 2


def test_order_check_raises_on_backward_timestamps_and_can_report_instead():
    meta = pd.DataFrame({"flow_id": ["R1", "R2", "R3"],
                         "timestamp": ["2017-07-07 10:00:02", "2017-07-07 10:00:01", "2017-07-07 10:00:03"]})
    with pytest.raises(ReplayOrderError, match="first at R2"):
        check_replay_order(meta)
    report = check_replay_order(meta, "report")
    assert report["authority"] == "file order" and "first at R2" in report["ambiguities"][0]


def test_order_check_refuses_duplicates_and_accepts_a_clean_order():
    with pytest.raises(ReplayOrderError, match="repeat"):
        check_replay_order(pd.DataFrame({"flow_id": ["R1", "R1"]}), "report")
    with pytest.raises(ReplayOrderError, match="seq is unusable"):
        check_replay_order(pd.DataFrame({"flow_id": ["R1", "R2"], "seq": [1, 1]}), "report")
    clean = check_replay_order(pd.DataFrame({"flow_id": ["R1", "R2"], "seq": [1, 2], "timestamp": [5, 5]}))
    assert clean == {"authority": "seq", "records": 2, "timestamps_checked": True, "ambiguities": []}


def test_a_departed_source_no_longer_counts_towards_converging():
    a = agent(window_records=4)
    for i, src in enumerate(["1.1.1.1", "2.2.2.2", "3.3.3.3"]):
        flow(a, i, src=src)
    assert a.converging_group("9.9.9.9") is not None
    # Sources 1 and 2 fall out of the window; a stale zero count would still make this look like three.
    flow(a, 5, src="3.3.3.3")
    assert a.converging_group("9.9.9.9") is None


def test_one_flow_forms_a_group_only_when_both_agents_call_it_an_attack_at_high_confidence():
    a = agent(group_on_one_agreed_attack=True)
    assert flow(a, 0, src="benign", cls="BENIGN", anomaly=True, status="ANOMALY_ONLY") is None
    assert flow(a, 1, src="a1-only", anomaly=False, status="CLASSIFIER_ONLY") is None
    assert flow(a, 2, src="unsure", conf=0.5, status="UNCERTAIN") is None
    assert flow(a, 3, src="both")["member_record_ids"] == ["R3"]

