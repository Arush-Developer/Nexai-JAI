# Day 2 technical flow

How a file a person picks in Chrome becomes the three Day 2 CSVs, and which code does each step.

This code was generated with Claude Code from the team's brief. "Owner" below means the person responsible for reviewing, explaining and maintaining a module, as defined in [TEAM_OWNERSHIP.md](TEAM_OWNERSHIP.md). It does not mean they wrote it.

## End to end

```
Chrome, Data Manager screen (frontend/src/data-manager/DataManager.tsx)
  showDirectoryPicker(), or <input webkitdirectory> as the fallback
    | selected File objects, multipart, to localhost only
    v
POST /api/local-data/upload    data/staging/<RUN>/originals/   SHA-256, unique run id, safe ZIP extraction
POST /api/local-data/inspect   the real schema of each file, checked against the chosen dataset type
POST /api/local-data/run       a background thread:
    |
    |  labels dropped here, before any agent runs
    v
OPERATION A: every record, independently (level2.score_records)
    A1 CatBoost -> A2 Isolation Forest -> A3 evidence rules -> classification.csv
    v
OPERATION B: one record at a time, in supplied order (CausalReplayEngine.step)
    expire lapsed policies -> check active policies -> BLOCK, adding no new evidence
                                                    -> or ALLOW -> A4 observes -> A5 may activate, for LATER records only
    v
Prefix check: rerun on 25%, 50% and 75% of the stream. Any changed earlier decision means a future record leaked in.
    v
data/outputs/<RUN>/   classification.csv, policy.csv, replay.csv, run_summary.json, validation_report.json, ...
    |  read back from disk and checked against the input
    v
Chrome   CHOOSE OUTPUT FOLDER, then createWritable() for each file, then its size is read back
```

Progress reaches the browser as Server-Sent Events from `GET /api/local-data/runs/{id}/events`.

## Rules the code enforces

| Rule | Where |
|---|---|
| The backend never opens a path the browser chose | The browser posts file contents only (`services/localData.ts`) |
| Requests from other websites are refused | `local_origin_only` in `backend/api/local_data.py`, and uvicorn binds to `127.0.0.1` |
| A client file name cannot escape staging | `_safe_name`, plus a generated `F001_` prefix |
| ZIP traversal, links, encrypted members and oversized expansion are refused | `member_problem`, `extract_archive` |
| Archived programs are never unpacked or run | Only `.csv` and `.parquet` members are extracted |
| The dataset type must agree with the schema, not the file name | `apply_dataset_rules` |
| Hidden data cannot reach training or calibration | This API has no route that fits anything, and those scripts read only the paths in the config. Training uploads are inspect-only |
| Incompatible or unordered files are never joined | `combine_job` |
| No endpoint, timestamp or feature value is invented | `endpoint_gaps` turns Agents 4 and 5 off, and missing features block the run. Missing or infinite values in a present feature take the fitted training median, and the report counts them |
| Record IDs stay exact, so `007` stays `007` | `read_flow_frame` |
| The same input cannot be processed twice by accident | `identity` in the manifest, and `start_run` |
| A record count is never presented as a time window | `window_basis` and `window_label` in `temporal_agent.py` |
| "Ready to submit" needs more than passing the rules | `validate` in `local_data.py` lists the blockers |

## Decisions that are configurable

All in `configs/default.yaml`.

| Setting | Now | Status |
|---|---|---|
| `level2.temporal.window_seconds` | 300 | Engineering default. No real timestamps exist to tune it on |
| `level2.defense.expiry_boundary` | `inclusive` | Unconfirmed by the organisers |
| `level2.expiry_boundary_confirmed` | `false` | Set true once confirmed |
| `level2.columns.replay` | `record_id, action, applied_policy_id` | Unconfirmed by the organisers |
| `level2.replay_schema_confirmed` | `false` | Set true once confirmed |
| `level2.replay_order.dayfirst` | `false` | Check it against the real timestamps |

## Each member's part

Run the tests from the repository root, with `.venv/bin/python -m pytest` before each path.

### Arnav: A1 Threat Detection

- **Files:** `backend/agents/threat_agent.py`
- **Entry points:** `ThreatAgent.load`, `predict_proba(features)`, `to_output(flow_id, proba)`
- **Input:** feature frame, using the saved pipeline only
- **Output:** one probability per class; `to_output` gives `flow_id` (written as `record_id` in the CSVs), `predicted_class`, `confidence`, `probabilities`
- **Tests:** `tests/agents/test_agents.py tests/agents/test_models_real_data.py`
- **Run alone:** `scripts/score_records.py`

### Arpit: A2 Anomaly Detection

- **Files:** `backend/agents/anomaly_agent.py`, `backend/preprocessing/data_processor.py`
- **Entry points:** `AnomalyAgent.load`, `score(features)`, `supporting_features(features)`, `to_output(flow_id, score, supporting)`
- **Input:** feature frame, through its own fitted pipeline
- **Output:** `anomaly_score`, `is_anomaly` (at or above 0.95), `supporting_features`. The score is a percentile among benign flows. **0.97 does not mean a 97% chance of attack.**
- **Tests:** `tests/agents/test_agents.py tests/agents/test_preprocessing.py`

### Lucky: A3 Evidence Fusion, and the Data Manager screen

- **Files:** `backend/agents/fusion_agent.py`, `frontend/src/data-manager/DataManager.tsx`, `frontend/src/services/localData.ts`
- **Entry points:** `fuse_batch(proba, classes, scores, threshold, config)`, `fusion_output(...)`, `DataManager()`, `exportRun(...)`
- **Input:** A1 probabilities and A2 scores, which are never averaged together
- **Output:** `final_class`, `evidence_status`, rationale. `classification.csv` uses five statuses. `CAMPAIGN_ONLY` and `CAMPAIGN_CORROBORATED` are for the website only
- **Tests:** `tests/agents/test_fusion.py`, and `npx playwright test e2e/data-manager.spec.ts` from `frontend/`

### Arush: A4 Temporal Correlation, replay integration and the local-data backend

- **Files:** `backend/agents/temporal_agent.py`, `backend/replay/causal_replay.py`, `backend/services/level2.py`, `backend/services/local_data.py`, `backend/api/local_data.py`
- **Entry points:** `TemporalCorrelationAgent.observe(...)`, `public_groups()`, `CausalReplayEngine.step(...)`, `level2.score_records`, `level2.replay_scored`, `LocalDataService.create_upload`, `.inspect`, `.start_run`, `.validate`
- **Input:** an allowed record's A1 to A3 output, endpoints and timestamp, one at a time
- **Output:** group IDs, member record IDs, supporting evidence, and a reason that claims no attack is proven
- **Tests:** `tests/agents/test_temporal_correlation.py tests/replay/ tests/api/test_local_data.py tests/integration/`
- **Run alone:** `scripts/run_level2.py --input FILE`, `scripts/validate_level2.py`

### Agrim: A5 Adaptive Defence

- **Files:** `backend/agents/defense_agent.py`
- **Entry points:** `begin_record`, `apply_active`, `consider`, `advance`, `finish`, `public_policies`
- **Input:** A3 and A4 evidence for the current record
- **Output:** temporary simulated `SOURCE_IP` `BLOCK` policies, with `policy_id`, `activated_after_record_id`, `scope_type`, `scope_value`, `expires_after_record_id`, `policy_action`
- **Policy criteria:** A1 confidence at least 0.90, not `UNCERTAIN`, an A4 group, at least 60% of the source's window being attacks, and 2 confident attacks when converging, 3 with A2 support, or 5 with A1 alone
- **Duration:** 2,000 replay positions, doubling for repeats, up to 16,000. Expiry uses positions, then converts to the real record ID. Record ID numbers are never subtracted
- **Tests:** `tests/agents/test_temporal_defense.py tests/replay/test_day2_causal_replay.py`

Every member starts the application the same way: `./scripts/run_local.sh`, then http://localhost:5173/data. See the [Local operator guide](LOCAL_OPERATOR_GUIDE.md).
