"""Agent 4: temporal correlation of suspicious flows, from observed endpoints and sliding windows.

Strictly causal: observe() is called once per analysed flow in replay order, and a group only ever
holds flows already seen. It reads Agents 1-3 output and endpoints, never labels or later records.
Windows are counted in replay records and, when the input has timestamps, in seconds as well.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field

from backend.agents.fusion_agent import UNCERTAIN

BENIGN = "BENIGN"
SOURCE, DESTINATION = "SOURCE_IP", "DESTINATION_IP"


class CausalityError(RuntimeError):
    """A record arrived out of order, or history holds a record later than the current one."""


@dataclass
class TemporalConfig:
    window_records: int = 2000
    window_seconds: float | None = None  # Applies as well as window_records when records carry timestamps.
    min_group_members: int = 2  # A single suspicious flow is not yet a correlation.
    scan_min_ports: int = 20
    converging_min_sources: int = 10
    max_member_ids: int = 5000
    max_supporting_evidence: int = 20
    on_timestamp_regression: str = "error"  # "report" keeps going on a monotonic clock and lists each case.


@dataclass
class _Observation:
    position: int
    record_id: str
    source: str
    destination: str
    port: float | None
    predicted_class: str
    is_anomaly: bool
    confident_attack: bool
    suspicious: bool = True
    confidence: float | None = None
    anomaly_score: float | None = None
    evidence_status: str | None = None
    source_port: float | None = None
    timestamp: float | None = None
    observed_at: str | None = None


@dataclass
class _Window:
    """Running totals over the observations of one endpoint still inside the window."""

    items: deque = field(default_factory=deque)
    attack_flows: int = 0
    anomalous: int = 0
    suspicious: int = 0
    classes: Counter = field(default_factory=Counter)
    sources: Counter = field(default_factory=Counter)
    destinations: Counter = field(default_factory=Counter)
    ports: Counter = field(default_factory=Counter)

    def add(self, o: _Observation) -> None:
        self.items.append(o)
        self._apply(o, 1)

    def evict(self, expired) -> None:
        while self.items and expired(self.items[0]):
            self._apply(self.items.popleft(), -1)

    def _apply(self, o: _Observation, sign: int) -> None:
        self.attack_flows += sign * o.confident_attack
        self.anomalous += sign * o.is_anomaly
        self.suspicious += sign * o.suspicious
        changes = []
        if o.confident_attack:
            changes += [(self.classes, o.predicted_class), (self.sources, o.source)]
        if o.suspicious:
            changes += [(self.destinations, o.destination), (self.ports, o.port)]
        for counter, key in changes:
            counter[key] += sign
            # A zero left behind would still count towards len(), and so towards "distinct sources".
            if counter[key] <= 0:
                del counter[key]

    @property
    def flows(self) -> int:
        return len(self.items)


class TemporalCorrelationAgent:
    def __init__(self, config: TemporalConfig | None = None, min_attack_confidence: float = 0.9):
        self.config = config or TemporalConfig()
        self.min_attack_confidence = min_attack_confidence
        self.groups: list[dict] = []
        self.timestamp_regressions: list[str] = []
        self.suspicious_observed = 0
        self.last_suspicious = False
        self._open: dict[tuple[str, str], dict] = {}
        self._windows: dict[tuple[str, str], _Window] = {}
        self._position = -1
        self._clock: float | None = None

    def observe(self, position: int, record_id: str, source: str, destination: str, port: float | None,
                predicted_class: str, confidence: float, is_anomaly: bool, evidence_status: str | None = None,
                anomaly_score: float | None = None, timestamp: float | None = None, observed_at: str | None = None,
                source_port: float | None = None) -> dict | None:
        """Record one analysed flow. Returns the source's current group, or None if it has none yet."""
        if position <= self._position:
            raise CausalityError(f"record {record_id} at position {position} arrived after position {self._position}")
        self._position = position
        attack = predicted_class != BENIGN
        o = _Observation(position, record_id, source, destination, port, predicted_class, is_anomaly,
                         attack and confidence >= self.min_attack_confidence,
                         attack or is_anomaly or evidence_status == UNCERTAIN, confidence, anomaly_score,
                         evidence_status, source_port, self._tick(record_id, timestamp), observed_at)
        self.last_suspicious = o.suspicious
        self.suspicious_observed += o.suspicious
        by_source = self._window(SOURCE, source, o)
        by_destination = self._window(DESTINATION, destination, o)
        by_source.add(o)
        by_destination.add(o)
        if not o.suspicious:
            return None
        group = self._touch(SOURCE, source, by_source, o)
        # A destination only becomes a group once several sources converge on it with attack calls.
        if len(by_destination.sources) >= self.config.converging_min_sources:
            self._touch(DESTINATION, destination, by_destination, o)
        return group

    def source_evidence(self, source: str) -> dict:
        return self._evidence(self._windows[(SOURCE, source)])

    def converging_group(self, destination: str) -> dict | None:
        """The destination's group, if it still has enough attacking sources inside the window."""
        window = self._windows.get((DESTINATION, destination))
        if window is None or len(window.sources) < self.config.converging_min_sources:
            return None
        return self._open.get((DESTINATION, destination))

    def assert_causal(self, current_position: int) -> None:
        """Raise if any window or group holds a record later than the one being processed."""
        for window in self._windows.values():
            for o in window.items:
                if o.position > current_position:
                    raise CausalityError(f"history holds record {o.record_id} at position {o.position}, "
                                         f"later than current position {current_position}")
        for group in self.groups:
            if group["_last_position"] > current_position:
                raise CausalityError(f"group {group['group_id']} reaches position {group['_last_position']}, "
                                     f"later than current position {current_position}")

    def _tick(self, record_id: str, timestamp: float | None) -> float | None:
        """Keep the clock monotonic, so a backward timestamp can never revive an expired observation."""
        if timestamp is None or timestamp != timestamp:
            return self._clock
        if self._clock is not None and timestamp < self._clock:
            if self.config.on_timestamp_regression != "report":
                raise CausalityError(f"record {record_id} has a timestamp earlier than the record before it")
            self.timestamp_regressions.append(record_id)
            return self._clock
        self._clock = timestamp
        return timestamp

    def _outside(self, position: int, timestamp: float | None, o: _Observation) -> bool:
        seconds = self.config.window_seconds
        return (o.position - position >= self.config.window_records
                or (seconds is not None and timestamp is not None and o.timestamp is not None
                    and o.timestamp - timestamp > seconds))

    def _window(self, scope: str, value: str, o: _Observation) -> _Window:
        window = self._windows.setdefault((scope, value), _Window())
        window.evict(lambda old: self._outside(old.position, old.timestamp, o))
        return window

    def _touch(self, scope: str, value: str, window: _Window, o: _Observation) -> dict | None:
        key = (scope, value)
        group = self._open.get(key)
        members = [o]
        # A gap longer than the window means this is a new episode, not a continuation.
        if group is None or self._outside(group["_last_position"], group["_last_time"], o):
            self._open.pop(key, None)
            if window.suspicious < self.config.min_group_members:
                return None
            group = {
                "group_id": f"GRP-{len(self.groups) + 1:05d}", "scope_type": scope, "scope_value": value,
                "first_record_id": None, "member_record_ids": [], "member_count": 0,
                "first_observed": None, "supporting_evidence": [],
                "_sources": set(), "_destinations": set(), "_ports": set(),
            }
            self.groups.append(group)
            self._open[key] = group
            # The flows that formed the group were already observed, so they are its first members.
            members = [item for item in window.items if item.suspicious]
        for member in members:
            self._add_member(group, member)
        group["evidence"] = self._evidence(window)
        return group

    def _add_member(self, group: dict, o: _Observation) -> None:
        if group["first_record_id"] is None:
            group["first_record_id"] = o.record_id
            group["first_observed"] = o.observed_at or o.record_id
        group["_last_position"], group["_last_time"] = o.position, o.timestamp
        group["last_record_id"] = o.record_id
        group["last_observed"] = o.observed_at or o.record_id
        group["member_count"] += 1
        if len(group["member_record_ids"]) < self.config.max_member_ids:
            group["member_record_ids"].append(o.record_id)
        group["_sources"].add(o.source)
        group["_destinations"].add(o.destination)
        if o.port is not None and o.port == o.port:
            group["_ports"].add(o.port)
        if len(group["supporting_evidence"]) < self.config.max_supporting_evidence:
            group["supporting_evidence"].append({
                "record_id": o.record_id, "source_ip": o.source, "destination_ip": o.destination,
                "source_port": o.source_port, "destination_port": o.port, "observed_at": o.observed_at,
                "agent1_class": o.predicted_class, "agent1_confidence": o.confidence,
                "agent2_score": o.anomaly_score, "agent2_flagged": o.is_anomaly, "agent3_status": o.evidence_status,
            })

    def _evidence(self, window: _Window) -> dict:
        dominant = window.classes.most_common(1)[0][0] if window.classes else None
        share = window.attack_flows / window.flows if window.flows else 0.0
        return {
            "window_records": self.config.window_records,
            "window_seconds": self.config.window_seconds,
            "flows_in_window": window.flows,
            "suspicious_flows": window.suspicious,
            "confident_attack_flows": window.attack_flows,
            "attack_share": round(share, 4),
            "anomalous_flows": window.anomalous,
            "dominant_predicted_class": dominant,
            "distinct_destinations": len(window.destinations),
            "distinct_destination_ports": len(window.ports),
            "distinct_attacking_sources": len(window.sources),
            "pattern": self._pattern(window, dominant),
        }

    def _pattern(self, window: _Window, dominant: str | None) -> str:
        if len(window.sources) >= self.config.converging_min_sources:
            return "many sources converging on one destination"
        if len(window.ports) >= self.config.scan_min_ports:
            return "one source touching many destination ports"
        if dominant is None:
            return "unusual flows with no attack call from the classifier"
        if len(window.ports) == 1:
            return "repeated flows to one service port"
        if len(window.destinations) == 1:
            return "repeated suspicious flows between one source and one destination"
        return "repeated suspicious flows from one source"

    @staticmethod
    def _reason(group: dict) -> str:
        e = group["evidence"]
        limit = f"{e['window_records']} records" + (f" or {e['window_seconds']:g} s" if e["window_seconds"] else "")
        called = f", mostly called {e['dominant_predicted_class']}" if e["dominant_predicted_class"] else ""
        return (f"{group['member_count']} suspicious flows on {group['scope_type']} {group['scope_value']}: "
                f"{e['pattern']}. Last window of {limit}: {e['suspicious_flows']} suspicious of {e['flows_in_window']} "
                f"analysed, {e['distinct_destinations']} destinations, {e['distinct_destination_ports']} destination "
                f"ports, {e['distinct_attacking_sources']} attacking sources{called}.")

    def public_group(self, group: dict, member_limit: int | None = None) -> dict:
        item = {k: v for k, v in group.items() if not k.startswith("_")}
        if member_limit is not None:
            item["member_record_ids"] = group["member_record_ids"][:member_limit]
        item["source_ips"] = sorted(group["_sources"])
        item["destination_ips"] = sorted(group["_destinations"])
        item["destination_ports"] = sorted(group["_ports"])
        item["correlation_reason"] = self._reason(group)
        return item

    def public_groups(self, member_limit: int | None = None) -> list[dict]:
        return [self.public_group(group, member_limit) for group in self.groups]
