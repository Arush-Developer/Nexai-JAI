"""The Data Manager API. Endpoints in these tests are SYNTHETIC; nothing here measures hidden-set performance."""
import io
import json
import stat
import time
import zipfile

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.config import load_config, resolve
from backend.main import app
from backend.preprocessing.data_processor import read_flow_frame
from backend.services.local_data import LocalDataError, LocalDataService, combine_job, extract_archive, member_problem
from scripts.make_local_fixture import build

LIMITS = load_config()["local_data"]
real_data = pytest.mark.real_data


@pytest.fixture
def client(tmp_path):
    (tmp_path / "data").mkdir()
    app.state.local_data = LocalDataService(base=tmp_path)
    # No "with": entering the client would run startup, which loads the dashboard and opens the real audit database.
    yield TestClient(app)
    app.state.local_data = None


def as_csv(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode()


def upload(client, dataset_type, *files):
    parts = [("files", (name, io.BytesIO(body), "application/octet-stream")) for name, body in files]
    return client.post("/api/local-data/upload", data={"dataset_type": dataset_type}, files=parts)


def stage(client, dataset_type, frame, name="input.csv"):
    response = upload(client, dataset_type, (name, as_csv(frame)))
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


def inspect(client, run_id, include=None):
    response = client.post("/api/local-data/inspect", json={"run_id": run_id, "include": include})
    assert response.status_code == 200, response.text
    return response.json()


def run_to_end(client, run_id, **options):
    response = client.post("/api/local-data/run", json={"run_id": run_id, **options})
    assert response.status_code == 200, response.text
    for _ in range(1200):
        view = client.get(f"/api/local-data/runs/{run_id}").json()
        if view["state"] in ("completed", "failed"):
            return view
        time.sleep(0.05)
    raise AssertionError("the run did not finish")


@pytest.fixture(scope="module")
def hidden_style():
    return build(6000)


@pytest.fixture(scope="module")
def labelled():
    data = load_config()["data"]
    return pd.read_parquet(resolve(data["eval_parquet"])).head(3000)


# ---- Archives, with no model needed --------------------------------------------------------------------
def make_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in members.items():
            z.writestr(name, body)
    return buffer.getvalue()


@pytest.mark.parametrize("name", ["../evil.csv", "a/../../evil.csv", "/etc/evil.csv", "C:/evil.csv", "..\\evil.csv"])
def test_zip_path_traversal_is_refused_and_nothing_is_extracted(tmp_path, name):
    archive = tmp_path / "a.zip"
    archive.write_bytes(make_zip({"good.csv": b"a\n1\n", name: b"a\n1\n"}))
    target = tmp_path / "out"
    with pytest.raises(LocalDataError, match="unsafe path"):
        extract_archive(archive, target, LIMITS)
    assert not target.exists()


def test_zip_symlink_is_refused():
    info = zipfile.ZipInfo("link.csv")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    assert "symbolic link" in member_problem(info)


def test_zip_that_expands_too_far_is_refused(tmp_path):
    archive = tmp_path / "bomb.zip"
    archive.write_bytes(make_zip({"bomb.csv": b"0" * 5_000_000}))
    with pytest.raises(LocalDataError, match="expands more than"):
        extract_archive(archive, tmp_path / "out", LIMITS)


def test_the_extraction_limit_applies_to_bytes_actually_written(tmp_path):
    archive = tmp_path / "big.zip"
    archive.write_bytes(make_zip({"big.csv": bytes(range(256)) * 4000}))
    with pytest.raises(LocalDataError, match="would extract to"):
        extract_archive(archive, tmp_path / "out", {**LIMITS, "max_extracted_bytes": 1000})


def test_archived_programs_are_listed_but_never_unpacked(tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(make_zip({"data/flows.csv": b"a\n1\n", "run.sh": b"rm -rf /", "x.exe": b"MZ",
                                  "__MACOSX/flows.csv": b"junk", "inner.zip": b"PK"}))
    target = tmp_path / "out"
    result = extract_archive(archive, target, LIMITS)
    assert [e["member"] for e in result["extracted"]] == ["data/flows.csv"]
    assert set(result["skipped"]) == {"run.sh", "x.exe", "__MACOSX/flows.csv", "inner.zip"}
    assert [p.name for p in target.rglob("*") if p.is_file()] == ["flows.csv"]


def test_a_damaged_zip_is_reported_and_leaves_no_run_behind(client, tmp_path):
    response = upload(client, "hidden", ("broken.zip", b"this is not a zip"))
    assert response.status_code == 422
    assert "not a readable ZIP" in response.json()["detail"]
    assert not list((tmp_path / LIMITS["staging_dir"]).glob("RUN_*"))


# ---- Upload, with no model needed ------------------------------------------------------------------------
def test_upload_keeps_the_original_bytes_with_their_hash_in_a_unique_run(client, tmp_path):
    body = b"record_id,a\n007,1\n"
    first, second = upload(client, "hidden", ("x.csv", body)).json(), upload(client, "hidden", ("x.csv", body)).json()
    assert first["run_id"] != second["run_id"]
    assert first["files"][0]["sha256"] == __import__("hashlib").sha256(body).hexdigest()
    stored = tmp_path / first["data_files"][0]["path"]
    assert stored.read_bytes() == body and not stored.stat().st_mode & stat.S_IWUSR
    assert second["duplicate_of"] == [first["run_id"]]


def test_a_client_filename_cannot_escape_the_staging_directory(client, tmp_path):
    made = upload(client, "hidden", ("../../../evil.csv", b"a\n1\n")).json()
    stored = (tmp_path / made["data_files"][0]["path"]).resolve()
    assert stored.is_relative_to((tmp_path / LIMITS["staging_dir"]).resolve()) and stored.name == "F001_evil.csv"
    assert not (tmp_path / "evil.csv").exists()


def test_other_file_types_and_unknown_dataset_types_are_refused(client):
    assert upload(client, "hidden", ("run.sh", b"x")).status_code == 422
    assert upload(client, "secret", ("a.csv", b"a\n1\n")).status_code == 422


def test_requests_from_another_website_are_refused(client):
    assert client.get("/api/local-data/status", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/local-data/status", headers={"Origin": "http://localhost:5173"}).status_code == 200


@pytest.mark.parametrize("path", ["/runs/RUN_x/files/replay.csv", "/runs/RUN_2026_999", "/runs/..%2f..%2fetc/archive"])
def test_unknown_or_malformed_run_ids_are_not_found(client, path):
    assert client.get(f"/api/local-data{path}").status_code == 404


def test_only_named_output_files_can_be_downloaded(client):
    run_id = upload(client, "hidden", ("a.csv", b"a\n1\n")).json()["run_id"]
    assert client.get(f"/api/local-data/runs/{run_id}/files/manifest.json").status_code == 404
    assert client.get(f"/api/local-data/runs/{run_id}/files/classification.csv").status_code == 404


def test_record_ids_keep_their_official_text(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("Record ID,Source IP,Fwd Packets\n007,10.0.0.1,3\n010,10.0.0.2,4\n")
    assert read_flow_frame(path)["Record ID"].tolist() == ["007", "010"]


def test_missing_models_are_explained_and_stop_inspection_without_crashing(tmp_path):
    def missing():
        raise FileNotFoundError(2, "No such file", "models/threat_model/threat_agent.joblib")

    app.state.local_data = LocalDataService(pipeline_loader=missing, base=tmp_path)
    try:
        client = TestClient(app)
        status = client.get("/api/local-data/status").json()
        assert not status["ready"] and "threat_agent.joblib" in status["error"] and "not in git" in status["error"]
        run_id = upload(client, "hidden", ("a.csv", b"record_id,a\n1,2\n")).json()["run_id"]
        job = client.post("/api/local-data/inspect", json={"run_id": run_id}).json()["job"]
        assert not job["runnable"] and "threat_agent.joblib" in job["errors"][0]
        assert client.post("/api/local-data/run", json={"run_id": run_id}).status_code == 422
    finally:
        app.state.local_data = None


# ---- Joining files ---------------------------------------------------------------------------------------
def test_incompatible_flow_files_are_never_joined():
    problems = []
    a, b = pd.DataFrame({"record_id": ["1"], "x": [1]}), pd.DataFrame({"record_id": ["2"], "y": [1]})
    assert combine_job([a, b], [], problems) is None and "different columns" in problems[0]


def test_flow_files_with_no_stated_order_are_never_joined():
    problems = []
    a, b = pd.DataFrame({"record_id": ["1"], "x": [1]}), pd.DataFrame({"record_id": ["2"], "x": [1]})
    assert combine_job([a, b], [], problems) is None and "order across files" in problems[0]


def test_an_ordering_file_sets_the_sequence_and_must_cover_exactly_the_records():
    flows = pd.DataFrame({"record_id": ["a", "b", "c"], "x": [1, 2, 3]})
    problems = []
    order = pd.DataFrame({"record_id": ["c", "a", "b"], "seq": [1, 2, 3]})
    assert combine_job([flows], [order], problems)["record_id"].tolist() == ["c", "a", "b"] and not problems
    short = pd.DataFrame({"record_id": ["c", "a"], "seq": [1, 2]})
    assert combine_job([flows], [short], problems) is None and "exactly the records" in problems[0]


# ---- Inspection, on the real saved models ------------------------------------------------------------------
@real_data
def test_inspection_reports_what_the_hidden_style_file_really_contains(client, hidden_style):
    report = inspect(client, stage(client, "hidden", hidden_style))
    job, file = report["job"], report["files"][0]
    assert job["runnable"] and job["mode"] == "full" and job["models_compatible"]
    assert job["records"] == file["records"] == 6000
    assert job["source_ip"] and job["destination_ip"] and job["source_port"] and job["protocol"] and job["timestamps"]
    assert job["temporal"]["basis"] == "seconds"
    assert file["label_column"] is None and file["record_id_column"] == "record_id"
    assert file["missing_required_features"] == [] and file["unrecognised_columns"] == []
    assert file["positive_infinity"] == 0 and file["timestamp_order"]["backward_steps"] == 0


@real_data
def test_the_type_is_checked_against_the_schema_not_the_filename(client, labelled, hidden_style):
    wrong = inspect(client, stage(client, "hidden", labelled, name="hidden_final.csv"))
    assert not wrong["job"]["runnable"] and "label column" in " ".join(wrong["job"]["errors"])
    wrong = inspect(client, stage(client, "validation", hidden_style, name="labelled_validation.csv"))
    assert not wrong["job"]["runnable"] and "no label column" in " ".join(wrong["job"]["errors"])


@real_data
def test_missing_features_block_the_run_and_are_never_invented(client, hidden_style):
    run_id = stage(client, "hidden", hidden_style.drop(columns=["Flow Duration", "Fwd Packet Length Mean"]))
    report = inspect(client, run_id)
    assert not report["job"]["runnable"]
    assert set(report["job"]["missing_columns"]) == {"Flow Duration", "Fwd Packet Length Mean"}
    assert client.post("/api/local-data/run", json={"run_id": run_id}).status_code == 422


@real_data
def test_a_missing_record_id_column_and_text_in_a_feature_are_reported(client, hidden_style):
    assert "record_id" in " ".join(inspect(client, stage(client, "hidden", hidden_style.drop(columns=["record_id"])))["job"]["errors"])
    bad = hidden_style.astype({"Flow Duration": object})
    bad.loc[0, "Flow Duration"] = "oops"
    report = inspect(client, stage(client, "hidden", bad))
    assert report["job"]["invalid_values"] == {"Flow Duration": 1} and not report["job"]["runnable"]


@real_data
def test_repeated_record_ids_block_the_run(client, hidden_style):
    bad = hidden_style.copy()
    bad.loc[1, "record_id"] = bad.loc[0, "record_id"]
    assert not inspect(client, stage(client, "hidden", bad))["job"]["runnable"]


@real_data
def test_inspection_of_the_training_partition_matches_the_measured_profile(client):
    data = load_config()["data"]
    parts = [("files", ("flows.parquet", resolve(data["training_parquet"]).read_bytes(), "application/octet-stream"))]
    run_id = client.post("/api/local-data/upload", data={"dataset_type": "training"}, files=parts).json()["run_id"]
    report = inspect(client, run_id)
    file = report["files"][0]
    assert (file["records"], file["duplicate_records"], file["label_column"]) == (456737, 24132, "label")
    assert file["feature_count"] == 78 and report["job"]["records"] == 456737
    # Training data is inspect-only: nothing on this screen can refit a model.
    assert not report["job"]["runnable"]
    assert client.post("/api/local-data/run", json={"run_id": run_id}).status_code == 409


# ---- Runs, on the real saved models ------------------------------------------------------------------------
@real_data
def test_full_run_writes_valid_files_that_read_back_from_disk(client, tmp_path, hidden_style):
    run_id = stage(client, "hidden", hidden_style)
    view = run_to_end(client, run_id)
    assert view["state"] == "completed", view["error"]
    assert view["processed"] == 6000 and view["mode"] == "full"
    assert {f["name"] for f in view["outputs"]} >= {"classification.csv", "policies.csv", "replay.csv", "run_summary.json",
                                                    "validation_report.json", "replay_events.jsonl"}
    report = view["validation"]
    assert report["rules_passed"] and report["problems"] == [] and report["labels_read"] is False
    assert view["summary"]["models_refitted"] is False and view["summary"]["causality_problems"] == []
    # A hidden run that passes every rule, with the schema and expiry confirmed, is ready.
    assert report["submission_ready"] and report["blockers"] == []
    out = tmp_path / LIMITS["outputs_dir"] / run_id
    classification = pd.read_csv(out / "classification.csv", dtype=str)
    assert list(classification.columns) == ["record_id", "predicted_class", "anomaly_score", "evidence_status"]
    assert classification["record_id"].tolist() == hidden_style["record_id"].astype(str).tolist()
    # Exactly the organisers' header-only templates, from the stage page.
    assert (out / "classification.csv").read_text().splitlines()[0] == "record_id,predicted_class,anomaly_score,evidence_status"
    assert (out / "policies.csv").read_text().splitlines()[0] == "policy_id,activated_after_record_id,scope_type,scope_value,expires_after_record_id"
    assert (out / "replay.csv").read_text().splitlines()[0] == "record_id,action,active_policy_id"
    assert not (out / "policy.csv").exists()
    assert [(f["name"], f["exists"]) for f in view["submission"]] == [("classification.csv", True), ("policies.csv", True), ("replay.csv", True)]
    assert view["submission"][0]["rows"] == 6000 and view["submission"][2]["rows"] == 6000
    names = zipfile.ZipFile(io.BytesIO(client.get(f"/api/local-data/runs/{run_id}/archive?submission_only=true").content)).namelist()
    assert sorted(names) == [f"{run_id}/classification.csv", f"{run_id}/policies.csv", f"{run_id}/replay.csv"]
    assert len(pd.read_csv(out / "replay.csv")) == 6000


@real_data
def test_a_tampered_output_fails_validation(client, tmp_path, hidden_style):
    run_id = stage(client, "hidden", hidden_style)
    assert run_to_end(client, run_id)["validation"]["rules_passed"]
    replay = tmp_path / LIMITS["outputs_dir"] / run_id / "replay.csv"
    frame = pd.read_csv(replay, dtype=str, keep_default_na=False)
    frame.loc[0, "active_policy_id"] = "POL-99999"
    frame.iloc[1:].to_csv(replay, index=False)
    report = client.post(f"/api/local-data/runs/{run_id}/validate").json()
    assert not report["rules_passed"] and "does not cover every record" in " ".join(report["problems"])


@real_data
def test_the_same_input_gives_identical_files_and_is_not_reprocessed_by_accident(client, tmp_path, hidden_style):
    first = stage(client, "hidden", hidden_style)
    run_to_end(client, first)
    assert client.post("/api/local-data/run", json={"run_id": first}).status_code == 409
    second = stage(client, "hidden", hidden_style)
    refused = client.post("/api/local-data/run", json={"run_id": second})
    assert refused.status_code == 409 and first in refused.json()["detail"]
    run_to_end(client, second, allow_duplicate=True)
    root = tmp_path / LIMITS["outputs_dir"]
    for name in ("classification.csv", "policies.csv", "replay.csv"):
        assert (root / first / name).read_bytes() == (root / second / name).read_bytes()


@real_data
def test_without_endpoints_a1_to_a3_still_run_and_no_address_is_invented(client, tmp_path, labelled):
    run_id = stage(client, "validation", labelled)
    report = inspect(client, run_id)
    assert report["job"]["mode"] == "classification_only" and report["job"]["runnable"]
    assert report["job"]["a4_a5_missing"] == ["source_ip column is absent", "destination_ip column is absent"]
    assert report["job"]["temporal"]["basis"] == "records"
    assert "not a time window" in report["job"]["temporal"]["detail"]
    view = run_to_end(client, run_id)
    assert view["state"] == "completed", view["error"]
    assert view["agents"]["A4"]["state"] == view["agents"]["A5"]["state"] == "unavailable"
    names = {f["name"] for f in view["outputs"]}
    assert "classification.csv" in names and not names & {"policies.csv", "replay.csv"}
    assert view["validation"]["rules_passed"] and not view["validation"]["submission_ready"]
    assert any("Agents 4 and 5 did not run" in b for b in view["validation"]["blockers"])


@real_data
def test_labels_in_a_validation_file_change_no_prediction(client, tmp_path, labelled):
    data = load_config()["data"]
    root = tmp_path / LIMITS["outputs_dir"]
    with_labels = stage(client, "validation", labelled)
    run_to_end(client, with_labels)
    other = stage(client, "validation", labelled.assign(**{data["label_column"]: "DOS"}))
    run_to_end(client, other)
    assert (root / with_labels / "classification.csv").read_bytes() == (root / other / "classification.csv").read_bytes()


@real_data
def test_a_zip_input_runs_like_its_csv_and_the_archive_download_is_complete(client, hidden_style):
    run_id = upload(client, "hidden", ("pack.zip", make_zip({"day2/flows.csv": as_csv(hidden_style), "README.txt": b"x"}))).json()["run_id"]
    report = inspect(client, run_id)
    assert report["files"][0]["display"] == "pack.zip / day2/flows.csv"
    assert report["uploads"][0]["skipped_members"] == ["README.txt"]
    view = run_to_end(client, run_id)
    assert view["state"] == "completed", view["error"]
    response = client.get(f"/api/local-data/runs/{run_id}/archive")
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert f"{run_id}/classification.csv" in names and f"{run_id}/validation_report.json" in names


@real_data
def test_events_report_real_progress_and_policy_activity(client, tmp_path, hidden_style):
    run_id = stage(client, "hidden", hidden_style)
    view = run_to_end(client, run_id)
    body = client.get(f"/api/local-data/runs/{run_id}/events").text
    kinds = {line.removeprefix("event: ") for line in body.splitlines() if line.startswith("event: ")}
    assert {"stage", "agent", "progress", "done", "closed"} <= kinds
    final = [json.loads(line.removeprefix("data: ")) for line in body.splitlines()
             if line.startswith("data: ") and '"type": "progress"' in line][-1]
    assert final["processed"] == 6000 and final["total"] == 6000
    logged = [json.loads(line) for line in
              (tmp_path / LIMITS["outputs_dir"] / run_id / "replay_events.jsonl").read_text().splitlines()]
    assert sum(e.get("event") == "ACTIVATED" for e in logged) == view["summary"]["policies"]


@real_data
def test_policies_on_real_timestamps_explain_the_window_in_seconds(client, tmp_path, hidden_style):
    run_id = stage(client, "hidden", hidden_style)
    view = run_to_end(client, run_id)
    assert view["summary"]["temporal"]["basis"] == "seconds"
    evidence = json.loads((tmp_path / LIMITS["outputs_dir"] / run_id / "agent4_agent5_evidence.json").read_text())
    assert evidence["correlation_groups"], "the synthetic fixture should form groups"
    assert all(g["evidence"]["window_basis"] == "seconds" for g in evidence["correlation_groups"])
    assert all("300 seconds" in g["correlation_reason"] for g in evidence["correlation_groups"])


def split_time(frame: pd.DataFrame) -> pd.DataFrame:
    """The hidden set's real time format: day plus time_offset, with no timestamp column."""
    stamps = pd.to_datetime(frame["timestamp"], format="mixed")
    offsets = [f"{int(t.minute):02d}:{t.second + t.microsecond / 1e6:04.1f}" for t in stamps]
    return frame.drop(columns=["timestamp"]).assign(day=stamps.dt.strftime("%Y-%m-%d"), time_offset=offsets)


@real_data
def test_day_and_time_offset_give_a4_real_seconds_and_are_not_unrecognised(client, tmp_path, hidden_style):
    run_id = stage(client, "hidden", split_time(hidden_style))
    report = inspect(client, run_id)
    file = report["files"][0]
    assert report["job"]["timestamps"] and report["job"]["temporal"]["basis"] == "seconds"
    assert file["unrecognised_columns"] == [] and report["job"]["runnable"]
    view = run_to_end(client, run_id)
    evidence = json.loads((tmp_path / LIMITS["outputs_dir"] / run_id / "agent4_agent5_evidence.json").read_text())
    assert evidence["correlation_groups"] and all(g["evidence"]["window_basis"] == "seconds" for g in evidence["correlation_groups"])
    assert view["validation"]["rules_passed"]


def test_a_time_offset_is_read_as_minutes_and_seconds_and_bad_values_stay_missing():
    from backend.preprocessing.data_processor import derive_timestamp
    out = derive_timestamp(pd.DataFrame({"day": ["2017-07-04", "2017-07-04", "2017-07-05"], "time_offset": ["00:49.8", "59:06.2", "soon"]}))
    assert out["timestamp"].tolist()[:2] == ["2017-07-04 00:00:49.800000", "2017-07-04 00:59:06.200000"]
    assert pd.isna(out["timestamp"].iloc[2])


@real_data
def test_the_tradeoff_report_counts_blocks_by_agent_1s_call(client, tmp_path, hidden_style):
    run_id = stage(client, "hidden", hidden_style)
    view = run_to_end(client, run_id)
    trade = view["summary"]["tradeoff"]
    assert trade["called_attack_blocked"] + trade["called_benign_blocked"] == view["summary"]["actions"].get("BLOCK", 0)
    assert trade["called_attack"] + trade["called_benign"] == 6000 and "not ground truth" in trade["basis"]


@real_data
def test_a_completed_run_can_drive_the_dashboard_with_all_five_agents(client, hidden_style):
    saved = getattr(app.state, "cs", None)
    try:
        run_id = stage(client, "hidden", hidden_style)
        assert client.post(f"/api/local-data/runs/{run_id}/open-in-dashboard").status_code == 409
        run_to_end(client, run_id)
        opened = client.post(f"/api/local-data/runs/{run_id}/open-in-dashboard").json()
        assert opened["defense_available"] and opened["records"] == 6000 and run_id in opened["replay_input"]
        status = client.get("/api/models/status").json()
        assert status["defense_available"] and run_id in status["replay_input"]
        app.state.cs.replay.start(speed=100000)
        for _ in range(400):
            replay = client.get("/api/replay/status").json()
            if replay["status"] == "completed":
                break
            time.sleep(0.05)
        assert replay["status"] == "completed" and replay["correlation_groups"] > 0 and replay["policies_activated"] > 0
        flows = client.get("/api/flows?scope=replay&limit=500").json()["items"]
        assert any(f["correlation_group_id"] for f in flows)
        assert all(f.get("ground_truth_evaluation_only") is None for f in flows)
    finally:
        if getattr(app.state, "cs", None) is not None and app.state.cs is not saved:
            app.state.cs.replay.stop()
        app.state.cs = saved


def stage_page_meta():
    return pd.DataFrame({"flow_id": ["F1", "F2", "F3", "F4", "F5"], "source_ip": ["a", "a", "b", "a", "a"], "destination_ip": ["x"] * 5})


def write_files(out, replay, policies, status="AGREE"):
    ids = [r[0] for r in replay]
    (out / "classification.csv").write_text("record_id,predicted_class,anomaly_score,evidence_status\n" + "".join(f"{r},DOS,0.5,{status}\n" for r in ids))
    (out / "replay.csv").write_text("record_id,action,active_policy_id\n" + "".join(f"{r},{a},{p}\n" for r, a, p in replay))
    (out / "policies.csv").write_text("policy_id,activated_after_record_id,scope_type,scope_value,expires_after_record_id\n" + "".join(",".join(p) + "\n" for p in policies))


GOOD = [("F1", "ALLOW", "NONE"), ("F2", "ALLOW", "NONE"), ("F3", "ALLOW", "NONE"), ("F4", "BLOCK", "P1"), ("F5", "ALLOW", "NONE")]
POLICY = ("P1", "F2", "SOURCE_IP", "a", "F4")


def test_the_stage_page_window_is_exclusive_at_activation_and_inclusive_at_expiry(tmp_path):
    from backend.services.level2 import stage_page_problems
    write_files(tmp_path, GOOD, [POLICY])
    assert stage_page_problems(tmp_path, stage_page_meta()) == []


@pytest.mark.parametrize("replay, policies, status, expected", [
    ([("F1", "ALLOW", "")] + GOOD[1:], [POLICY], "AGREE", "the ledger gives ALLOW NONE"),
    (GOOD, [POLICY], "AGREED_BENIGN", "evidence_status has values"),
    (GOOD[:3] + [("F4", "ALLOW", "NONE")] + GOOD[4:], [POLICY], "AGREE", "the ledger gives BLOCK P1"),
    (GOOD[:4] + [("F5", "BLOCK", "P1")], [POLICY], "AGREE", "the ledger gives ALLOW NONE"),
    (GOOD[:3] + [("F4", "BLOCK", "P2")] + GOOD[4:], [POLICY, ("P2", "F2", "SOURCE_IP", "a", "F4")], "AGREE", "the ledger gives BLOCK P1"),
    (GOOD, [("P1", "F2", "SOURCE_IP", "10.0.0.0/8", "F4")], "AGREE", "not an exact observed endpoint"),
    (GOOD[:-1], [POLICY], "AGREE", "exactly one row per hidden record_id"),
])
def test_the_stage_page_validator_refuses_what_the_page_says_it_refuses(tmp_path, replay, policies, status, expected):
    from backend.services.level2 import stage_page_problems
    write_files(tmp_path, replay, policies, status)
    assert expected in " ".join(stage_page_problems(tmp_path, stage_page_meta()))


@real_data
def test_real_output_passes_the_stage_page_validator_with_its_values(client, tmp_path, hidden_style):
    run_id = stage(client, "hidden", hidden_style)
    view = run_to_end(client, run_id)
    assert view["validation"]["rules_passed"], view["validation"]["problems"]
    out = tmp_path / LIMITS["outputs_dir"] / run_id
    classification, replay = pd.read_csv(out / "classification.csv", keep_default_na=False), pd.read_csv(out / "replay.csv", keep_default_na=False)
    assert set(classification["evidence_status"]) <= {"AGREE", "CONFLICT", "UNCERTAIN"}
    assert set(replay.loc[replay["action"] == "ALLOW", "active_policy_id"]) == {"NONE"}


@real_data
def test_every_decision_is_explained_and_the_policy_card_matches_the_files(client, tmp_path, hidden_style):
    saved = getattr(app.state, "cs", None)
    try:
        run_id = stage(client, "hidden", hidden_style)
        view = run_to_end(client, run_id)
        options = view["summary"]["policy_options"]
        assert [o for o in options if o["submitted"]][0]["policies"] == view["summary"]["policies"]
        client.post(f"/api/local-data/runs/{run_id}/open-in-dashboard")
        app.state.cs.replay.start(speed=100000)
        for _ in range(400):
            if client.get("/api/replay/status").json()["status"] == "completed":
                break
            time.sleep(0.05)
        page = client.get("/api/decisions?limit=500&view=all").json()
        assert page["total"] == 6000 and page["items"][0]["position"] == 6000
        assert all(item["decision_reason"] for item in page["items"])
        out = tmp_path / LIMITS["outputs_dir"] / run_id
        replay = pd.read_csv(out / "replay.csv", keep_default_na=False)
        assert page["counts"]["blocked"] == int((replay["action"] == "BLOCK").sum())
        blocked = client.get("/api/decisions?limit=5&view=blocked").json()["items"]
        assert all(item["decision_reason"].startswith("BLOCK.") and item["applied_policy_id"] for item in blocked)
        impact = client.get("/api/defense/impact").json()["items"]
        policies = pd.read_csv(out / "policies.csv", keep_default_na=False)
        assert [i["policy_id"] for i in impact] == policies["policy_id"].tolist()
        for item in impact:
            assert item["blocked_records"] == int((replay["active_policy_id"] == item["policy_id"]).sum())
            assert sum(item["by_class"].values()) == item["blocked_records"]
            assert sum(p["flows"] for p in item["phases"]) == item["blocked_records"]
    finally:
        if getattr(app.state, "cs", None) is not None and app.state.cs is not saved:
            app.state.cs.replay.stop()
        app.state.cs = saved


def test_agent_5_says_why_it_waits_and_why_it_acts():
    from backend.agents.defense_agent import DefenseConfig
    from backend.agents.temporal_agent import TemporalConfig
    from backend.replay.causal_replay import CausalReplayEngine
    engine = CausalReplayEngine(TemporalConfig(window_records=50), DefenseConfig(min_attack_flows_classifier_only=3, ttl_records=2), None)
    step = lambda i, cls="DOS": engine.step(i, f"R{i}", "a", "x", 80.0, cls, 0.99, 0.1, False, "CLASSIFIER_ONLY")  # noqa: E731
    assert "A4 has no group yet" in step(0)["reason"]
    assert "2 of the 3 confident attack calls needed" in step(1)["reason"]
    assert "triggers POL-00001" in step(2)["reason"]
    assert step(3)["reason"].startswith("BLOCK. a is under POL-00001")


def test_nothing_is_preloaded_and_the_app_asks_for_data(monkeypatch):
    monkeypatch.delenv("CYBERSENTINEL_REPLAY_INPUT", raising=False)
    monkeypatch.delenv("CYBERSENTINEL_NO_AUTOLOAD", raising=False)
    saved = getattr(app.state, "cs", None)
    try:
        with TestClient(app) as client:
            assert app.state.cs is None
            health = client.get("/api/health").json()
            assert health["status"] == "no_data" and "Data Manager" in health["detail"]
            response = client.get("/api/metrics")
            assert response.status_code == 503 and "No data is connected" in response.json()["detail"]
    finally:
        app.state.cs = saved


@real_data
def test_accuracy_comes_from_the_connected_labelled_file(client, labelled):
    saved = getattr(app.state, "cs", None)
    try:
        run_id = stage(client, "validation", labelled)
        run_to_end(client, run_id)
        client.post(f"/api/local-data/runs/{run_id}/open-in-dashboard")
        connected = client.get("/api/metrics").json()["connected"]
        assert connected["labelled"] and connected["rows"] == 3000 and run_id in connected["input"]
        assert connected["evaluation"]["rows"] == 3000 and connected["evaluation"]["accuracy"] > 0.9
        assert sum(r["flows"] for r in connected["by_class"].values()) == 3000
        # These first records are almost all one class, and that must not break the screen.
        assert connected["anomaly_evaluation"] is None or connected["anomaly_evaluation"]["roc_auc"] >= 0
    finally:
        app.state.cs = saved


@real_data
def test_an_unlabelled_file_never_reports_an_accuracy(client, hidden_style):
    saved = getattr(app.state, "cs", None)
    try:
        run_id = stage(client, "hidden", hidden_style)
        run_to_end(client, run_id)
        client.post(f"/api/local-data/runs/{run_id}/open-in-dashboard")
        connected = client.get("/api/metrics").json()["connected"]
        assert not connected["labelled"] and connected["evaluation"] is None and connected["anomaly_evaluation"] is None
        assert connected["rows"] == 6000
    finally:
        if getattr(app.state, "cs", None) is not None and app.state.cs is not saved:
            app.state.cs.replay.stop()
        app.state.cs = saved

