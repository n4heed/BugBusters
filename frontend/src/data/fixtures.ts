// Synthetic demo fixtures used only when the real backend is unreachable.
// These mirror backend/app/fixtures.py so the demo is internally consistent:
// raw alerts (420) = incident alerts (12+6+5=23) + safety alerts (1) + tuning alerts (396).
import type {
  Alert,
  Incident,
  IncidentSummary,
  SeaMemoryEvent,
  TuningRecommendation,
} from "../types/domain";

const NOW = new Date("2026-10-28T14:30:00Z");
const minutesAgo = (m: number) => new Date(NOW.getTime() - m * 60_000).toISOString();

function alert(
  id: string,
  minsAgo: number,
  service: string,
  signal: string,
  severity: Alert["severity"],
  incident_id: string | null,
  is_safety_queue = false,
  safety_reason: string | null = null,
): Alert {
  return {
    id,
    timestamp: minutesAgo(minsAgo),
    service,
    signal,
    severity,
    host_redacted: "[REDACTED_HOST]",
    ip_redacted: "[REDACTED_IP]",
    customer_redacted: "[REDACTED]",
    incident_id,
    is_safety_queue,
    safety_reason,
  };
}

export const demoIncidents: Incident[] = [
  {
    id: "INC-2026-001",
    title: "Cascading Failure via primary-postgres-db",
    severity: "critical",
    origin_hypothesis: "primary-postgres-db",
    first_seen: minutesAgo(30),
    last_seen: minutesAgo(19),
    affected_services: ["primary-postgres-db", "core-app-svc-01", "api-gateway", "status-page", "async-worker-01"],
    status: "pending_review",
    related_alerts: [
      alert("ALT-A-01", 30, "primary-postgres-db", "Disk I/O > 94%", "critical", "INC-2026-001"),
      alert("ALT-A-02", 29, "primary-postgres-db", "Connection pool exhausted", "critical", "INC-2026-001"),
      alert("ALT-A-03", 28, "core-app-svc-01", "CPU > 92%, retry activity", "high", "INC-2026-001"),
      alert("ALT-A-04", 27, "core-app-svc-01", "GC pause spike", "high", "INC-2026-001"),
      alert("ALT-A-05", 26, "api-gateway", "HTTP 502/504 rate > 18.5%", "critical", "INC-2026-001"),
      alert("ALT-A-06", 25, "api-gateway", "Upstream timeout spike", "high", "INC-2026-001"),
      alert("ALT-A-07", 24, "status-page", "Degraded status propagated", "medium", "INC-2026-001"),
      alert("ALT-A-08", 23, "async-worker-01", "Queue backlog growing", "high", "INC-2026-001"),
      alert("ALT-A-09", 22, "async-worker-01", "Job retry storm", "high", "INC-2026-001"),
      alert("ALT-A-10", 21, "primary-postgres-db", "Write latency > 2s", "critical", "INC-2026-001"),
      alert("ALT-A-11", 20, "core-app-svc-01", "5xx rate climbing", "high", "INC-2026-001"),
      alert("ALT-A-12", 19, "api-gateway", "Circuit breaker opened", "critical", "INC-2026-001"),
    ],
    dependencies: [
      { service: "status-page", depends_on: "api-gateway" },
      { service: "api-gateway", depends_on: "core-app-svc-01" },
      { service: "core-app-svc-01", depends_on: "primary-postgres-db" },
      { service: "async-worker-01", depends_on: "core-app-svc-01" },
    ],
    evidence: {
      narrative:
        "The proposed grouping contains 12 alerts across the affected services. Shared dependency paths and latency changes within the relevant investigation window support a possible failure originating at primary-postgres-db.\n\nThis is a probable-cause hypothesis, not proof of causation. Review the linked metrics and logs before confirming it.",
      overlapping_time_windows: ["14:00:05Z - 14:11:00Z across all 5 affected services"],
      supporting_dependencies: [
        "core-app-svc-01 -> primary-postgres-db",
        "api-gateway -> core-app-svc-01",
        "async-worker-01 -> core-app-svc-01",
      ],
      supporting_metrics: ["Disk I/O > 94% on primary-postgres-db", "HTTP 502/504 rate > 18.5% on api-gateway"],
      missing_or_contradictory: ["No confirmed deploy or config-change event in this window"],
      similar_historical_incidents: [],
      unresolved_uncertainty: ["Whether the disk I/O spike is cause or symptom of an upstream query pattern"],
    },
    safety: {
      confidence_label: "DEMO ESTIMATE — NOT CALIBRATED",
      confidence_value: 0.74,
      evidence_completeness: "partial",
      topology_bias_note:
        "Grouping leans on known dependency topology; services without modeled edges would not surface here.",
      matches_confirmed_pattern: false,
      weak_support_warning: null,
      missing_evidence_warning: "No deploy/change event correlated yet; causal direction unconfirmed.",
    },
    workflow_status: "VERIFIED",
    is_candidate: false,
    ai_status: "AI_NOT_RUN",
    ai_reason: "Deterministic evidence was sufficient; AI-assisted analysis was not needed.",
    ai_model_used: null,
    ai_alerts_considered: 0,
    ai_historical_cases_considered: 0,
  },
  {
    id: "INC-2026-002",
    title: "Cascading Failure via redis-session-cluster",
    severity: "high",
    origin_hypothesis: "redis-session-cluster",
    first_seen: minutesAgo(40),
    last_seen: minutesAgo(35),
    affected_services: ["redis-session-cluster", "core-app-svc-02", "api-gateway"],
    status: "pending_review",
    related_alerts: [
      alert("ALT-B-01", 40, "redis-session-cluster", "Memory eviction rate spike", "high", "INC-2026-002"),
      alert("ALT-B-02", 39, "redis-session-cluster", "Replication lag > 5s", "high", "INC-2026-002"),
      alert("ALT-B-03", 38, "core-app-svc-02", "Session lookup latency up", "medium", "INC-2026-002"),
      alert("ALT-B-04", 37, "core-app-svc-02", "Auth timeout rate up", "high", "INC-2026-002"),
      alert("ALT-B-05", 36, "api-gateway", "Elevated 401 rate", "medium", "INC-2026-002"),
      alert("ALT-B-06", 35, "redis-session-cluster", "Node failover triggered", "high", "INC-2026-002"),
    ],
    dependencies: [
      { service: "api-gateway", depends_on: "core-app-svc-02" },
      { service: "core-app-svc-02", depends_on: "redis-session-cluster" },
    ],
    evidence: {
      narrative:
        "6 alerts cluster around redis-session-cluster within a 5-minute window. Session-lookup and auth-timeout symptoms on core-app-svc-02 follow the cluster's replication lag and failover event, consistent with a session-store origin.\n\nThis is a probable-cause hypothesis, not confirmed causation.",
      overlapping_time_windows: ["13:50:00Z - 13:55:00Z across 3 affected services"],
      supporting_dependencies: ["core-app-svc-02 -> redis-session-cluster", "api-gateway -> core-app-svc-02"],
      supporting_metrics: ["Memory eviction rate spike on redis-session-cluster", "Replication lag > 5s"],
      missing_or_contradictory: ["No corresponding spike in core-app-svc-02 CPU/memory"],
      similar_historical_incidents: [],
      unresolved_uncertainty: ["Whether failover was triggered by eviction pressure or a separate network blip"],
    },
    safety: {
      confidence_label: "DEMO ESTIMATE — NOT CALIBRATED",
      confidence_value: 0.61,
      evidence_completeness: "partial",
      topology_bias_note: null,
      matches_confirmed_pattern: false,
      weak_support_warning: "Moderate alert count; origin hypothesis is weakly supported relative to Incident A.",
      missing_evidence_warning: null,
    },
    workflow_status: "VERIFIED",
    is_candidate: false,
    ai_status: "AI_NOT_RUN",
    ai_reason: "Deterministic evidence was sufficient; AI-assisted analysis was not needed.",
    ai_model_used: null,
    ai_alerts_considered: 0,
    ai_historical_cases_considered: 0,
  },
  {
    id: "INC-2026-003",
    title: "Cascading Failure via payments-ledger-db",
    severity: "medium",
    origin_hypothesis: "payments-ledger-db",
    first_seen: minutesAgo(55),
    last_seen: minutesAgo(51),
    affected_services: ["payments-ledger-db", "core-app-svc-03"],
    status: "pending_review",
    related_alerts: [
      alert("ALT-C-01", 55, "payments-ledger-db", "Replica lag > 8s", "medium", "INC-2026-003"),
      alert("ALT-C-02", 54, "payments-ledger-db", "Slow query rate up", "medium", "INC-2026-003"),
      alert("ALT-C-03", 53, "core-app-svc-03", "Payment confirmation delay", "medium", "INC-2026-003"),
      alert("ALT-C-04", 52, "core-app-svc-03", "Downstream retry increase", "low", "INC-2026-003"),
      alert("ALT-C-05", 51, "payments-ledger-db", "Lock wait time elevated", "medium", "INC-2026-003"),
    ],
    dependencies: [{ service: "core-app-svc-03", depends_on: "payments-ledger-db" }],
    evidence: {
      narrative:
        "5 alerts link payments-ledger-db replica lag and lock contention to confirmation delays on core-app-svc-03. The overlap window and the single dependency edge support a probable origin at the ledger database, though evidence is thinner than Incidents A or B.\n\nThis is a probable-cause hypothesis, not confirmed causation.",
      overlapping_time_windows: ["13:35:00Z - 13:39:00Z across 2 affected services"],
      supporting_dependencies: ["core-app-svc-03 -> payments-ledger-db"],
      supporting_metrics: ["Replica lag > 8s", "Lock wait time elevated"],
      missing_or_contradictory: ["No corroborating network or infra event found"],
      similar_historical_incidents: [],
      unresolved_uncertainty: ["Only one dependency edge observed; broader blast radius unconfirmed"],
    },
    safety: {
      confidence_label: "DEMO ESTIMATE — NOT CALIBRATED",
      confidence_value: 0.48,
      evidence_completeness: "insufficient",
      topology_bias_note: null,
      matches_confirmed_pattern: false,
      weak_support_warning: "Origin hypothesis is weakly supported — only one dependency edge and 5 alerts observed.",
      missing_evidence_warning: "No corroborating infrastructure event found for the lock contention symptom.",
    },
    workflow_status: "VERIFIED",
    is_candidate: false,
    ai_status: "AI_NOT_RUN",
    ai_reason: "Deterministic evidence was sufficient; AI-assisted analysis was not needed.",
    ai_model_used: null,
    ai_alerts_considered: 0,
    ai_historical_cases_considered: 0,
  },
];

export const demoIncidentSummaries: IncidentSummary[] = demoIncidents.map((i) => ({
  id: i.id,
  title: i.title,
  severity: i.severity,
  related_alert_count: i.related_alerts.length,
  origin_hypothesis: i.origin_hypothesis,
  affected_service_count: i.affected_services.length,
  status: i.status,
  last_activity: i.last_seen,
  workflow_status: i.workflow_status,
  is_candidate: i.is_candidate,
}));

export const demoSafetyQueueAlerts: Alert[] = [
  alert(
    "ALT-SAFE-01",
    12,
    "waf-firewall",
    "Unseen TLS cipher mismatch drop detected.",
    "critical",
    null,
    true,
    "No sufficiently supported grouping found.",
  ),
];

export const demoTuningRecommendations: TuningRecommendation[] = [
  {
    rule_id: "TUNE-001",
    service: "analytics-clickhouse",
    rule_name: "LEGACY_DISK_WARN_70",
    count_24h: 68,
    historical_links: 0,
    suggested_action: "Review threshold and escalation policy.",
    status: "pending",
  },
  {
    rule_id: "TUNE-002",
    service: "memcached-catalog",
    rule_name: "CACHE_WARMUP_CPU",
    count_24h: 64,
    historical_links: 0,
    suggested_action: "Compare occurrences with known scheduled workloads.",
    status: "pending",
  },
  {
    rule_id: "TUNE-003",
    service: "core-app-svc-19",
    rule_name: "GC_PAUSE_WARN_200MS",
    count_24h: 71,
    historical_links: 0,
    suggested_action: "Review alert frequency, workload context and threshold suitability.",
    status: "pending",
  },
  {
    rule_id: "TUNE-004",
    service: "search-indexer-03",
    rule_name: "QUEUE_DEPTH_WARN_500",
    count_24h: 65,
    historical_links: 0,
    suggested_action: "Compare queue-depth threshold against typical indexing backlog.",
    status: "pending",
  },
  {
    rule_id: "TUNE-005",
    service: "notification-dispatch",
    rule_name: "RETRY_RATE_WARN_10PCT",
    count_24h: 63,
    historical_links: 0,
    suggested_action: "Review retry-rate threshold against known downstream flakiness.",
    status: "pending",
  },
  {
    rule_id: "TUNE-006",
    service: "image-resize-worker",
    rule_name: "CPU_THROTTLE_WARN_80",
    count_24h: 65,
    historical_links: 0,
    suggested_action: "Review throttle threshold against expected batch-job load.",
    status: "pending",
  },
];

export const demoRawAlertCount =
  demoIncidents.reduce((sum, i) => sum + i.related_alerts.length, 0) +
  demoSafetyQueueAlerts.length +
  demoTuningRecommendations.reduce((sum, t) => sum + t.count_24h, 0);

export const initialDemoMemoryLog: SeaMemoryEvent[] = [];
