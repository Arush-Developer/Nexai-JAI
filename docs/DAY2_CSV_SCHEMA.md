# Day 2 CSV schema

`scripts/run_level2.py` writes these files, then reads them back from disk and checks them.

## classification.csv (exact columns from the brief)

```
record_id,predicted_class,anomaly_score,evidence_status
```

| Column | Rule |
|---|---|
| `record_id` | Every hidden record exactly once, including records later blocked |
| `predicted_class` | `BENIGN`, `BRUTE_FORCE`, `DOS`, `DDOS` or `PORT_SCAN` |
| `anomaly_score` | A2's score, 0 to 1. A percentile among benign calibration flows, not a probability |
| `evidence_status` | A3's: `AGREED_BENIGN`, `MULTI_SIGNAL_ATTACK`, `CLASSIFIER_ONLY`, `ANOMALY_ONLY` or `UNCERTAIN` |

## policy.csv (exact columns from the brief)

```
policy_id,activated_after_record_id,scope_type,scope_value,expires_after_record_id,policy_action
```

| Column | Rule |
|---|---|
| `policy_id` | Unique. Format `POL-00001` |
| `activated_after_record_id` | The triggering record, an organiser id. Its own action is ALLOW |
| `scope_type` | Always `SOURCE_IP` |
| `scope_value` | The triggering record's source address |
| `expires_after_record_id` | A later organiser id. See the boundary convention in [DAY2_REPLAY_LOGIC.md](DAY2_REPLAY_LOGIC.md) |
| `policy_action` | Always `BLOCK` |

## replay.csv (PROVISIONAL header)

```
record_id,action,applied_policy_id
```

**The semantics come from the brief. The header does not: it is our placeholder and has not been confirmed. Arush must confirm it with the organisers.**

When it is confirmed, change only `configs/default.yaml`, and set the flag so the warning stops:

```yaml
level2:
  replay_schema_confirmed: true
  columns:
    replay: {record_id: <official>, action: <official>, applied_policy_id: <official>}
```

`classification` can be renamed the same way. Internal code keeps its own names, and `read_outputs` maps the file back before checking it.

| Meaning | Rule |
|---|---|
| Record | Every hidden record exactly once, in replay order |
| Action | `ALLOW` or `BLOCK` |
| Applied policy | A valid active policy on BLOCK; empty on ALLOW |

## Checks before submitting

`scripts/validate_level2.py` runs these on files already on disk. `scripts/run_level2.py` runs them after writing, and adds check 9.

| # | Requirement | Where |
|---|---|---|
| 1 | Every record once in `classification.csv` | `validate_outputs` |
| 2 | Every record once in `replay.csv` | `validate_outputs` |
| 3 | All classes in the five | `validate_outputs` |
| 4 | Every BLOCK references an existing policy | `validate_outputs` |
| 5 | Policy activated before the blocked record | `validate_outputs` |
| 6 | Policy scope matches the blocked source | `validate_outputs` |
| 7 | Policy had not expired | `validate_outputs`, using the configured boundary |
| 8 | Every ALLOW has an empty policy id | `validate_outputs` |
| 9 | No future record influenced an earlier decision | `check_prefix_invariance`, `run_level2.py` only, because it needs the agents' output |
| 10 | No hidden labels used | By construction: `run_level2` strips labels before scoring. `test_labels_in_the_input_do_not_change_anything` holds it to that |

It also checks the exact header, score range, A3 status values, repeated policy ids, unsupported scope or action, that no blocked record triggered a policy, and that no matching record escaped an active policy.

`agent4_agent5_evidence.json` is written beside the CSVs. It holds groups, policies, the log and final state, and is not a submission file.
