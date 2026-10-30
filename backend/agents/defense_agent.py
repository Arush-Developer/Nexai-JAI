
from __future__ import annotations

from dataclasses import asdict, dataclass

from backend.agents.fusion_agent import MULTI_SIGNAL_ATTACK, UNCERTAIN
from backend.agents.temporal_agent import TemporalCorrelationAgent

ALLOW, BLOCK = "ALLOW", "BLOCK"
MISSING_ADDRESSES = {"", "nan", "none", "nat", "<na>"}
INCLUSIVE, EXCLUSIVE = "inclusive", "exclusive"


@dataclass
class DefenseConfig:
    min_confidence: float = 0.90  # Agent 1 confidence for a flow to count towards, or trigger, a policy.
    min_attack_flows_multi_signal: int = 3  # When the triggering flow also has Agent 2 support.
    min_attack_flows_classifier_only: int = 5  # Agent 2 barely sees brute force or port scans.
    min_attack_flows_converging: int = 2  # Source joins a many-to-one pattern already seen.
    min_attack_share: float = 0.60  # Protects shared sources that mostly send benign traffic.
    ttl_records: int = 2000
    max_ttl_records: int = 16000
    # Is the record named by expires_after_record_id still blocked? Unconfirmed by the organisers.
    expiry_boundary: str = INCLUSIVE

    def __post_init__(self) -> None:
        if self.expiry_boundary not in (INCLUSIVE, EXCLUSIVE):
            raise ValueError(f"expiry_boundary must be {INCLUSIVE!r} or {EXCLUSIVE!r}, not {self.expiry_boundary!r}")
        if self.ttl_records < (1 if self.expiry_boundary == INCLUSIVE else 2):
            raise ValueError(f"ttl_records {self.ttl_records} would never block a record")

    def to_dict(self) -> dict:
        return asdict(self)


class AdaptiveDefenseAgent:
    def __init__(self, config: DefenseConfig | None = None, total_records: int | None = None):
        self.config = config or DefenseConfig()
        self.total_records = total_records
        self.policies: list[dict] = []
        self.log: list[dict] = []
        self.stopped = False
        self._active: dict[str, dict] = {}
        self._activations: dict[str, int] = {}

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def active_policies(self) -> list[dict]:
        return list(self._active.values())

    @property
    def expired_policies(self) -> list[dict]:
        return [p for p in self.policies if p["status"] == "EXPIRED"]

    def stop(self, position: int, record_id: str | None, reason: str = "") -> None:
        """No new policies from here on. Active ones keep their recorded expiry, so replay stays deterministic."""
        if not self.stopped:
            self.stopped = True
            self.log.append({"event": "STOPPED", "policy_id": "-", "record_id": record_id, "position": position,
                             "scope_value": reason or "no reason given"})

    def begin_record(self, position: int, record_id: str) -> None:
        """Before step 1. Name the expiry record once replay reaches it, so no future record is ever read."""
        for policy in self.active_policies:
            if policy["_last_position"] == position:
                policy["expires_after_record_id"] = record_id
            if self.config.expiry_boundary == EXCLUSIVE and policy["_last_position"] <= position:
                self._expire(policy, record_id, position)

    def apply_active(self, position: int, record_id: str, source: str) -> dict | None:
        """Step 1. The policy that blocks this record, if any. Lapsed policies are already expired."""
        policy = self._active.get(source)
        if policy is not None:
            policy["blocked_records"] += 1
        return policy

    def advance(self, position: int, record_id: str) -> None:
        """Expire every policy whose last active record is this one, whichever source it belongs to."""
        if self.config.expiry_boundary == INCLUSIVE:
            for policy in [p for p in self._active.values() if p["_last_position"] <= position]:
                self._expire(policy, record_id, position)

    def _expire(self, policy: dict, record_id: str, position: int) -> None:
        if policy["expires_after_record_id"] is None:
            policy["expires_after_record_id"] = record_id
        policy["status"] = "EXPIRED"
        self._active.pop(policy["scope_value"], None)
        self.log.append({"event": "EXPIRED", "policy_id": policy["policy_id"], "record_id": record_id,
                         "position": position, "scope_value": policy["scope_value"],
                         "expires_after_record_id": policy["expires_after_record_id"],
                         "blocked_records": policy["blocked_records"]})

    def consider(self, position: int, record_id: str, source: str, destination: str, predicted_class: str,
                 confidence: float, anomaly_score: float, evidence_status: str,
                 agent4: TemporalCorrelationAgent, group: dict | None) -> dict | None:
        """Step 2. Activate a policy for later records if the evidence so far justifies one."""
        cfg = self.config
        if (self.stopped or predicted_class == "BENIGN" or evidence_status == UNCERTAIN
                or confidence < cfg.min_confidence or source in self._active):
            return None
        evidence = agent4.source_evidence(source)
        converging = agent4.converging_group(destination)
        group = group or converging
        if group is None:
            return None
        multi_signal = evidence_status == MULTI_SIGNAL_ATTACK
        required = (cfg.min_attack_flows_converging if converging
                    else cfg.min_attack_flows_multi_signal if multi_signal
                    else cfg.min_attack_flows_classifier_only)
        if evidence["confident_attack_flows"] < required or evidence["attack_share"] < cfg.min_attack_share:
            return None
        # expires_after_record_id must name a later record, and the file's length is not evidence.
        needs_later = 1 if cfg.expiry_boundary == INCLUSIVE else 2
        if self.total_records is not None and position + needs_later > self.total_records - 1:
            self.log.append({"event": "SKIPPED_NO_LATER_RECORD", "policy_id": "-", "record_id": record_id,
                             "position": position, "scope_value": source})
            return None
        repeats = self._activations.get(source, 0)
        ttl = min(cfg.ttl_records * 2 ** repeats, cfg.max_ttl_records)
        self._activations[source] = repeats + 1
        last_position = position + ttl
        if self.total_records is not None:
            last_position = min(last_position, self.total_records - 1)
        policy = {
            "policy_id": f"POL-{len(self.policies) + 1:05d}",
            "activated_after_record_id": record_id,
            "scope_type": "SOURCE_IP",
            "scope_value": source,
            "expires_after_record_id": None,
            "policy_action": BLOCK,
            "status": "ACTIVE",
            "ttl_records": ttl,
            "blocked_records": 0,
            "group_id": group["group_id"],
            "converging_group_id": converging["group_id"] if converging else None,
            "rationale": self._rationale(predicted_class, confidence, anomaly_score, evidence_status, evidence,
                                         converging, ttl, repeats),
            "evidence": evidence,
            "_activated_position": position,
            "_last_position": last_position,
        }
        self.policies.append(policy)
        self._active[source] = policy
        self.log.append({"event": "ACTIVATED", "policy_id": policy["policy_id"], "record_id": record_id,
                         "position": position, "scope_type": "SOURCE_IP", "scope_value": source,
                         "policy_action": BLOCK, "ttl_records": ttl, "group_id": group["group_id"]})
        return policy

    @staticmethod
    def _rationale(predicted_class, confidence, anomaly_score, status, evidence, converging, ttl, repeats) -> str:
        parts = [
            f"Triggering flow: Agent 1 {predicted_class} at {confidence:.3f}, Agent 2 score {anomaly_score:.3f}, "
            f"evidence status {status}.",
            f"Agent 4 saw {evidence['confident_attack_flows']} confident attack flows among "
            f"{evidence['flows_in_window']} analysed from this source in the last {evidence['window_records']} "
            f"records ({evidence['attack_share']:.0%}); pattern: {evidence['pattern']}.",
        ]
        if converging:
            parts.append(f"The destination is also in converging group {converging['group_id']}.")
        if repeats:
            parts.append(f"Policy number {repeats + 1} for this source, so it lasts longer.")
        parts.append(f"Blocks later flows from this source for {ttl} records.")
        return " ".join(parts)

    def finish(self, last_position: int, last_record_id: str) -> None:
        """End of replay: anything still active stops applying at the last record."""
        for policy in self.active_policies:
            if policy["_activated_position"] >= last_position:
                # Activated on the final record: nothing later exists, so there is no valid expiry to publish.
                self.policies.remove(policy)
                self._active.pop(policy["scope_value"], None)
                self.log.append({"event": "DISCARDED_NO_LATER_RECORD", "policy_id": policy["policy_id"],
                                 "record_id": last_record_id, "position": last_position,
                                 "scope_value": policy["scope_value"]})
            else:
                self._expire(policy, last_record_id, last_position)

    def public_policies(self) -> list[dict]:
        return [{k: v for k, v in p.items() if not k.startswith("_")} for p in self.policies]
