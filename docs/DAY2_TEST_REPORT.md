# Day 2 test report

Executed on 30 October 2026, on this machine. Everything below was run. Nothing was run on the hidden set, which has not been released.

| Suite | Command | Result |
|---|---|---|
| Backend, including real-model integration | `.venv/bin/python -m pytest` | **236 passed, 0 failed, 0 skipped** |
| Browser, real backend and models, rehearsal input | `E2E_REPLAY_INPUT="$PWD/data/processed/rehearsal/flows.parquet" npm --prefix frontend run test:e2e` | **12 passed** |
| Frontend type check and build | `npm --prefix frontend run build` | Passed |

The browser suite was not re-run on the default eval input, because that run overwrites the saved replay state in `data/processed`. A read-only load of that saved state with the new engine succeeded.

## The brief's twelve required tests

In `tests/replay/test_day2_causal_replay.py`, on labelled synthetic fixtures. All passed.

| # | Brief's case | Test |
|---|---|---|
| 1 | Suspicious, no active policy: ALLOW | `test_01_first_suspicious_record_with_no_active_policy_is_allowed` |
| 2 | Repeat: group updated, activated after it | `test_02_repeated_behaviour_updates_a_group_and_activates_after_that_record` |
| 3 | Different source: ALLOW | `test_03_a_different_source_is_allowed` |
| 4 | Blocked source during policy: BLOCK with id | `test_04_matching_source_during_an_active_policy_is_blocked_with_its_policy_id` |
| 5 | After expiry: no longer blocked by it | `test_05_record_after_expiry_is_no_longer_blocked_by_that_policy` |
| 6 | Trigger is unaffected by its own policy | `test_06_a_policy_never_affects_its_own_triggering_record` |
| 7 | BLOCK with nonexistent policy fails | `test_07_block_referencing_a_nonexistent_policy_fails_validation` |
| 8 | ALLOW with policy id fails | `test_08_allow_with_an_applied_policy_id_fails_validation` |
| 9 | Future record in history fails | `test_09_a_future_record_in_correlation_history_fails_the_causality_check`, `test_09b_records_arriving_out_of_order_are_refused` |
| 10 | Blocked row missing from classification fails | `test_10_a_blocked_row_missing_from_classification_fails_validation` |
| 11 | Unsupported scope fails | `test_11_an_unsupported_policy_scope_fails_validation` |
| 12 | Boundary matches the convention | `test_12_expiry_at_the_boundary_record_matches_the_configured_convention` (both conventions), `test_12b_validator_accepts_its_own_convention_and_rejects_the_other` |

The same file also covers: blocked records adding no evidence, STOP, determinism, no activation without a later record, expiry clamping, and the state the brief asks the machine to maintain.

## The brief's A4 tests

In `tests/agents/test_temporal_correlation.py`. All passed.

| Case | Test |
|---|---|
| Same source and destination | `test_same_source_and_destination_are_grouped_with_the_required_output` |
| Multiple sources, common destination | `test_multiple_sources_on_a_common_destination_form_a_group_with_earlier_flows_as_members` |
| Repeated destination-port activity | `test_repeated_activity_on_one_destination_port`, `test_many_destination_ports_from_one_source` |
| Unrelated flows | `test_unrelated_flows_do_not_share_a_group` |
| Expired windows | `test_expired_record_window_drops_old_flows_and_opens_a_new_group`, `test_expired_time_window_applies_even_when_few_records_have_passed` |
| Insufficient evidence | `test_one_suspicious_flow_is_insufficient_evidence_for_a_group` |
| Non-monotonic timestamps | `test_non_monotonic_timestamps_raise_by_default`, `test_non_monotonic_timestamps_can_be_reported_and_cannot_revive_old_flows`, `test_order_check_raises_on_backward_timestamps_and_can_report_instead` |

## Real models on real eval flows

In `tests/integration/test_level2.py`, on the saved Day 1 models and 12,000 official eval flows, with **invented endpoints**. All passed. They cover:

- Blocked records keeping their predictions, and replay leaving classification untouched.
- One record scored alone matching the batch result.
- Earlier decisions surviving removal or scrambling of later records.
- Labels in the input changing nothing.
- Files read back from disk passing, and tampered ones failing.
- The exclusive convention producing valid files.
- The live replay matching the export.
- Resuming after a stop matching an uninterrupted run.

## Bugs Day 2 work found and fixed

| Bug | Effect | Test |
|---|---|---|
| `replay_order` silently sorted by timestamp or numeric id | Violated "do not silently reorder" | `test_replay_order_follows_seq_or_else_the_file_and_never_sorts_on_its_own` |
| A zero count stayed in A4's source counter | Departed sources still counted towards "converging", lowering the bar for a policy | `test_a_departed_source_no_longer_counts_towards_converging` |
| A policy activated on the final record had `expires == activated` | Our own checker would have rejected the whole submission | `test_a_policy_activated_on_the_final_record_is_not_published`, `test_no_policy_is_activated_when_no_later_record_exists` |
| One flow formed a "group" | 2,453 groups on the rehearsal, now 658, and destination groups omitted their earlier flows | `test_one_suspicious_flow_is_insufficient_evidence_for_a_group` |
| Benign flows' ports counted towards scan evidence | A busy benign client could be named as scanning | `test_benign_ports_do_not_make_a_source_look_like_a_scanner` |

Three existing tests changed because they asserted the old behaviour: the silent timestamp sort in `test_preprocessing.py`, the shuffled `seq` case in `test_level2.py`, and single-member groups and same-record expiry in `test_temporal_defense.py`.

## Measured results: A1 to A3 on the official eval partition

Macro-F1 0.99982, accuracy 0.99995, over 114,185 records. Status counts: `AGREED_BENIGN` 57,000; `MULTI_SIGNAL_ATTACK` 30,290; `CLASSIFIER_ONLY` 23,893; `ANOMALY_ONLY` 3,001; `UNCERTAIN` 1.

## Measured results: A4 and A5, invented endpoints

**These show the mechanism working. They do not predict hidden-set scores, because they depend on how the addresses were invented.** Real eval flows, one source per attack class, 30 sources for DDoS, and 2% of benign flows sharing an attacker's address (`scripts/rehearse_level2.py`):

| Measure | Inclusive | Exclusive |
|---|---|---|
| Policies | 164 | 164 |
| Temporal groups | 658 | 658 |
| Blocks | 54,098 | 54,096 |
| Attack flows blocked | 99.24% | 99.24% |
| Benign flows blocked | 325 (0.54%) | 325 (0.54%) |
| Rule violations | 0 | 0 |

### Threshold comparison

`scripts/tune_level2.py`, 81 settings, chosen on the first half of eval and reported on the second, across two invented layouts. Objective: the worst layout's attack-blocked share minus 5 times its benign-blocked share.

| Second half of eval | Attack blocked | Benign blocked |
|---|---|---|
| Kept (3 / 5 / 0.60 / TTL 2000), per-class layout | 99.857% | 145 flows |
| Grid's best (2 / 3 / 0.50 / TTL 2000), per-class layout | 99.901% | 143 flows |
| Kept, single-attacker layout | 99.901% | 634 flows |
| Grid's best, single-attacker layout | 99.940% | 638 flows |

**Decision: kept the existing thresholds.** The gain is 0.04 points, on invented addresses, at the aggressive edge of the grid. That is not a reason to block more readily on real traffic.

## Not tested

- **Anything on the hidden set or on real endpoints.** Not released yet.
- **Real timestamps.** The pack has none, so the time window and the timestamp check have only run on synthetic and generated timestamps.
- **Organiser acceptance of the files.** The `replay.csv` header and expiry boundary are unconfirmed, and the upload API is not known.
