# Final implementation audit

An audit of the repository against the final Day 2 brief, on 31 October 2026, and the repairs made. Test results are in [FINAL_TEST_REPORT.md](FINAL_TEST_REPORT.md).

"Assigned" is the person responsible for reviewing and maintaining a module, from [TEAM_OWNERSHIP.md](TEAM_OWNERSHIP.md). It does not record who wrote it. This code was generated with Claude Code from the team's brief.

## What the brief asked to find

| Looked for | Found |
|---|---|
| Empty files | Only the 11 `__init__.py` package markers. `tests/test_imports.py` checks this |
| Placeholder functions | None. The bare `pass` statements are empty exception classes |
| Broken imports | None. `tests/test_imports.py` imports every module and script |
| Duplicate implementations | None. The live replay and the export share `CausalReplayEngine` |
| Disconnected APIs | `backend/api/submission_adapter.py` is deliberately isolated for Level 1, and refuses until the organisers' details are filled in. No route calls it |
| Hardcoded paths | One: `/opt/homebrew/bin/python3.12` in the README. **Fixed** |
| Fake metrics | None. Published metrics regenerate from scripts, and Test C reproduced them. `scripts/rehearse_level2.py` invents addresses from true labels; it is labelled synthetic, but must not be presented as performance |
| Missing model artifacts | Present here, but **gitignored, so absent from every fresh clone**. Not changed: this is the team's decision. The app now explains it |
| Incomplete replay logic | The time window. **Fixed**, see A4 below |
| Incomplete submission logic | The Level 2 upload is **BLOCKED**, because no API details exist |
| Missing folder selection | The whole Data Manager was missing. **Built** |

## The brief's reported figures, checked against the real artifacts

All match: 78 features with 8 removed and 70 left; `Destination Port`, 3 URG columns and 4 duplicates removed; 24,132 exact duplicates; splits of 319,563, 68,569 and 68,605; A1 with 400 trees and temperature 0.8; A2 with 200 trees, 167,916 fit rows, 36,069 calibration rows and threshold 0.95; seven A3 statuses, of which `classification.csv` uses five; A4 and A5 thresholds; policy durations of 2,000 doubling to 16,000.

One naming difference is left as it is: the agents' API output calls the field `flow_id`. The CSVs use `record_id`, as the organisers' schema does.

## Module by module

| Path | What it does | State found | Problem | Repair | Tests | Assigned |
|---|---|---|---|---|---|---|
| `backend/preprocessing/data_processor.py` | Loading, cleaning, separating features, labels and metadata | Working | CSV record IDs were parsed as numbers, so `007` became `7`. `Label` and `Record ID` spellings were not recognised | Added `read_flow_frame`, and normalised those two spellings | `tests/api/test_local_data.py`, `tests/agents/test_preprocessing.py` | Arpit |
| `backend/agents/threat_agent.py` | A1 | Working | None | None | `tests/agents/test_agents.py`, `tests/agents/test_models_real_data.py` | Arnav |
| `backend/agents/anomaly_agent.py` | A2 | Working | None | None | `tests/agents/test_agents.py` | Arpit |
| `backend/agents/fusion_agent.py` | A3 | Working | None | None | `tests/agents/test_fusion.py` | Lucky |
| `backend/agents/temporal_agent.py` | A4 | Partial | `window_seconds` was unset, and the record limit still applied when timestamps existed. Explanations could mix the two | Seconds now replace the record count when timestamps exist. Added `window_basis` to the evidence and `window_label` to the explanations | `tests/agents/test_temporal_correlation.py` | Arush |
| `backend/agents/defense_agent.py` | A5 | Working | The policy rationale always described a record window | It now states the real basis | `tests/agents/test_temporal_defense.py` | Agrim |
| `backend/replay/causal_replay.py` | The record-by-record state machine | Working | None | None | `tests/replay/test_day2_causal_replay.py` | Arush |
| `backend/services/level2.py` | CSV writers, validator | Working | An input with no endpoints could only fail, though A1 to A3 could still run. Stages were not reported | Added `score_classification`, `write_classification`, `validate_classification` and an `on_agent` callback | `tests/integration/test_level2.py`, `tests/api/test_local_data.py` | Arush |
| `backend/services/local_data.py` | Staging, inspection, runs, validation | **Missing** | The brief's main feature | **New** | `tests/api/test_local_data.py` | Arush |
| `backend/api/local_data.py` | The Data Manager's routes | **Missing** | Also: CORS cannot stop a form post from another website | **New**, refusing non-local origins | `tests/api/test_local_data.py` | Arush |
| `backend/main.py` | Entry point | Working | The routes were not mounted | Mounted | `tests/api/test_api.py` | Arush |
| `backend/schemas/contracts.py`, `frontend/src/services/types.ts` | Shared contracts | Working | No Data Manager types or `window_basis` | Added | `tests/api/test_contract_sync.py` | Arush, Lucky |
| `configs/default.yaml` | Configuration | Working | `window_seconds` was null. No flag for the unconfirmed expiry convention. No limits | Set 300 as an untuned default, and added `expiry_boundary_confirmed` and `local_data` | `tests/api/test_local_data.py` | Arush |
| `requirements.txt` | Dependencies | Incomplete | File uploads need `python-multipart` | Added | Clean-copy install | Arush |
| `.gitignore` | Keeps data out of git | Incomplete | The new data folders were not covered | Added `data/staging`, `outputs`, `inputs` and `day2` | `git status` after runs | Arush |
| `frontend/src/components/Layout.tsx` | App shell | Partial | **Every screen waited for the official dataset, so a laptop without it could never reach the Data Manager** | The Data Manager now renders regardless | `frontend/e2e/data-manager.spec.ts`, no-dataset mode | Lucky |
| `frontend/src/data-manager/DataManager.tsx`, `frontend/src/services/localData.ts` | The Data Manager screen and folder access | **Missing** | The brief's main feature | **New** | `frontend/e2e/data-manager.spec.ts` | Lucky |
| `frontend/src/app/router.tsx`, `frontend/src/components/Sidebar.tsx` | Routes, navigation | Working | No Data Manager entry | Added | `frontend/e2e/app.spec.ts` | Lucky |
| `scripts/run_local.sh` | One-command startup | **Missing** | | **New** | Test A | Arush |
| `scripts/make_local_fixture.py` | Synthetic hidden-style input | **Missing** | The real hidden set is not released | **New**, with addresses from a feature, never from labels | `tests/api/test_local_data.py` | Arush |
| `README.md`, `docs/API.md`, `docs/DAY2_REPLAY_LOGIC.md`, `docs/TEAM_OWNERSHIP.md` | Documentation | Working | One hardcoded path, an outdated window description, and no Data Manager | Updated | Reviewed | Arush |

## Choices worth reviewing

- **Parquet is accepted as well as CSV and ZIP.** The brief says CSV and ZIP, but the organisers' real ZIP holds Parquet.
- **`window_seconds` is 300.** This is an engineering default. Revisit it when real timestamps can be inspected, without using labels.
- **A file declared hidden that has a label column is refused.** The brief says hidden data ships without labels, so this most likely means the wrong file.
- **Without endpoints, no `replay.csv` or `policy.csv` is written.** An all-ALLOW file would look like a replay that never happened.
- **"Ready to submit" stays off until the organisers confirm the header and expiry convention,** even when every rule passes.
- **Training data is inspect-only.** This screen cannot refit a model, which is also how hidden data is kept out of training.
