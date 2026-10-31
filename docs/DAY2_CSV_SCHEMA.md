# CSV schema

The three files the organisers' stage page requires. `scripts/run_level2.py` and the Data Manager write them, read them back from disk, and check them.

## classification.csv

```
record_id,predicted_class,anomaly_score,evidence_status
```

| Column | Rule |
|---|---|
| `record_id` | Every hidden record exactly once, including records later blocked |
| `predicted_class` | `BENIGN`, `BRUTE_FORCE`, `DOS`, `DDOS` or `PORT_SCAN` |
| `anomaly_score` | A2's score, 0 to 1. A percentile among benign calibration flows, not a probability |
| `evidence_status` | `AGREE`, `CONFLICT` or `UNCERTAIN` |

A3 keeps finer statuses on the website, mapped in `level2.evidence_status_values`:

| A3 status | Meaning | In the file |
|---|---|---|
| `AGREED_BENIGN` | A1 says benign, and A2 finds it normal | `AGREE` |
| `MULTI_SIGNAL_ATTACK` | A1 calls an attack, and A2 flags it | `AGREE` |
| `CLASSIFIER_ONLY` | A1 calls an attack, but A2 finds it normal | `CONFLICT` |
| `ANOMALY_ONLY` | A1 says benign, but A2 flags it | `CONFLICT` |
| `UNCERTAIN` | A1's confidence is below the threshold | `UNCERTAIN` |

## replay.csv

```
record_id,action,active_policy_id
```

| Column | Rule |
|---|---|
| `record_id` | Every hidden record exactly once |
| `action` | `ALLOW` or `BLOCK` |
| `active_policy_id` | For BLOCK, the smallest matching policy id. For ALLOW, `NONE` |

## policies.csv

```
policy_id,activated_after_record_id,scope_type,scope_value,expires_after_record_id
```

| Column | Rule |
|---|---|
| `policy_id` | Unique. Format `POL-00001` |
| `activated_after_record_id` | The triggering record. The policy starts on the next record, and the trigger itself stays ALLOW |
| `scope_type` | `SOURCE_IP`. The organisers also allow `DESTINATION_IP` and `SRC_DST_PAIR`, which this system does not use |
| `scope_value` | The exact source address observed on the triggering record |
| `expires_after_record_id` | The last active record, inclusive |

## Checks

| Check | Where |
|---|---|
| Exact headers and values | `read_outputs`, `stage_page_problems` |
| One row per record in `classification.csv` and `replay.csv` | `validate_outputs`, `stage_page_problems` |
| Every `replay.csv` row equals what the organisers' published algorithm recomputes from `policies.csv` | `stage_page_problems` |
| No flow from a blocked source escaped an active policy | `validate_outputs` |
| No future record influenced an earlier decision | `check_prefix_invariance` |
| No label was used | `replace(partition, labels=None)` before scoring. `test_labels_in_the_input_do_not_change_anything` holds it to that |

All are in `backend/services/level2.py`. The organisers' own validator is the final word.

`agent4_agent5_evidence.json` is written beside the CSVs. It holds groups, policies, the activation and expiry log, and final state, and is not a submission file.
