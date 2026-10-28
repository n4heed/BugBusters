"""Canonical AIOps data model — the one shared shape every imported
dataset is normalized into, regardless of source format or original field
names. The three agents (triage/investigation/verification) only ever see
records built from these canonical types (converted further into the
existing pipeline `Alert` model by `ingestion/normalization.py`), never
raw CSV columns or source-specific JSON keys directly.

Every canonical record carries `dataset_id` and `source_ref` so its
provenance is always traceable back to the exact imported dataset and
original row/line.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class CanonicalSeverity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"


_SEVERITY_SYNONYMS: dict[str, CanonicalSeverity] = {
    "critical": CanonicalSeverity.critical,
    "crit": CanonicalSeverity.critical,
    "p1": CanonicalSeverity.critical,
    "sev1": CanonicalSeverity.critical,
    "high": CanonicalSeverity.high,
    "p2": CanonicalSeverity.high,
    "sev2": CanonicalSeverity.high,
    "major": CanonicalSeverity.high,
    "medium": CanonicalSeverity.medium,
    "med": CanonicalSeverity.medium,
    "moderate": CanonicalSeverity.medium,
    "p3": CanonicalSeverity.medium,
    "sev3": CanonicalSeverity.medium,
    "warning": CanonicalSeverity.medium,
    "low": CanonicalSeverity.low,
    "minor": CanonicalSeverity.low,
    "p4": CanonicalSeverity.low,
    "sev4": CanonicalSeverity.low,
    "info": CanonicalSeverity.low,
}


def coerce_severity(raw: str | None) -> CanonicalSeverity:
    if not raw:
        return CanonicalSeverity.unknown
    return _SEVERITY_SYNONYMS.get(str(raw).strip().lower(), CanonicalSeverity.unknown)


class SourceRef(BaseModel):
    """Traces a canonical record back to exactly where it came from."""

    dataset_id: str
    file_name: str
    row_index: int


class CanonicalAlert(BaseModel):
    event_id: str  # stable canonical identifier, generated if the source has none
    source_alert_id: Optional[str] = None  # the source's own ID, preserved verbatim
    timestamp: datetime
    service_name: str
    rule_name: Optional[str] = None
    severity: CanonicalSeverity
    signal_type: Optional[str] = None
    message: str
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    metric_threshold: Optional[float] = None
    fingerprint: Optional[str] = None
    dataset_id: str
    source_ref: SourceRef
    is_synthetic: bool = False
    extra_fields: dict = Field(default_factory=dict)  # unmapped source fields, preserved rather than discarded


class CanonicalMetric(BaseModel):
    timestamp: datetime
    service_name: str
    metric_name: str
    value: float
    unit: Optional[str] = None
    threshold: Optional[float] = None
    dataset_id: str
    source_ref: SourceRef


class CanonicalLog(BaseModel):
    timestamp: datetime
    service_name: str
    severity: CanonicalSeverity
    message: str
    correlation_id: Optional[str] = None
    dataset_id: str
    source_ref: SourceRef


class CanonicalDependency(BaseModel):
    """Direction convention (documented once, applied consistently):
    `source_service` DEPENDS ON `target_service` — i.e. an outage in
    target_service can cause symptoms in source_service. This matches the
    existing pipeline's `ServiceDependency(service, depends_on)` shape
    exactly, so conversion is a direct field rename.
    """

    source_service: str
    target_service: str
    relationship: Literal["depends_on"] = "depends_on"
    edge_type: Optional[str] = None
    dataset_id: str
    source_ref: SourceRef


class CanonicalHistoricalIncident(BaseModel):
    incident_id: str
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    affected_services: list[str] = Field(default_factory=list)
    description: Optional[str] = None
    evidence_refs: list[str] = Field(default_factory=list)
    # Only set when the SOURCE DATA actually states a confirmation status —
    # never inferred or defaulted to "confirmed".
    confirmation_status: Optional[Literal["confirmed", "unconfirmed", "false_positive"]] = None
    available_since: Optional[datetime] = None
    dataset_id: str
    source_ref: SourceRef


class GroundTruthLabel(BaseModel):
    """Evaluation-only. Never passed to any agent as an inference-time
    fact — see ingestion/import_service.py and evaluation/ground_truth.py
    for the enforced separation.
    """

    alert_event_id: str
    true_incident_id: str
    dataset_id: str
    source_ref: SourceRef


FileCategory = Literal[
    "alerts", "metrics", "logs", "dependencies", "historical_incidents", "ground_truth", "unknown"
]
