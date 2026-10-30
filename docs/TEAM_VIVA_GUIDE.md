# Team viva guide

For the five members preparing to defend this system. Being named here means you are responsible for explaining this part. It does not mean you wrote it: see [TEAM_OWNERSHIP.md](TEAM_OWNERSHIP.md). If the panel asks who wrote what, answer truthfully. Generated code was reviewed and owned afterwards, and the README discloses it.

Read your file, run your command, and answer your questions from the code, not from this page.

## Everyone must be able to answer

- **Why can a blocked record have a prediction?** Classification and replay are separate steps. Scoring happens first, per record, and replay never changes it.
- **Can a policy block its own trigger?** No. Active policies are checked before analysis, and a new policy is only added after that check.
- **Does a blocked flow add evidence?** No. It returns before A4 and A5 run.
- **How do you know the future isn't leaking?** Rerun on a prefix of the stream and compare: earlier decisions must not change. Every `run_level2.py` run does this.
- **Are your A4 and A5 numbers real?** The flows and model outputs are real. **The addresses are invented**, because the pack has none. They prove the mechanism and do not predict the score.
- **False blocks?** Benign flows from a blocked source are blocked while the policy lasts. That is the containment trade-off. The rehearsal's 325 wrongly blocked benign flows come from an invented 2% of benign traffic sharing attacker addresses.

## Arnav: A1 Threat Detection

**File:** `backend/agents/threat_agent.py`. **Run:** `.venv/bin/python -m pytest tests/agents/test_models_real_data.py -v`

| Question | Point to make |
|---|---|
| Which model and why? | The best of four candidates on an internal holdout, with balanced class weights. Check `data/reports/training_report.json` for the winner |
| Macro-F1 of 0.9998: is it believable? | It reflects this capture. Attack classes have near-constant TCP fingerprints. A harder split on record order gives 0.9973. See the README |
| Could the label leak in? | Labels are split off in `split_representations`. Removing the 25 strongest features still gives 0.9996 (`scripts/leakage_audit.py`) |
| What does confidence do on Day 2? | Below 0.90, an attack call does not count towards a policy |
| Are endpoints model inputs? | No. They stay in metadata. `Destination Port` is a feature because it is in the pack's feature set |

## Arpit: A2 Anomaly Detection

**Files:** `backend/agents/anomaly_agent.py`, `backend/intelligence/feature_explainer.py`. **Run:** `.venv/bin/python -m pytest tests/agents/test_agents.py -v`

| Question | Point to make |
|---|---|
| What is the score? | A percentile among held-out benign flows. 0.97 means more unusual than 97% of them. **Not a probability** |
| Why independent? | It trains on benign flows only, has its own preprocessing, and imports nothing from A1 |
| How good is it? | ROC-AUC 0.884. It catches 89% of DDoS and 73% of DoS, but under 1% of brute force and port scan |
| Why can't it block alone? | Its threshold flags about 5% of benign flows by design. Anomaly-only evidence never creates a policy |
| Does it use hidden-set statistics? | No. Its references come from training-side data. One record scored alone gives the same result as in a batch |

## Lucky: A3 Evidence Fusion

**File:** `backend/agents/fusion_agent.py`. **Run:** `.venv/bin/python -m pytest tests/agents/test_fusion.py -v`

| Question | Point to make |
|---|---|
| The five statuses? | `AGREED_BENIGN`, `MULTI_SIGNAL_ATTACK`, `CLASSIFIER_ONLY`, `ANOMALY_ONLY`, `UNCERTAIN` |
| Why not average the scores? | A class probability and a benign percentile are not on one scale |
| Does fusion improve classification? | No. `final_class` comes from A1. The one override tested failed validation and is off |
| So what is it for? | A5 needs fewer flows when status is `MULTI_SIGNAL_ATTACK` (3) than `CLASSIFIER_ONLY` (5), and `UNCERTAIN` can never trigger a policy |
| What goes in `evidence_status` for a blocked flow? | Its normal A3 status. Replay does not touch it |

## Arush: A4 Temporal Correlation and replay integration

**Files:** `backend/agents/temporal_agent.py`, `backend/replay/causal_replay.py`, `backend/services/level2.py`. **Run:** `.venv/bin/python -m pytest tests/agents/test_temporal_correlation.py tests/replay/test_day2_causal_replay.py -v`

| Question | Point to make |
|---|---|
| Walk through one record | Read `CausalReplayEngine.step` aloud, using [DAY2_REPLAY_LOGIC.md](DAY2_REPLAY_LOGIC.md) |
| What is a group? | Related suspicious flows, by source or by converging destination, inside a window. Two members minimum |
| Which patterns? | Repeated source, one source and destination, one service port, many ports, many sources on one destination |
| Records or seconds? | Records always, seconds too once `window_seconds` is set. The pack has no timestamps, so seconds are only tested on synthetic fixtures |
| What if timestamps go backwards? | The run stops by default, because the order is not trustworthy. We never silently re-sort |
| Show the bug you know about | Before Day 2, a source's zero count stayed in a `Counter` and inflated "distinct sources". `test_a_departed_source_no_longer_counts_towards_converging` covers it |
| What's still unconfirmed? | The `replay.csv` header and the expiry boundary. **Action on you: ask the organisers** |

## Agrim: A5 Adaptive Defence

**File:** `backend/agents/defense_agent.py`. **Run:** `.venv/bin/python scripts/demo_day2.py`

| Question | Point to make |
|---|---|
| When does a policy activate? | The criteria list in [DAY2_REPLAY_LOGIC.md](DAY2_REPLAY_LOGIC.md). Be able to recite why each exists |
| Why a 60% attack share? | It protects shared addresses that mostly send benign traffic |
| Were thresholds tuned? | Compared on 81 settings with `scripts/tune_level2.py`, on the first half of eval with invented addresses, and reported on the second. The best beat ours by 0.0006 at the aggressive edge of the grid, so we kept ours. **Say plainly that the layouts are invented** |
| Why does a repeat offender's policy get longer? | Each new policy for a source doubles, up to 16,000 records |
| How is `expires_after_record_id` known at activation? | It isn't. Length is decided at activation, and the id is filled in when replay reaches that record, so no future record is read |
| What does STOP do? | No new policies. Active ones run to their recorded expiry, because `policy.csv` already committed to that range |
| Do you block anything real? | No. No firewall code exists. Actions are rows in a CSV |

## The demonstration

`.venv/bin/python scripts/demo_day2.py --pause 2`

It runs live and prints each of the brief's 13 steps as replay reaches it. Until the organisers release the hidden file, it runs on eval flows with invented endpoints, and says so. Afterwards, add `--input PATH/TO/hidden.parquet`. If an input doesn't produce a step, the script says which one and exits with an error rather than inventing it.
