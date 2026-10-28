"""Synthetic demo fixtures.

These numbers are illustrative hackathon demo data, not measured production
results. They are internally reconciled:

    raw_alerts (420) = incident alerts (12 + 6 + 5 = 23)
                      + safety queue alerts (1)
                      + recurring tuning-review alerts (396)
                      = 23 + 1 + 396 = 420

Six tuning rule groups sum to the 396 recurring alert events.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import (
    Alert,
    AlertSeverity,
    Evidence,
    Incident,
    IncidentStatus,
    SafetyDisclosure,
    ServiceDependency,
    TuningRecommendation,
    RecommendationStatus,
)
from .services.normalization import compute_fingerprint

NOW = datetime(2026, 10, 28, 14, 30, 0, tzinfo=timezone.utc)


def _ts(minutes_ago: int) -> datetime:
    return NOW - timedelta(minutes=minutes_ago)


def _alert(
    id_: str,
    minutes_ago: int,
    service: str,
    signal: str,
    severity: AlertSeverity,
    host: str,
    ip: str,
    customer: str,
    incident_id: str | None = None,
    is_safety_queue: bool = False,
    safety_reason: str | None = None,
) -> Alert:
    return Alert(
        id=id_,
        timestamp=_ts(minutes_ago),
        service=service,
        signal=signal,
        severity=severity,
        host_redacted="[REDACTED_HOST]",
        ip_redacted="[REDACTED_IP]",
        customer_redacted="[REDACTED]",
        incident_id=incident_id,
        is_safety_queue=is_safety_queue,
        safety_reason=safety_reason,
        workflow_status="VERIFIED" if incident_id else ("UNCLUSTERED" if is_safety_queue else None),
        fingerprint=compute_fingerprint(service, signal),
    )


def build_incident_a() -> Incident:
    alerts = [
        _alert("ALT-A-01", 30, "primary-postgres-db", "Disk I/O > 94%", AlertSeverity.critical, "h1", "10.0.0.1", "cust-1", "INC-2026-001"),
        _alert("ALT-A-02", 29, "primary-postgres-db", "Connection pool exhausted", AlertSeverity.critical, "h1", "10.0.0.1", "cust-1", "INC-2026-001"),
        _alert("ALT-A-03", 28, "core-app-svc-01", "CPU > 92%, retry activity", AlertSeverity.high, "h2", "10.0.0.2", "cust-1", "INC-2026-001"),
        _alert("ALT-A-04", 27, "core-app-svc-01", "GC pause spike", AlertSeverity.high, "h2", "10.0.0.2", "cust-1", "INC-2026-001"),
        _alert("ALT-A-05", 26, "api-gateway", "HTTP 502/504 rate > 18.5%", AlertSeverity.critical, "h3", "10.0.0.3", "cust-1", "INC-2026-001"),
        _alert("ALT-A-06", 25, "api-gateway", "Upstream timeout spike", AlertSeverity.high, "h3", "10.0.0.3", "cust-1", "INC-2026-001"),
        _alert("ALT-A-07", 24, "status-page", "Degraded status propagated", AlertSeverity.medium, "h4", "10.0.0.4", "cust-1", "INC-2026-001"),
        _alert("ALT-A-08", 23, "async-worker-01", "Queue backlog growing", AlertSeverity.high, "h5", "10.0.0.5", "cust-1", "INC-2026-001"),
        _alert("ALT-A-09", 22, "async-worker-01", "Job retry storm", AlertSeverity.high, "h5", "10.0.0.5", "cust-1", "INC-2026-001"),
        _alert("ALT-A-10", 21, "primary-postgres-db", "Write latency > 2s", AlertSeverity.critical, "h1", "10.0.0.1", "cust-1", "INC-2026-001"),
        _alert("ALT-A-11", 20, "core-app-svc-01", "5xx rate climbing", AlertSeverity.high, "h2", "10.0.0.2", "cust-1", "INC-2026-001"),
        _alert("ALT-A-12", 19, "api-gateway", "Circuit breaker opened", AlertSeverity.critical, "h3", "10.0.0.3", "cust-1", "INC-2026-001"),
    ]
    return Incident(
        id="INC-2026-001",
        title="Cascading Failure via primary-postgres-db",
        severity=AlertSeverity.critical,
        origin_hypothesis="primary-postgres-db",
        first_seen=_ts(30),
        last_seen=_ts(19),
        affected_services=["primary-postgres-db", "core-app-svc-01", "api-gateway", "status-page", "async-worker-01"],
        status=IncidentStatus.pending_review,
        related_alerts=alerts,
        dependencies=[
            ServiceDependency(service="status-page", depends_on="api-gateway"),
            ServiceDependency(service="api-gateway", depends_on="core-app-svc-01"),
            ServiceDependency(service="core-app-svc-01", depends_on="primary-postgres-db"),
            ServiceDependency(service="async-worker-01", depends_on="core-app-svc-01"),
        ],
        evidence=Evidence(
            narrative=(
                "The proposed grouping contains 12 alerts across the affected "
                "services. Shared dependency paths and latency changes within "
                "the relevant investigation window support a possible failure "
                "originating at primary-postgres-db.\n\n"
                "This is a probable-cause hypothesis, not proof of causation. "
                "Review the linked metrics and logs before confirming it."
            ),
            overlapping_time_windows=["14:00:05Z - 14:11:00Z across all 5 affected services"],
            supporting_dependencies=[
                "core-app-svc-01 -> primary-postgres-db",
                "api-gateway -> core-app-svc-01",
                "async-worker-01 -> core-app-svc-01",
            ],
            supporting_metrics=["Disk I/O > 94% on primary-postgres-db", "HTTP 502/504 rate > 18.5% on api-gateway"],
            missing_or_contradictory=["No confirmed deploy or config-change event in this window"],
            similar_historical_incidents=[],
            unresolved_uncertainty=["Whether the disk I/O spike is cause or symptom of an upstream query pattern"],
        ),
        safety=SafetyDisclosure(
            confidence_label="DEMO ESTIMATE — NOT CALIBRATED",
            confidence_value=0.74,
            evidence_completeness="partial",
            topology_bias_note="Grouping leans on known dependency topology; services without modeled edges would not surface here.",
            matches_confirmed_pattern=False,
            weak_support_warning=None,
            missing_evidence_warning="No deploy/change event correlated yet; causal direction unconfirmed.",
        ),
    )


def build_incident_b() -> Incident:
    alerts = [
        _alert("ALT-B-01", 40, "redis-session-cluster", "Memory eviction rate spike", AlertSeverity.high, "h6", "10.0.1.1", "cust-2", "INC-2026-002"),
        _alert("ALT-B-02", 39, "redis-session-cluster", "Replication lag > 5s", AlertSeverity.high, "h6", "10.0.1.1", "cust-2", "INC-2026-002"),
        _alert("ALT-B-03", 38, "core-app-svc-02", "Session lookup latency up", AlertSeverity.medium, "h7", "10.0.1.2", "cust-2", "INC-2026-002"),
        _alert("ALT-B-04", 37, "core-app-svc-02", "Auth timeout rate up", AlertSeverity.high, "h7", "10.0.1.2", "cust-2", "INC-2026-002"),
        _alert("ALT-B-05", 36, "api-gateway", "Elevated 401 rate", AlertSeverity.medium, "h3", "10.0.0.3", "cust-2", "INC-2026-002"),
        _alert("ALT-B-06", 35, "redis-session-cluster", "Node failover triggered", AlertSeverity.high, "h6", "10.0.1.1", "cust-2", "INC-2026-002"),
    ]
    return Incident(
        id="INC-2026-002",
        title="Cascading Failure via redis-session-cluster",
        severity=AlertSeverity.high,
        origin_hypothesis="redis-session-cluster",
        first_seen=_ts(40),
        last_seen=_ts(35),
        affected_services=["redis-session-cluster", "core-app-svc-02", "api-gateway"],
        status=IncidentStatus.pending_review,
        related_alerts=alerts,
        dependencies=[
            ServiceDependency(service="api-gateway", depends_on="core-app-svc-02"),
            ServiceDependency(service="core-app-svc-02", depends_on="redis-session-cluster"),
        ],
        evidence=Evidence(
            narrative=(
                "6 alerts cluster around redis-session-cluster within a 5-minute "
                "window. Session-lookup and auth-timeout symptoms on core-app-svc-02 "
                "follow the cluster's replication lag and failover event, consistent "
                "with a session-store origin.\n\n"
                "This is a probable-cause hypothesis, not confirmed causation."
            ),
            overlapping_time_windows=["13:50:00Z - 13:55:00Z across 3 affected services"],
            supporting_dependencies=["core-app-svc-02 -> redis-session-cluster", "api-gateway -> core-app-svc-02"],
            supporting_metrics=["Memory eviction rate spike on redis-session-cluster", "Replication lag > 5s"],
            missing_or_contradictory=["No corresponding spike in core-app-svc-02 CPU/memory"],
            similar_historical_incidents=[],
            unresolved_uncertainty=["Whether failover was triggered by eviction pressure or a separate network blip"],
        ),
        safety=SafetyDisclosure(
            confidence_label="DEMO ESTIMATE — NOT CALIBRATED",
            confidence_value=0.61,
            evidence_completeness="partial",
            topology_bias_note=None,
            matches_confirmed_pattern=False,
            weak_support_warning="Moderate alert count; origin hypothesis is weakly supported relative to Incident A.",
            missing_evidence_warning=None,
        ),
    )


def build_incident_c() -> Incident:
    alerts = [
        _alert("ALT-C-01", 55, "payments-ledger-db", "Replica lag > 8s", AlertSeverity.medium, "h8", "10.0.2.1", "cust-3", "INC-2026-003"),
        _alert("ALT-C-02", 54, "payments-ledger-db", "Slow query rate up", AlertSeverity.medium, "h8", "10.0.2.1", "cust-3", "INC-2026-003"),
        _alert("ALT-C-03", 53, "core-app-svc-03", "Payment confirmation delay", AlertSeverity.medium, "h9", "10.0.2.2", "cust-3", "INC-2026-003"),
        _alert("ALT-C-04", 52, "core-app-svc-03", "Downstream retry increase", AlertSeverity.low, "h9", "10.0.2.2", "cust-3", "INC-2026-003"),
        _alert("ALT-C-05", 51, "payments-ledger-db", "Lock wait time elevated", AlertSeverity.medium, "h8", "10.0.2.1", "cust-3", "INC-2026-003"),
    ]
    return Incident(
        id="INC-2026-003",
        title="Cascading Failure via payments-ledger-db",
        severity=AlertSeverity.medium,
        origin_hypothesis="payments-ledger-db",
        first_seen=_ts(55),
        last_seen=_ts(51),
        affected_services=["payments-ledger-db", "core-app-svc-03"],
        status=IncidentStatus.pending_review,
        related_alerts=alerts,
        dependencies=[
            ServiceDependency(service="core-app-svc-03", depends_on="payments-ledger-db"),
        ],
        evidence=Evidence(
            narrative=(
                "5 alerts link payments-ledger-db replica lag and lock contention "
                "to confirmation delays on core-app-svc-03. The overlap window and "
                "the single dependency edge support a probable origin at the "
                "ledger database, though evidence is thinner than Incidents A or B.\n\n"
                "This is a probable-cause hypothesis, not confirmed causation."
            ),
            overlapping_time_windows=["13:35:00Z - 13:39:00Z across 2 affected services"],
            supporting_dependencies=["core-app-svc-03 -> payments-ledger-db"],
            supporting_metrics=["Replica lag > 8s", "Lock wait time elevated"],
            missing_or_contradictory=["No corroborating network or infra event found"],
            similar_historical_incidents=[],
            unresolved_uncertainty=["Only one dependency edge observed; broader blast radius unconfirmed"],
        ),
        safety=SafetyDisclosure(
            confidence_label="DEMO ESTIMATE — NOT CALIBRATED",
            confidence_value=0.48,
            evidence_completeness="insufficient",
            topology_bias_note=None,
            matches_confirmed_pattern=False,
            weak_support_warning="Origin hypothesis is weakly supported — only one dependency edge and 5 alerts observed.",
            missing_evidence_warning="No corroborating infrastructure event found for the lock contention symptom.",
        ),
    )


def build_safety_queue_alert() -> Alert:
    return _alert(
        "ALT-SAFE-01",
        12,
        "waf-firewall",
        "Unseen TLS cipher mismatch drop detected.",
        AlertSeverity.critical,
        "h10",
        "10.0.3.1",
        "cust-4",
        incident_id=None,
        is_safety_queue=True,
        safety_reason="No sufficiently supported grouping found.",
    )


def build_tuning_recommendations() -> list[TuningRecommendation]:
    return [
        TuningRecommendation(
            rule_id="TUNE-001",
            service="analytics-clickhouse",
            rule_name="LEGACY_DISK_WARN_70",
            count_24h=68,
            historical_links=0,
            suggested_action="Review threshold and escalation policy.",
            status=RecommendationStatus.pending,
        ),
        TuningRecommendation(
            rule_id="TUNE-002",
            service="memcached-catalog",
            rule_name="CACHE_WARMUP_CPU",
            count_24h=64,
            historical_links=0,
            suggested_action="Compare occurrences with known scheduled workloads.",
            status=RecommendationStatus.pending,
        ),
        TuningRecommendation(
            rule_id="TUNE-003",
            service="core-app-svc-19",
            rule_name="GC_PAUSE_WARN_200MS",
            count_24h=71,
            historical_links=0,
            suggested_action="Review alert frequency, workload context and threshold suitability.",
            status=RecommendationStatus.pending,
        ),
        TuningRecommendation(
            rule_id="TUNE-004",
            service="search-indexer-03",
            rule_name="QUEUE_DEPTH_WARN_500",
            count_24h=65,
            historical_links=0,
            suggested_action="Compare queue-depth threshold against typical indexing backlog.",
            status=RecommendationStatus.pending,
        ),
        TuningRecommendation(
            rule_id="TUNE-005",
            service="notification-dispatch",
            rule_name="RETRY_RATE_WARN_10PCT",
            count_24h=63,
            historical_links=0,
            suggested_action="Review retry-rate threshold against known downstream flakiness.",
            status=RecommendationStatus.pending,
        ),
        TuningRecommendation(
            rule_id="TUNE-006",
            service="image-resize-worker",
            rule_name="CPU_THROTTLE_WARN_80",
            count_24h=65,
            historical_links=0,
            suggested_action="Review throttle threshold against expected batch-job load.",
            status=RecommendationStatus.pending,
        ),
    ]


def total_raw_alert_count() -> int:
    incidents = [build_incident_a(), build_incident_b(), build_incident_c()]
    incident_alerts = sum(len(i.related_alerts) for i in incidents)
    safety_alerts = 1
    tuning_alerts = sum(t.count_24h for t in build_tuning_recommendations())
    return incident_alerts + safety_alerts + tuning_alerts
