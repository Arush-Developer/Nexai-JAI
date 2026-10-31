# Test report

Executed on 31 October 2026, on one MacBook, in Chrome. Every result below was run. Statuses are PASS, BLOCKED or NOT VERIFIED.

The hidden set ran end to end through the website. It has no labels here, so its accuracy can only come from the organisers' score.

## Suites

| Suite | Command | Result |
|---|---|---|
| Backend, with real models and data | `.venv/bin/python -m pytest` | **299 passed, 0 failed, 0 skipped** |
| Data Manager in Chrome, with the real hidden file | `E2E_HIDDEN=<folder> E2E_SHOTS=<folder> npx playwright test e2e/data-manager.spec.ts`, from `frontend/` | **4 passed**, 1 skipped (it needs the mode below) |
| Data Manager with no copy of the dataset | `E2E_NO_DATASET=1 CYBERSENTINEL_CONFIG=<config with no eval file> npx playwright test e2e/data-manager.spec.ts` | **1 passed**, 4 skipped (they need the dataset) |
| All other screens, including the 3D Command Centre | `E2E_REPLAY_INPUT="$PWD/../data/processed/rehearsal/flows.parquet" npx playwright test e2e/app.spec.ts` | **17 passed** |
| Type check and build | `npm --prefix frontend run build` | Passed. Vite warns the bundle is over 500 kB |
| Clean install, in a folder with spaces in its name | New `.venv` from `requirements.txt`, `npm ci`, models copied in, no dataset | Passed on an earlier build today, before the final screens: 198 passed, 80 skipped (they need the dataset) |

## Status by area

| Area | Status | Evidence |
|---|---|---|
| A1, A2 and A3 on labelled records | PASS | Macro-F1 0.99990 on 114,185 records |
| A1 to A5 on the hidden set | PASS | 200 records, 1 group, 1 policy, 97 blocks, 0 rule violations |
| A4 and A5 accuracy on real endpoints | BLOCKED | The labelled pack has no endpoints, and the hidden set has no labels |
| Policy activation and expiry | PASS on synthetic data | `tests/replay/test_day2_causal_replay.py`. On the hidden set, the policy's expiry is the end of the file |
| Chronological replay | PASS | The same input gives byte-identical files. Rerunning on parts of the stream changes no earlier decision |
| Submission format | PASS | Headers are byte-identical to the organisers' templates. A separate check recomputes `replay.csv` from `policies.csv` by their published algorithm |
| Organisers' validator | NOT VERIFIED | Our check rebuilds their published rules. Only their upload is final |
| Local input and output folders | PASS, except the native dialog | See below |
| Windows | NOT VERIFIED | Commands are documented, but were not run |

## Details

**Labelled records.** The real organiser ZIP went through the website's API. The labels were used only to score the written `classification.csv`, and changing them changed no prediction.

**Folders.** In real Chrome: detecting CSV and ZIP files, uploading them to localhost, inspecting schemas, processing, exporting, and comparing each exported file byte-for-byte with what the backend generated. An existing run folder was left unchanged until replacing it was approved. The fallback picker and downloads also passed. A test cannot click Chrome's native folder dialog, so the tests replace it with one returning a real handle from the browser's private file system. Everything after the dialog is the real API.

**Temporal and policy fixtures.** Clearly synthetic. `test_the_final_brief_sequence_r001_to_r005_then_expiry` runs: R001 ALLOW, R002 ALLOW with activation after it, R003 a different source and ALLOW, R004 and R005 BLOCK with the policy's ID, then expiry.

**Command Centre.** The test checks that the orchestrator turns to each of the five agents as it works, and that a policy is announced when it really activates. The tests ran on software graphics, so smoothness on a real graphics card was not measured.

## Known gaps

- The app starts empty and asks for data. Restarting the backend clears the connected run.
- The Command Centre steps through the newest flow's real results at a watchable pace, because the pipeline scores whole batches in milliseconds. Agents 4 and 5 work up to one batch ahead of the display.
- `scripts/rehearse_level2.py` invents addresses from true labels, so its report must never be presented as performance.
