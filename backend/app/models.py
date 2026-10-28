from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class AlertSeverity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"


class IncidentStatus(str, Enum):
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"


class RecommendationStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    implemented = "implemented"


class AlertDisposition(str, Enum):
    """The duplicate-detection axis of an alert's identity — deliberately
    ORTHOGONAL to incident-group membership (`Alert.incident_id`), per the
    Phase 3 requirement that disposition and group membership never be
    conflated into one status field.

    unique: not a redundant duplicate of another alert. May or may not
        belong to an incident group — see `alert_relationship_label` below
        for that further (derived, never separately stored) distinction.
    redundant: a confirmed duplicate of another alert (its canonical
        representative — see `Alert.duplicate_of`, reused from the
        existing field rather than adding a new one).
    uncertain: the pipeline could not reliably establish a disposition
        (e.g. a malformed/schema-invalid record) — never silently
        defaulted to "unique" or "redundant".
    """

    unique = "unique"
    redundant = "redundant"
    uncertain = "uncertain"


class Alert(BaseModel):
    id: str
    timestamp: datetime
    service: str
    signal: str
    severity: AlertSeverity
    host_raw: Optional[str] = Field(default=None, exclude=True)
    ip_raw: Optional[str] = Field(default=None, exclude=True)
    customer_raw: Optional[str] = Field(default=None, exclude=True)
    host_redacted: str
    ip_redacted: str
    customer_redacted: str
    incident_id: Optional[str] = None
    is_safety_queue: bool = False
    safety_reason: Optional[str] = None
    workflow_status: Optional[str] = None
    duplicate_of: Optional[str] = None
    fingerprint: Optional[str] = Field(default=None, exclude=True)
    # Data provenance (Phase 3, section 2A) — distinct from `disposition`
    # below. An imported alert from a user's dataset is never a verified
    # production event merely because it was imported; this flag only ever
    # distinguishes it from the application's own synthetic/demo traffic.
    is_synthetic: bool = False
    # Duplicate-detection disposition (Phase 3, section 2B) — orthogonal to
    # `incident_id` (group membership). Defaults to "uncertain" until
    # triage actually runs; never silently defaulted to "unique".
    disposition: AlertDisposition = AlertDisposition.uncertain


def alert_relationship_label(alert: Alert) -> str:
    """The human-facing 4-way classification (redundant / related /
    standalone / uncertain) — DERIVED from `disposition` plus whether
    `incident_id` is set, never stored as its own field. This is what
    makes "related vs standalone" incapable of disagreeing with actual
    group membership: there is only one source of truth (`incident_id`)
    for whether an alert is in a group at all.
    """
    if alert.disposition == AlertDisposition.redundant:
        return "redundant"
    if alert.disposition == AlertDisposition.uncertain:
        return "uncertain"
    return "related" if alert.incident_id is not None else "standalone"


class ServiceDependency(BaseModel):
    service: str
    depends_on: str


class IncidentSummary(BaseModel):
    id: str
    title: str
    severity: AlertSeverity
    related_alert_count: int
    origin_hypothesis: str
    affected_service_count: int
    status: IncidentStatus
    last_activity: datetime
    workflow_status: str = "VERIFIED"
    is_candidate: bool = False


class Evidence(BaseModel):
    narrative: str
    overlapping_time_windows: list[str]
    supporting_dependencies: list[str]
    supporting_metrics: list[str]
    missing_or_contradictory: list[str]
    similar_historical_incidents: list[str]
    unresolved_uncertainty: list[str]


class SafetyDisclosure(BaseModel):
    confidence_label: Literal["DEMO ESTIMATE — NOT CALIBRATED", "NOT MEASURED"]
    confidence_value: Optional[float] = None
    evidence_completeness: Literal["complete", "partial", "insufficient"]
    topology_bias_note: Optional[str] = None
    matches_confirmed_pattern: bool = False
    weak_support_warning: Optional[str] = None
    missing_evidence_warning: Optional[str] = None


class Incident(BaseModel):
    id: str
    title: str
    severity: AlertSeverity
    origin_hypothesis: str
    first_seen: datetime
    last_seen: datetime
    affected_services: list[str]
    status: IncidentStatus
    related_alerts: list[Alert]
    dependencies: list[ServiceDependency]
    evidence: Evidence
    safety: SafetyDisclosure
    workflow_status: str = "VERIFIED"
    is_candidate: bool = False
    ai_status: str = "AI_NOT_RUN"
    ai_reason: str = "Deterministic evidence was sufficient; AI-assisted analysis was not needed."
    ai_model_used: Optional[str] = None
    ai_alerts_considered: int = 0
    ai_historical_cases_considered: int = 0


class TuningRecommendation(BaseModel):
    rule_id: str
    service: str
    rule_name: str
    count_24h: int
    historical_links: int
    suggested_action: str
    status: RecommendationStatus


class SeaMemoryEvent(BaseModel):
    id: str
    timestamp: datetime
    event_type: Literal[
        "INCIDENT_PATTERN_APPROVED",
        "TUNING_RECOMMENDATION_APPROVED",
        "TUNING_RECOMMENDATION_REJECTED",
    ]
    entity_id: str
    decision: str
    pattern: Optional[str] = None
    status: Literal["DEMO MEMORY ENTRY", "CONFIRMED"]


class EvaluationMetrics(BaseModel):
    raw_alerts_24h: Optional[int] = None
    correlated_incidents: Optional[int] = None
    notification_reduction_pct: Optional[float] = None
    incident_recall_pct: Optional[float] = None
    grouping_precision_pct: Optional[float] = None
    alert_pair_precision_pct: Optional[float] = None
    alert_pair_recall_pct: Optional[float] = None
    verification_failure_rate_pct: Optional[float] = None
    avg_processing_latency_ms: Optional[float] = None
    is_demo_data: bool = True
    evaluated_on: Literal["synthetic_fixture_ground_truth", "not_evaluated"] = "not_evaluated"


class ApprovalRequest(BaseModel):
    confirm: bool = True


class ApprovalResult(BaseModel):
    success: bool
    demo_only: bool
    message: str
    memory_event: Optional[SeaMemoryEvent] = None
