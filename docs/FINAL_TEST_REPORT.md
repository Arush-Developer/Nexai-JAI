# Final test report

Executed on 31 October 2026, on one MacBook, in Chrome. Every result below was run. Statuses are PASS, FAIL, BLOCKED or NOT VERIFIED.

**The hidden set is not released, so nothing here has run on it, and Agents 4 and 5 have never run on real endpoints.**

## Summary

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Codebase health | PASS | 280 backend tests, 17 browser tests, type check and build all pass |
| 2 | Files repaired | PASS | See [FINAL_IMPLEMENTATION_AUDIT.md](FINAL_IMPLEMENTATION_AUDIT.md) |
| 3 | Empty files completed | PASS, none needed | The only empty files are the 11 `__init__.py` package markers |
| 4 | A1 | PASS on real records | 400 trees, temperature 0.8, 70 features |
| 5 | A2 | PASS on real records | 200 trees, 167,916 benign fit rows, 36,069 calibration rows, threshold 0.95 |
| 6 | A3 | PASS on real records | Status counts match the published evaluation |
| 7 | A4 | PASS on synthetic data; BLOCKED on real endpoints | The pack has no endpoints or timestamps |
| 8 | A5 | PASS on synthetic data; BLOCKED on real endpoints | The pack has no endpoints |
| 9 | Model loading and inference | PASS | Macro-F1 0.99982 through the new path |
| 10 | Temporal correlation | PASS on synthetic data | `tests/agents/test_temporal_correlation.py` |
| 11 | Policy activation and expiry | PASS on synthetic data | `tests/replay/test_day2_causal_replay.py`, both conventions |
| 12 | Chronological replay | PASS on synthetic endpoints | 20,000 real flows, invented endpoints |
| 13 | Local input folder connector | PASS, except the native dialog: NOT VERIFIED | See Test B |
| 14 | Local output folder connector | PASS, except the native dialog: NOT VERIFIED | See Test B |
| 15 | Frontend and backend integration | PASS | Real Chrome, real backend, real models |
| 16 | CSV validation | PASS | Files read back from disk; a tampered file fails |
| 17 | Official API integration | BLOCKED | The organisers' Level 2 API details are not available |
| 18 | Remaining blockers | See below | |
| 19 | Commands to run | See below | |

## Suites

| Suite | Command | Result |
|---|---|---|
| Backend, with real models and data | `.venv/bin/python -m pytest` | **280 passed, 0 failed, 0 skipped** |
| Data Manager in Chrome | `npx playwright test e2e/data-manager.spec.ts`, from `frontend/` | **3 passed**, 1 skipped (it needs the mode below) |
| Data Manager with no dataset | `E2E_NO_DATASET=1 CYBERSENTINEL_CONFIG=<config with no eval file> npx playwright test e2e/data-manager.spec.ts` | **1 passed**, 3 skipped (they need the dataset) |
| All other screens in Chrome | `E2E_REPLAY_INPUT="$PWD/../data/processed/rehearsal/flows.parquet" npx playwright test e2e/app.spec.ts` | **13 passed** |
| Type check and build | `npm --prefix frontend run build` | Passed. Vite warns the bundle is over 500 kB |
| Clean copy, in a folder with spaces in its name | New `.venv` from `requirements.txt`, `npm ci`, models copied in, no dataset | **198 passed, 80 skipped** (they need the dataset). Build passed, and a 20,000-record run completed |

## The brief's tests

### Test A: startup. PASS on Mac

`./scripts/run_local.sh` started the backend and frontend. The backend bound to `127.0.0.1` only, health reported `ok` after 3 seconds, the frontend returned 200, and the API answered through the frontend's proxy. A1 and A2 loaded, and stopping the script with a terminate signal freed both ports. Ctrl+C uses the same cleanup, but was not pressed.

**Windows: NOT VERIFIED.** The commands are documented but were not run.

### Test B: local folder connector. PASS, with one gap

Run in real Chrome: detecting CSV and ZIP files, choosing files, uploading the File objects to localhost, inspecting schemas, processing, exporting, and comparing every exported file byte-for-byte with what the backend generated. An existing run folder was left unchanged until replacing it was approved. The `webkitdirectory` fallback and the downloads also passed.

**The native folder dialogs: NOT VERIFIED.** A test cannot click Chrome's dialog, so the test replaces `showDirectoryPicker` with one returning a real handle from the browser's private file system. Everything after the dialog is the real API. **A person should click through it once on a real folder.**

### Test C: model inference on labelled validation. PASS

The real 74,987,240-byte organiser ZIP went through the HTTP API, and its eval partition was processed. The labels were then used only to score the written `classification.csv`:

| Measure | Data Manager | Published evaluation |
|---|---|---|
| Records | 114,185 | 114,185 |
| Macro-F1 | 0.99982 | 0.99982 |
| Accuracy | 0.99995 | 0.99995 |
| `AGREED_BENIGN` | 57,000 | 57,000 |
| `MULTI_SIGNAL_ATTACK` | 30,290 | 30,290 |
| `CLASSIFIER_ONLY` | 23,893 | 23,893 |
| `ANOMALY_ONLY` | 3,001 | 3,001 |
| `UNCERTAIN` | 1 | 1 |

Inspection also reproduced the profile: 456,737 training records, 78 features, 24,132 exact duplicates.

### Test D: temporal and policy fixtures. PASS

Clearly synthetic. `test_the_final_brief_sequence_r001_to_r005_then_expiry` runs the brief's exact sequence: R001 ALLOW, R002 ALLOW with activation after it, R003 a different source and ALLOW, R004 and R005 BLOCK with the policy's ID, then expiry. The exact boundary is tested under both conventions.

### Test E: chronological integration. PASS for what the data permits, otherwise BLOCKED

- **Genuine labelled records, A1 to A3: PASS.** See Test C. Agents 4 and 5 correctly reported `unavailable`, naming the missing columns, and no address was invented.
- **Genuine endpoints, A4 and A5: BLOCKED.** The pack has none.
- **Mechanism only: PASS.** `scripts/make_local_fixture.py` attaches invented documentation-range addresses and timestamps to 20,000 real flows. Addresses come from each flow's Destination Port, never from its label. Result: 474 groups, 55 policies, 9,692 blocks, 0 rule violations. **These numbers are not performance, and depend on how the addresses were invented.**

### Test F: output validation. PASS

Run on files read back from disk: exact coverage in both files, no repeated IDs, valid classes, valid policy references, every BLOCK matching its policy's source and range, every ALLOW with an empty policy ID, and no label read. A tampered `replay.csv` failed. Labels changed in a validation file changed no prediction, byte-for-byte. Identical input produced byte-identical CSVs.

### Test G: frontend. PASS, with the same gap as Test B

Type check, build, rendering, folder selection (see Test B), replay controls, five-agent status, API connectivity, real progress, and error states all ran in Chrome. Each button on the Data Manager was clicked in a test, except that the private-file-system handle stands in for the native dialog.

## Remaining blockers

| Blocker | Who can clear it | Effect |
|---|---|---|
| The hidden set is not released | Organisers | A4 and A5 are unproven on real endpoints. `window_seconds` (300) and the policy thresholds are untuned on real data |
| The `replay.csv` header is unconfirmed | Organisers | Every run says "Not ready to submit" |
| The expiry convention is unconfirmed | Organisers | Every run says "Not ready to submit" |
| The Level 2 API is unknown | Organisers | There is no upload. Files are exported only |
| Models are not in git | The team to decide | A fresh clone cannot run until `models/` is copied or rebuilt |
| Native dialogs were not clicked by a person | Anyone, in 2 minutes | See Test B |
| Windows was not run | A teammate on Windows | Documented commands are unproven |

**Also known:** the other dashboard screens still show the official validation set, not an uploaded run. Replay of an uploaded file is watched in the Data Manager. `scripts/rehearse_level2.py` invents addresses from true labels, so its report must never be presented as performance, and it was not used for Test E.

## Exact commands

Setup is in the [Local operator guide](LOCAL_OPERATOR_GUIDE.md). Then:

```bash
./scripts/run_local.sh
```

Open http://localhost:5173/data in Chrome.
