"""The chronological state machine behind Agents 4 and 5. The live replay and the Level 2 export share it.

The order at each record is fixed by the brief:
  1. apply policies already active from earlier records (BLOCK, and the flow is not analysed further);
  2. otherwise ALLOW, let Agent 4 correlate it, and let Agent 5 possibly activate a policy for LATER records.
A flow can therefore never be blocked by a policy its own evidence created, and a blocked flow can never
add evidence. Nothing here reads a label or a later record, and nothing touches a network.
"""
from __future__ import annotations

from backend.agents.defense_agent import ALLOW, BLOCK, MISSING_ADDRESSES, AdaptiveDefenseAgent, DefenseConfig
from backend.agents.temporal_agent import CausalityError, TemporalConfig, TemporalCorrelationAgent


class CausalReplayEngine:
    def __init__(self, temporal: TemporalConfig | None = None, defense: DefenseConfig | None = None,
                 total_records: int | None = None):
        self.defense = AdaptiveDefenseAgent(defense, total_records)
        self.temporal = TemporalCorrelationAgent(temporal, self.defense.config.min_confidence)
        self.position = -1
        self.record_id: str | None = None
        self.action_log: list[dict] = []

    def step(self, position: int, record_id: str, source: str, destination: str, port: float | None,
             predicted_class: str, confidence: float, anomaly_score: float, is_anomaly: bool,
             evidence_status: str, timestamp: float | None = None, observed_at: str | None = None,
             source_port: float | None = None) -> dict:
        if position <= self.position:
            raise CausalityError(f"record {record_id} at position {position} arrived after position {self.position}")
        self.position, self.record_id = position, record_id
        self.defense.begin_record(position, record_id)
        # A flow with no usable source cannot match a policy or be attributed. It must not become one big "source".
        known_source = source.strip().lower() not in MISSING_ADDRESSES
        blocking = self.defense.apply_active(position, record_id, source) if known_source else None
        activated, group, suspicious = None, None, False
        reason = (f"BLOCK. {source} is under {blocking['policy_id']}, active since {blocking['activated_after_record_id']}. "
                  "Its prediction is kept, but it adds no new evidence.") if blocking else "ALLOW. This flow has no usable source_ip, so it cannot match a policy."
        if blocking is None and known_source:
            group = self.temporal.observe(position, record_id, source, destination, port, predicted_class, confidence,
                                          is_anomaly, evidence_status, anomaly_score, timestamp, observed_at,
                                          source_port)
            suspicious = self.temporal.last_suspicious
            activated = self.defense.consider(position, record_id, source, destination, predicted_class, confidence,
                                              anomaly_score, evidence_status, self.temporal, group)
            reason = self.defense.last_reason
            if activated:
                self.temporal.assert_causal(position)
        applied = blocking["policy_id"] if blocking else ""
        self.defense.advance(position, record_id)
        action = BLOCK if blocking else ALLOW
        self.action_log.append({"position": position, "record_id": record_id, "source_ip": source, "action": action,
                                "applied_policy_id": applied})
        return {"action": action, "applied_policy_id": applied, "activated_policy": activated, "group": group,
                "suspicious": suspicious, "reason": reason}

    def stop(self, reason: str = "") -> None:
        """STOP: Agent 5 activates nothing further. Policies already active run to their recorded expiry."""
        self.defense.stop(self.position, self.record_id, reason)

    def finish(self, last_position: int, last_record_id: str) -> None:
        self.defense.finish(last_position, last_record_id)
        self.temporal.assert_causal(last_position)

    def state(self) -> dict:
        blocked = sum(a["action"] == BLOCK for a in self.action_log)
        return {
            "position": self.position,
            "record_id": self.record_id,
            "suspicious_flows_observed": self.temporal.suspicious_observed,
            "temporal_groups": len(self.temporal.groups),
            "active_policy_ids": [p["policy_id"] for p in self.defense.active_policies],
            "expired_policy_ids": [p["policy_id"] for p in self.defense.expired_policies],
            "policy_log_events": len(self.defense.log),
            "actions": {ALLOW: len(self.action_log) - blocked, BLOCK: blocked},
            "applied_policy_ids": sorted({a["applied_policy_id"] for a in self.action_log if a["applied_policy_id"]}),
            "stopped": self.defense.stopped,
            "timestamp_regressions": len(self.temporal.timestamp_regressions),
        }
