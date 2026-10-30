# Day 2 architecture

CyberSentinel X, JAI 2026. Five agents over an offline replay. Nothing touches a real network: every ALLOW and BLOCK is a record in a file.

## Two logically separate steps

```
 hidden flows ──► check_replay_order ──► score_records ──────────────────► classification.csv
                  (refuse or report       A1 threat, A2 anomaly, A3 fusion    every record, once,
                   a conflict)            one record's features only          including later BLOCKs
                                                │
                                                ▼
                                          replay_scored, one record at a time, in the supplied order
                                          ┌──────────────────────────────────────────────────┐
                                          │ CausalReplayEngine.step                          │
                                          │  1. A5: does an active policy match the source?  │
                                          │     yes ─► BLOCK. Stop here. No new evidence.    │
                                          │  2. no ─► ALLOW                                  │
                                          │  3. A4 correlates it with earlier records        │──► replay.csv
                                          │  4. A5 may activate a policy for LATER records   │──► policy.csv
                                          │  5. expire policies at their boundary            │
                                          └──────────────────────────────────────────────────┘
```

Classification never reads replay state, and replay never changes classification. That is why a blocked record still has a prediction.

## Agents and files

| Agent | Owner | Source | Reads | Produces |
|---|---|---|---|---|
| A1 Threat Detection | Arnav | `backend/agents/threat_agent.py` | One flow's features | Class, confidence, probabilities |
| A2 Anomaly Detection | Arpit | `backend/agents/anomaly_agent.py`, `backend/intelligence/feature_explainer.py` | One flow's features. Never A1 or labels | Benign percentile score, flag, deviating features |
| A3 Evidence Fusion | Lucky | `backend/agents/fusion_agent.py` | A1 and A2 output | `final_class`, `evidence_status`, rationale |
| A4 Temporal Correlation | Arush | `backend/agents/temporal_agent.py` | Current record, A1 to A3 output, earlier analysed records | Groups, member ids, supporting evidence, reason |
| A5 Adaptive Defence | Agrim | `backend/agents/defense_agent.py` | Current record, A3 status, A4 evidence | Policies, activation and expiry log, STOP |
| Replay integration | Arush | `backend/replay/causal_replay.py`, `backend/services/level2.py`, `backend/replay/replay_engine.py` | All of the above | Actions, state, the three CSVs, the live API |

## Why one engine

`CausalReplayEngine` holds the order of operations in one place. The CSV export (`services/level2.py`) and the live website replay (`replay/replay_engine.py`) both call it, and `test_live_replay_makes_the_same_decisions_as_the_export` holds them to the same result.

## Other pieces

- **Order check:** `check_replay_order` in `backend/preprocessing/data_processor.py`.
- **Rule checker:** `validate_outputs` and `validate_submission` in `backend/services/level2.py`.
- **Causality proof:** `check_prefix_invariance` in the same file, and `TemporalCorrelationAgent.assert_causal`.
- **Scripts:** `run_level2.py` (the real run), `validate_level2.py` (check existing files), `demo_day2.py` (the demonstration), `rehearse_level2.py` (eval flows with invented endpoints), `tune_level2.py` (threshold comparison).
- **Config:** the `level2` block of `configs/default.yaml`.
- **Contracts:** `backend/schemas/contracts.py` and `frontend/src/services/types.ts`, which `tests/api/test_contract_sync.py` keeps matched.

## What is unchanged from Day 1

The A1 and A2 models are the saved Day 1 artifacts, not retrained or replaced. Re-verified on the official eval partition for Day 2: macro-F1 0.99982, with A3 status counts matching the README. One record scored alone gives exactly the same result as in a batch.

## Limits

The participant pack has no endpoints, so A4 and A5 have only ever run on real flows with **invented addresses**. See [DAY2_TEST_REPORT.md](DAY2_TEST_REPORT.md).
