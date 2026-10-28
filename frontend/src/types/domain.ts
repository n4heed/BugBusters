export type AlertSeverity = "critical" | "high" | "medium" | "low" | "unknown";

export type IncidentStatus = "pending_review" | "approved" | "rejected";

export type RecommendationStatus = "pending" | "approved" | "rejected" | "implemented";

export interface Alert {
  id: string;
  timestamp: string;
  service: string;
  signal: string;
  severity: AlertSeverity;
  host_redacted: string;
  ip_redacted: string;
  customer_redacted: string;
  incident_id: string | null;
  is_safety_queue: boolean;
  safety_reason: string | null;
}

export interface ServiceDependency {
  service: string;
  depends_on: string;
}

export interface IncidentSummary {
  id: string;
  title: string;
  severity: AlertSeverity;
  related_alert_count: number;
  origin_hypothesis: string;
  affected_service_count: number;
  status: IncidentStatus;
  last_activity: string;
  workflow_status: string;
  is_candidate: boolean;
}

export interface Evidence {
  narrative: string;
  overlapping_time_windows: string[];
  supporting_dependencies: string[];
  supporting_metrics: string[];
  missing_or_contradictory: string[];
  similar_historical_incidents: string[];
  unresolved_uncertainty: string[];
}

export interface SafetyDisclosure {
  confidence_label: "DEMO ESTIMATE — NOT CALIBRATED" | "NOT MEASURED";
  confidence_value: number | null;
  evidence_completeness: "complete" | "partial" | "insufficient";
  topology_bias_note: string | null;
  matches_confirmed_pattern: boolean;
  weak_support_warning: string | null;
  missing_evidence_warning: string | null;
}

export interface Incident {
  id: string;
  title: string;
  severity: AlertSeverity;
  origin_hypothesis: string;
  first_seen: string;
  last_seen: string;
  affected_services: string[];
  status: IncidentStatus;
  related_alerts: Alert[];
  dependencies: ServiceDependency[];
  evidence: Evidence;
  safety: SafetyDisclosure;
  workflow_status: string;
  is_candidate: boolean;
  ai_status: "AI_NOT_RUN" | "AI_DISABLED" | "AI_CALLED" | "AI_BUDGET_EXHAUSTED" | "AI_CALL_FAILED";
  ai_reason: string;
  ai_model_used: string | null;
  ai_alerts_considered: number;
  ai_historical_cases_considered: number;
}

export interface TuningRecommendation {
  rule_id: string;
  service: string;
  rule_name: string;
  count_24h: number;
  historical_links: number;
  suggested_action: string;
  status: RecommendationStatus;
}

export interface SeaMemoryEvent {
  id: string;
  timestamp: string;
  event_type:
    | "INCIDENT_PATTERN_APPROVED"
    | "TUNING_RECOMMENDATION_APPROVED"
    | "TUNING_RECOMMENDATION_REJECTED";
  entity_id: string;
  decision: string;
  pattern: string | null;
  status: "DEMO MEMORY ENTRY" | "CONFIRMED";
}

export interface EvaluationMetrics {
  raw_alerts_24h: number | null;
  correlated_incidents: number | null;
  notification_reduction_pct: number | null;
  incident_recall_pct: number | null;
  grouping_precision_pct: number | null;
  alert_pair_precision_pct?: number | null;
  alert_pair_recall_pct?: number | null;
  verification_failure_rate_pct?: number | null;
  avg_processing_latency_ms?: number | null;
  is_demo_data: boolean;
  evaluated_on?: "synthetic_fixture_ground_truth" | "not_evaluated";
}

export type PipelineWorkflowStatus =
  | "RECEIVED"
  | "TRIAGE_COMPLETE"
  | "INVESTIGATION_PENDING"
  | "INVESTIGATION_COMPLETE"
  | "VERIFICATION_PENDING"
  | "VERIFIED"
  | "PENDING_HUMAN_REVIEW"
  | "UNCLUSTERED"
  | "DUPLICATE"
  | "PROCESSING_ERROR";

export type AlertDisposition = "unique" | "redundant" | "uncertain";

export interface ProcessingResult {
  alert_id: string;
  workflow_status: PipelineWorkflowStatus;
  incident_id: string | null;
  is_synthetic: boolean;
  error: string | null;
  processing_ms: number | null;
  disposition?: AlertDisposition | null;
  duplicate_of?: string | null;
}

export interface ReplayResponse {
  results: ProcessingResult[];
  evaluation: EvaluationMetrics;
}

export interface VerificationResult {
  candidate_group_id: string;
  outcome: "verified" | "rejected" | "pending";
  reasons: string[];
  validated_alert_ids: string[];
  missing_evidence: string[];
}

export interface InvestigationResult {
  candidate_group_id: string;
  status: "insufficient_evidence" | "hypothesis_formed";
  observed_facts: string[];
  derived_relationships: string[];
  historical_similarities: string[];
  probable_cause_hypothesis: string | null;
  missing_or_contradictory: string[];
  unresolved_unknowns: string[];
  evidence_source: "deterministic_fallback" | "llm";
  model_used: string | null;
  ai_status: Incident["ai_status"];
  ai_reason: string;
  ai_alerts_considered: number;
  ai_historical_cases_considered: number;
}

export interface InvestigateIncidentResponse {
  incident: Incident;
  investigation: InvestigationResult;
  verification: VerificationResult;
}

export interface AIStatus {
  ai_enabled: boolean;
  provider: string | null;
  model: string | null;
  api_key_configured: boolean;
  max_calls_per_run: number;
  max_alerts_per_incident: number;
  max_historical_cases: number;
  max_output_tokens: number;
  max_retries: number;
  budget_used: number;
  budget_remaining: number;
}

export interface EvaluationBaselineResult {
  name: string;
  notification_count: number;
  precision_pct: number | null;
  recall_pct: number | null;
  true_positives: number;
  false_positives: number;
  false_negatives: number;
  ai_calls_made: number;
  verification_failures: number;
  total_latency_ms: number;
  notes: string;
}

export interface EvaluationFinding {
  case_ref: string;
  stage: string;
  category: string;
  observed: string;
  expected: string;
  alert_ids: string[];
}

export interface EvaluationReport {
  generated_at: string;
  mode_a_synthetic_smoke_tests: { provenance: string; total_checks: number; passed: number; all_passed: boolean };
  mode_b_challenge_dataset_benchmark: { provenance: string; reason?: string };
  mode_c_selective_ai_evaluation: { provenance: string; reason?: string };
  baselines: { provenance: string; dataset: string; results: EvaluationBaselineResult[] };
  error_analysis: { provenance: string; total_findings: number; findings: EvaluationFinding[] };
}

export interface AIUsageSummary {
  total_calls: number;
  successes: number;
  errors: number;
  total_input_tokens: number | null;
  total_output_tokens: number | null;
  tokens_are_estimated: boolean;
  avg_latency_ms: number | null;
  estimated_cost_usd: number | null;
}

/** Whether the backend process can be reached at all — NOT a claim that
 * any data behind it is real production telemetry. This prototype has no
 * production alert source; every alert it ever shows, live backend or not,
 * is synthetic (fixture-seeded or agent-pipeline-generated from the demo
 * simulation). See DataProvenance. */
export type BackendStatus = "UNKNOWN" | "REACHABLE" | "UNREACHABLE";

export type DataProvenance = "synthetic_demo";

export interface ApprovalResult {
  success: boolean;
  demo_only: boolean;
  message: string;
  memory_event: SeaMemoryEvent | null;
}

export type StreamStatus = "DISCONNECTED" | "CONNECTING" | "CONNECTED" | "CONNECTED_WAITING" | "ERROR";

export type DataSource = "live" | "demo";

// --- Dataset ingestion (Milestone 4) ---

export type DatasetProvenance = "synthetic_demo" | "uploaded" | "evaluation_reference";
export type DatasetLifecycleState =
  | "imported"
  | "mapped"
  | "validated"
  | "loaded"
  | "processing"
  | "processed"
  | "failed";
export type FileCategory = "alerts" | "metrics" | "logs" | "dependencies" | "historical_incidents" | "ground_truth" | "unknown";

export interface FieldMappingEntry {
  source_field: string;
  canonical_field: string;
  confidence: "suggested" | "confirmed";
}

export interface FileMapping {
  file_name: string;
  category: FileCategory;
  category_confidence: "suggested" | "confirmed";
  field_mappings: FieldMappingEntry[];
}

export interface ValidationIssue {
  file_name: string;
  row_index: number;
  reason: string;
  field: string | null;
}

export interface ValidationReport {
  total_records_examined: number;
  valid_records: number;
  invalid_records: number;
  missing_required_fields: number;
  invalid_timestamps: number;
  invalid_severity_values: number;
  duplicate_identifiers: number;
  unresolved_service_references: number;
  unrecognized_fields: string[];
  warnings: string[];
  issues: ValidationIssue[];
  issues_truncated: boolean;
}

export interface DatasetFileInfo {
  file_name: string;
  file_type: "csv" | "json" | "jsonl";
  size_bytes: number;
  record_count: number | null;
  detected_fields: string[];
  detected_timestamp_field: string | null;
  detected_identifier_field: string | null;
  sample_records: Record<string, unknown>[];
}

export interface DatasetSummary {
  id: string;
  name: string;
  provenance: DatasetProvenance;
  created_at: string;
  lifecycle_state: DatasetLifecycleState;
  files: DatasetFileInfo[];
  record_counts: Record<string, number>;
  validation_report: ValidationReport | null;
  is_active: boolean;
}

export interface DatasetSchemaResponse {
  files: Record<string, DatasetFileInfo>;
  suggested_mappings: Record<string, FileMapping>;
}

export interface ReplayRunSummary {
  run_id: string;
  dataset_id: string;
  alert_count: number;
  workflow_status_counts: Record<string, number>;
  status: string;
  duplicate_count: number;
  group_count: number;
  incident_count: number;
  unique_count: number;
  uncertain_count: number;
  grouped_alert_count: number;
  standalone_count: number;
}

export interface RunSummary {
  run_id: string;
  dataset_id: string;
  started_at: string;
  completed_at: string | null;
  status: string;
  eligible_alert_count: number;
  processed_count: number;
  workflow_status_counts: Record<string, number>;
  duplicate_count: number;
  group_count: number;
  incident_count: number;
  error_summary: string | null;
  unique_count: number;
  uncertain_count: number;
  grouped_alert_count: number;
  standalone_count: number;
}

export interface StartRunResponse {
  run_id: string;
  dataset_id: string;
  status: string;
  eligible_alert_count: number;
  started_at: string;
  already_running: boolean;
}

/** Phase 4: restart-safe live run statistics, recomputed from persisted
 * processing_results/processing_groups on every call — works before,
 * during, and after processing, and after a backend restart. */
export interface RunStatsSummary {
  dataset_id: string;
  run_id: string;
  status: string;
  eligible_alert_count: number;
  processed_count: number;
  unique_count: number;
  duplicate_count: number;
  uncertain_count: number;
  group_count: number;
  grouped_alert_count: number;
  unique_grouped_alert_count: number;
  standalone_count: number;
  incident_count: number;
  workflow_status_counts: Record<string, number>;
  started_at: string;
  completed_at: string | null;
  last_progress_at: string | null;
  error_summary: string | null;
}

export interface GroupMember {
  alert_id: string;
  service: string | null;
  severity: string | null;
  timestamp: string | null;
  workflow_status: string | null;
  disposition: AlertDisposition | null;
  duplicate_of: string | null;
}

export interface GroupDetail {
  group_id: string;
  dataset_id: string;
  run_id: string;
  size: number;
  unique_member_count: number;
  redundant_member_count: number;
  members: GroupMember[];
  incident: Record<string, unknown> | null;
}
