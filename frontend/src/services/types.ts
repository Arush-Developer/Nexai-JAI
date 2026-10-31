// TypeScript mirror of backend/schemas/contracts.py. tests/api/test_contract_sync.py compares the two.
export const CLASSES = ["BENIGN", "BRUTE_FORCE", "DOS", "DDOS", "PORT_SCAN"] as const;
export type Decision = "ALLOW" | "BLOCK" | "REVIEW";

export interface ThreatAssessment {
  agent: string;
  flow_id: string;
  predicted_class: string;
  confidence: number;
  probabilities: Record<string, number>;
  model_version: string;
  calibration_status: string;
}

export interface SupportingFeature {
  feature: string;
  deviation: string;
  measured_value: number;
  benign_median: number;
  benign_mad: number;
  robust_z: number;
}

export interface AnomalyAssessment {
  agent: string;
  flow_id: string;
  anomaly_score: number;
  score_meaning: string;
  is_anomaly: boolean;
  threshold: number;
  supporting_features: SupportingFeature[];
  baseline_reference: string;
  model_version: string;
}

export interface CampaignRelationship {
  source: string | null;
  destination: string | null;
  destination_port: number | null;
  flows: number;
}

export interface CampaignRecord {
  campaign_id: string;
  campaign_type: string;
  hypothesis: string;
  detection_mode: string;
  target: string;
  first_observed: string;
  last_observed: string;
  related_flow_ids: string[];
  related_flow_ids_total?: number | null;
  flow_count: number;
  distinct_sources: number | null;
  target_count: number | null;
  relationships: CampaignRelationship[];
  traffic_intensity: string;
  supporting_evidence: Record<string, number | string>[];
  contradicting_evidence: string[];
  confidence_basis: string;
  confidence: string;
  uncertainty: string;
  windows_observed: number;
}

export interface ToolResult {
  available: boolean;
  summary: string;
  supports: string;
  evidence: Record<string, unknown>;
}

export interface ToolCall {
  tool: string;
  arguments: Record<string, unknown>;
  status: string;
  duration_ms: number;
  result: ToolResult;
}

export interface InvestigationResult {
  investigation_id: string;
  flow_id: string;
  triggers: string[];
  tools_selected: string[];
  tool_calls: ToolCall[];
  supporting_observations: string[];
  contradicting_observations: string[];
  neutral_observations: string[];
  unavailable_evidence: string[];
  status: string;
  verdict: string;
  planner: string;
}

export interface FusionAssessment {
  agent: string;
  flow_id: string;
  final_class: string;
  final_class_source: string;
  evidence_status: string;
  agreement: string;
  low_confidence: boolean;
  confidence_status: "CONFIDENT" | "LOW_CONFIDENCE";
  evidence_refs: string[];
  investigation_refs: string[];
  rationale: string;
}

export interface PolicyOutcome {
  decision: Decision;
  policy_rule: string;
  decision_reason: string;
  evidence_refs: string[];
  simulated: boolean;
}

export interface GraphNode { id: string; kind: string; label: string; data: Record<string, unknown> }
export interface GraphEdge { id: string; source: string; target: string; relation: string }
export interface EvidenceGraph { nodes: GraphNode[]; edges: GraphEdge[] }

export interface FlowDetail {
  flow_id: string;
  network: Record<string, string | number | null>;
  unavailable_metadata: string[];
  threat: ThreatAssessment;
  anomaly: AnomalyAssessment;
  fusion: FusionAssessment;
  policy: PolicyOutcome;
  campaign: CampaignRecord | null;
  triggers: string[];
  investigation: InvestigationResult | null;
  replay: FlowDefense | null;
  ground_truth_evaluation_only: string | null;
  evidence_graph: EvidenceGraph;
}

export interface FlowDefense {
  action: "ALLOW" | "BLOCK" | null;
  applied_policy_id: string | null;
  replay_decision: Decision;
  replay_evidence_status: string;
}

export interface CorrelationEvidence {
  window_basis: "seconds" | "records";
  window_records: number;
  window_seconds: number | null;
  flows_in_window: number;
  suspicious_flows: number;
  confident_attack_flows: number;
  attack_share: number;
  anomalous_flows: number;
  dominant_predicted_class: string | null;
  distinct_destinations: number;
  distinct_destination_ports: number;
  distinct_attacking_sources: number;
  pattern: string;
}

export interface CorrelationGroup {
  group_id: string;
  scope_type: "SOURCE_IP" | "DESTINATION_IP";
  scope_value: string;
  first_record_id: string;
  last_record_id: string;
  member_record_ids: string[];
  member_count: number;
  source_ips: string[];
  destination_ips: string[];
  destination_ports: number[];
  first_observed: string;
  last_observed: string;
  supporting_evidence: Record<string, unknown>[];
  correlation_reason: string;
  evidence: CorrelationEvidence;
}

export interface DefensePolicy {
  policy_id: string;
  activated_after_record_id: string;
  scope_type: "SOURCE_IP";
  scope_value: string;
  expires_after_record_id: string | null;
  policy_action: "BLOCK";
  status: "ACTIVE" | "EXPIRED";
  ttl_records: number;
  blocked_records: number;
  group_id: string;
  converging_group_id: string | null;
  rationale: string;
  evidence: CorrelationEvidence;
}

export interface DefenseLogEntry {
  event: "ACTIVATED" | "EXPIRED";
  policy_id: string;
  record_id: string;
  position: number;
  scope_value: string;
}

export interface DefenseState {
  available: boolean;
  unavailable_reason: string | null;
  config: Record<string, number>;
  policies: DefensePolicy[];
  log: DefenseLogEntry[];
  groups: CorrelationGroup[];
  groups_total: number;
}

export interface PolicyEvent {
  seq: number;
  action: string;
  policy: Pick<DefensePolicy, "policy_id" | "scope_type" | "scope_value" | "activated_after_record_id" | "ttl_records" | "group_id" | "rationale">;
}

export interface FlowRow {
  flow_id: string;
  threat_class: string;
  threat_confidence: number;
  anomaly_score: number;
  is_anomaly: boolean;
  agreement: string;
  final_class: string;
  evidence_status: string;
  policy_decision: Decision;
  policy_rule: string;
  campaign_id: string | null;
}

export interface ReplayStatus {
  status: string;
  cursor: number;
  total: number;
  progress: number;
  speed: number;
  batch_size: number;
  counts: {
    total: number;
    classes: Record<string, number>;
    decisions: Record<Decision, number>;
    agreement: Record<string, number>;
    evidence: Record<string, number>;
    defense: Record<"ALLOW" | "BLOCK", number>;
  };
  campaigns: number;
  campaign_mode: string;
  investigations: number;
  investigation_triggers: number;
  inference_flows_per_second: number | null;
  latest_seq: number;
  defense_available: boolean;
  defense_stopped: boolean;
  active_policies: number;
  policies_activated: number;
  correlation_groups: number;
}

export interface ModelStatus {
  threat_model: string;
  threat_calibration: string;
  threat_features: number;
  anomaly_model: string;
  anomaly_detector: string;
  anomaly_threshold: number;
  anomaly_features: number;
  campaign_mode: string;
  campaign_limitations: string[];
  defense_available: boolean;
  replay_input: string;
  llm_in_use: boolean;
}

export interface Incident {
  incident_id: string;
  kind: string;
  severity: string;
  title: string;
  summary: string;
  flow_count: number;
  flow_ids: string[];
  campaign_id: string | null;
}

export interface Thresholds {
  allow_benign_confidence: number;
  block_multi_signal_confidence: number;
  block_classifier_only_confidence: number;
}

export interface PolicyState {
  thresholds: Thresholds;
  calibrated: Thresholds;
  history: { time: number; previous: Thresholds; new: Thresholds; reason: string; decisions_before: Record<string, number>; decisions_after: Record<string, number> }[];
}

export interface Simulation {
  flows_evaluated: number;
  previous_thresholds: Thresholds;
  new_thresholds: Thresholds;
  decisions_before: Record<Decision, number>;
  decisions_after: Record<Decision, number>;
  changed: number;
  transitions: Record<string, number>;
  examples: { flow_id: string; before: string; after: string; reason: string; final_class: string; confidence: number; anomaly_score: number; evidence_status: string }[];
}

export interface ReplayFlowEvent {
  flow_id: string;
  threat_class: string;
  confidence: number;
  anomaly_score: number;
  is_anomaly: boolean;
  agreement: string;
  final_class: string;
  evidence_status: string;
  policy_decision: Decision;
  policy_rule: string;
  campaign_id: string | null;
  investigation_triggered: boolean;
  defense_action: "ALLOW" | "BLOCK" | null;
  applied_policy_id: string | null;
  correlation_group_id: string | null;
  decision_reason: string | null;
  ground_truth_evaluation_only: string | null;
}
export interface DecisionRow {
  position: number;
  flow_id: string;
  source_ip: string | null;
  destination_ip: string | null;
  destination_port: number | null;
  protocol: string | number | null;
  timestamp: string | null;
  threat_class: string;
  threat_confidence: number;
  anomaly_score: number;
  is_anomaly: boolean;
  agreement: string;
  final_class: string;
  evidence_status: string;
  correlation_group_id: string | null;
  defense_action: "ALLOW" | "BLOCK" | null;
  applied_policy_id: string | null;
  decision_reason: string | null;
  policy_decision: Decision;
}
export interface DecisionPage {
  total: number;
  offset: number;
  limit: number;
  counts: { all: number; blocked: number; attack_allowed: number; conflict: number; grouped: number };
  items: DecisionRow[];
}
export interface PolicyImpact {
  policy_id: string;
  status: "ACTIVE" | "EXPIRED";
  scope_type: string;
  scope_value: string;
  policy_action: string;
  group_id: string;
  activated_after_record_id: string;
  expires_after_record_id: string | null;
  ttl_records: number;
  rationale: string;
  blocked_records: number;
  activated_position: number | null;
  first_blocked_record: string | null;
  last_blocked_record: string | null;
  blocked_positions: number[];
  by_class: Record<string, number>;
  destinations: Record<string, number>;
  phases: { day: string | null; cls: string; flows: number; ports: number }[];
}
export interface CampaignEvent { seq: number; action: string; campaign: CampaignRecord }
export interface InvestigationEvent { seq: number; flow_id: string; investigation_id: string; triggers: string[]; verdict: string; tools: string[] }

// The Data Manager. Reported by backend/services/local_data.py.
export interface LocalStatus {
  ready: boolean;
  error: string | null;
  agents: Record<string, string>;
  replay_schema_confirmed: boolean;
  expiry_boundary_confirmed: boolean;
  expiry_boundary: string;
  window_seconds: number | null;
  window_records: number;
}

export interface LocalUploadedFile {
  file_id: string;
  name: string;
  relative_path: string;
  size: number;
  sha256: string;
  kind: string;
  skipped_members: string[];
  previous_uploads: { run_id: string; dataset_type: string }[];
}

export interface LocalUpload {
  run_id: string;
  created: string;
  dataset_type: string;
  files: LocalUploadedFile[];
  data_files: { id: string; display: string; size: number }[];
  duplicate_of: string[];
}

export interface LocalInspectionFile {
  id: string;
  display: string;
  size: number;
  included: boolean;
  role: "flows" | "ordering" | "unknown";
  records: number;
  columns: number;
  errors: string[];
  warnings: string[];
  column_names?: string[];
  dtypes?: Record<string, string>;
  missing_values?: number;
  positive_infinity?: number;
  negative_infinity?: number;
  duplicate_records?: number;
  duplicate_columns?: string[][];
  label_column?: string | null;
  record_id_column?: string | null;
  timestamp_column?: string | null;
  source_ip_available?: boolean;
  destination_ip_available?: boolean;
  source_port_available?: boolean;
  destination_port_available?: boolean;
  protocol_available?: boolean;
  feature_count?: number;
  missing_required_features?: string[];
  unrecognised_columns?: string[];
  invalid_values?: Record<string, number>;
  timestamp_order?: { available: boolean; unreadable: number | null; backward_steps: number | null; first: string | null; last: string | null } | null;
}

export interface LocalJob {
  selected: string[];
  records: number;
  feature_count: number;
  models_compatible: boolean;
  errors: string[];
  warnings: string[];
  order_conflict: boolean;
  mode: "full" | "classification_only" | null;
  a4_a5_available: boolean;
  a4_a5_missing: string[];
  temporal: { basis: "seconds" | "records"; detail: string } | null;
  runnable: boolean;
}

export interface LocalInspection {
  run_id: string;
  dataset_type: string;
  files: LocalInspectionFile[];
  uploads: LocalUploadedFile[];
  job: LocalJob;
}

export interface LocalAgentState {
  state: string;
  detail: string;
}

export interface LocalPolicyEvent {
  seq: number;
  event: string;
  policy_id: string;
  record_id: string;
  scope_value: string;
  ttl_records?: number;
  blocked_records?: number;
  expires_after_record_id?: string;
}

export interface LocalValidation {
  rules_passed: boolean;
  problems: string[];
  problem_count: number;
  rules_checked: string[];
  labels_read: boolean;
  submission_ready: boolean;
  blockers: string[];
  records_expected: number;
  validated_at: string;
}

export interface LocalRun {
  run_id: string;
  state: string;
  stage: string;
  mode: string | null;
  processed: number;
  total: number;
  agents: Record<string, LocalAgentState>;
  error: string | null;
  outputs: { name: string; size: number }[];
  preview: Record<string, { columns: string[]; rows: string[][] }>;
  summary: {
    records: number; seconds: number; policies?: number; groups?: number; actions?: Record<string, number>;
    tradeoff?: { basis: string; by_class: Record<string, { allow: number; block: number }>; called_attack: number; called_attack_blocked: number; called_attack_allowed: number; called_benign: number; called_benign_blocked: number };
    policy_options?: { ttl_records: number; submitted: boolean; policies: number; called_attack_blocked: number; called_attack_allowed: number; called_benign_blocked: number }[];
  } | null;
  validation: LocalValidation | null;
  submission: { name: string; exists: boolean; rows: number | null; size: number | null; columns: string[] }[];
}
