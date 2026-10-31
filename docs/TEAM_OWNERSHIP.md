# Team ownership

This records who is responsible for each module, not who wrote it. The first version of this code was generated with Claude Code from the team brief. A person owns a module once they have reviewed it, can explain it, and have contributed to it. Use real commits under your own name for that work; do not attribute generated code to anyone.

`docs/TEAM_HANDOFF.md` has what each person must review. `docs/MODULE_CONTRACTS.md` defines the interfaces below.

## Day 2 agent ownership

| Agent | Owner |
|---|---|
| A1 Threat Detection | Arnav |
| A2 Anomaly Detection | Arpit |
| A3 Evidence Fusion | Lucky |
| A4 Temporal Correlation, and chronological replay integration | Arush |
| A5 Adaptive Defence, and policy activation and expiry | Agrim |

Modules outside the five agents stay with whoever held them on Day 1. `docs/TEAM_VIVA_GUIDE.md` has what each person must be able to defend.

## Arush: A4, replay integration and backend architecture

| Files | Responsibility |
|---|---|
| `backend/agents/temporal_agent.py` | Agent 4 |
| `backend/replay/causal_replay.py`, `backend/replay/replay_engine.py` | Chronological state machine; live replay |
| `backend/services/level2.py`, `scripts/run_level2.py`, `scripts/validate_level2.py`, `scripts/rehearse_level2.py`, `scripts/demo_day2.py` | Level 2 CSVs, rule checker, rehearsal, demonstration |
| `backend/main.py`, `backend/config.py` | Entry point, configuration |
| `backend/api/routes.py`, `backend/api/submission_adapter.py` | HTTP and SSE API; Level 1 submission |
| `backend/services/model_service.py`, `backend/services/app_state.py` | Runs the agents; state behind the API |
| `backend/agents/investigation_orchestrator.py`, `backend/intelligence/evidence_engine.py`, `backend/investigation/audit.py` | Investigation planning, evidence graph, audit log |
| `backend/policy/policy_engine.py` | Simulated decisions, what-if |
| `backend/services/local_data.py`, `backend/api/local_data.py`, `scripts/run_local.sh`, `scripts/make_local_fixture.py`, `tests/api/test_local_data.py` | Data Manager backend, local startup, synthetic fixture |
| `backend/schemas/contracts.py` | Shared contracts, coordinated with everyone |
| `scripts/calibrate_fusion.py`, `scripts/score_records.py`, `scripts/run_all.sh` | Thresholds, scoring, full rebuild |
| `tests/agents/test_temporal_correlation.py`, `tests/replay/`, `tests/policy/`, `tests/api/`, `tests/integration/`, `tests/test_imports.py` | Backend and end-to-end tests |

- **Inputs:** Agents 1 to 3 output, endpoints, tool results, HTTP requests.
- **Outputs:** `CorrelationGroup`, the three CSVs, `PolicyOutcome`, `InvestigationResult`, the REST and SSE API.
- **Test:** `.venv/bin/python -m pytest tests/agents/test_temporal_correlation.py tests/replay tests/policy tests/api tests/integration -v`

## Agrim: A5 (and frontend analytics, below)

| Files | Responsibility |
|---|---|
| `backend/agents/defense_agent.py` | Agent 5: policy activation, expiry, STOP |
| `scripts/tune_level2.py` | Threshold comparison |
| `tests/agents/test_temporal_defense.py` | Agent 5 behaviour |

- **Inputs:** the current record, Agent 3 status, Agent 4 evidence.
- **Outputs:** `DefensePolicy`, the activation and expiry log.
- **Test:** `.venv/bin/python -m pytest tests/agents/test_temporal_defense.py tests/replay/test_day2_causal_replay.py -v`

## Lucky: A3 (and frontend foundation, below)

| Files | Responsibility |
|---|---|
| `backend/agents/fusion_agent.py` | Agent 3 |
| `tests/agents/test_fusion.py` | Fusion tests |

- **Inputs:** Agents 1 and 2 output.
- **Outputs:** `FusionAssessment`.
- **Test:** `.venv/bin/python -m pytest tests/agents/test_fusion.py -v`

## Arnav: A1 and security intelligence

| Files | Responsibility |
|---|---|
| `backend/agents/threat_agent.py` | Agent 1 |
| `models/threat_model/` | Threat model artifact |
| `backend/intelligence/campaign_engine.py` | Cross-flow hypotheses |
| `backend/investigation/tools.py`, `backend/investigation/registry.py` | Investigation tools, bounded execution |
| `tests/agents/test_models_real_data.py`, threat tests in `tests/agents/test_agents.py`, `tests/intelligence/`, `tests/investigation/` | Classification, campaign and investigation tests |

- **Inputs:** feature frames, flow metadata. Never labels at prediction time.
- **Outputs:** `ThreatAssessment`, `CampaignRecord`, `ToolResult`.
- **Test:** `.venv/bin/python -m pytest tests/agents/test_models_real_data.py tests/intelligence tests/investigation -v`

## Arpit: A2 and data engineering

| Files | Responsibility |
|---|---|
| `backend/agents/anomaly_agent.py`, `backend/intelligence/feature_explainer.py` | Agent 2, median/MAD baseline, deviation evidence |
| `models/anomaly_model/` | Anomaly model artifact |
| `backend/preprocessing/data_processor.py` | Loading, cleaning, splits, leakage control, replay order check |
| `backend/evaluation/metrics.py` | Metrics |
| `scripts/profile_dataset.py`, `scripts/train_models.py`, `scripts/leakage_audit.py`, `scripts/evaluate.py` | Profiling, training, audit, evaluation |
| `tests/agents/test_preprocessing.py`, anomaly tests in `tests/agents/test_agents.py` | Data and anomaly tests |

- **Inputs:** `data/raw/cyber-pack.zip`, extracted parquet.
- **Outputs:** `Partition`, `FeaturePipeline`, `AnomalyAssessment`, `data/reports/`.
- **Test:** `.venv/bin/python -m pytest tests/agents/test_preprocessing.py tests/agents/test_agents.py -v`

## Lucky: frontend foundation (with A3, above)

| Files | Responsibility |
|---|---|
| `frontend/e2e/app.spec.ts`, `frontend/playwright.config.ts` | Browser tests, shared with Agrim |
| `frontend/src/app/`, `frontend/src/main.tsx` | App, routes |
| `frontend/src/components/` | `Layout`, `Sidebar`, `Navbar`, `AgentStatus`, shared `ui.tsx` |
| `frontend/src/command/` | The 3D Command Centre: `AgentScene` (robots and room), `useAgentFocus` (which agent is working), `AgentStage`, `HudPanels` |
| `frontend/src/pages/CommandCenter.tsx`, `frontend/src/pages/AgentDecisions.tsx` | Command Centre, decision log |
| `frontend/src/replay/` | `ReplayLab`, `ReplayControls`, `ReplayTimeline`, `LiveFlowFeed` |
| `frontend/src/styles/`, `frontend/tailwind.config.js`, `frontend/vite.config.ts` | Styling, build |
| `frontend/src/services/api.ts`, `frontend/src/services/replay.ts` | REST client, SSE stream |
| `frontend/src/data-manager/`, `frontend/src/services/localData.ts`, `frontend/e2e/data-manager.spec.ts` | Data Manager screen, folder access, browser tests |
| `frontend/src/services/types.ts` | Shared with Arush; mirrors `contracts.py` |

- **Inputs:** REST and SSE responses.
- **Outputs:** the app shell, replay screens, and the client every screen uses.
- **Test:** `npm --prefix frontend run build`, then run the checks in `docs/TEAM_HANDOFF.md`. No automated frontend tests exist yet.

## Agrim: frontend analytics and investigation (with A5, above)

| Files | Responsibility |
|---|---|
| `frontend/src/analytics/CorrelationGroups.tsx`, `frontend/src/audit/AdaptiveDefense.tsx` | Agent 4 and Agent 5 screens |
| `frontend/src/analytics/` | `ThreatAnalytics` (Attack Intelligence screen), `ModelMetrics`, `AnomalyAnalytics`, `ConfusionMatrix` |
| `frontend/src/investigations/` | `InvestigationWorkbench`, `HypothesisPanel`, `IncidentTimeline` |
| `frontend/src/evidence-graph/EvidenceGraph.tsx` | Evidence graph |
| `frontend/src/audit/` | `DefenceAudit`, `PolicyControls`, `DecisionHistory`, `AuditLogs` |

- **Inputs:** `/metrics`, `/campaigns`, `/ports`, `/flows`, `/incidents`, `/investigations`, `/policy`, `/audit`.
- **Outputs:** the analytics, investigation and audit screens.
- **Test:** `npm --prefix frontend run build`, then run the checks in `docs/TEAM_HANDOFF.md`.

## Shared

`backend/schemas/contracts.py` and `frontend/src/services/types.ts` must change together. `tests/api/test_contract_sync.py` fails if their fields drift apart.
