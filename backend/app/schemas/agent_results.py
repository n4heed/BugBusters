"""Typed contracts passed between the Triage, Investigation and Verification
agents and the orchestrator. Nothing here is a statistically calibrated
probability — `confidence_score` and similar fields are heuristic scores
from deterministic rules, labelled as such wherever they are surfaced.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class WorkflowStatus(str, Enum):
    RECEIVED = "RECEIVED"
    TRIAGE_COMPLETE = "TRIAGE_COMPLETE"
    INVESTIGATION_PENDING = "INVESTIGATION_PENDING"
    INVESTIGATION_COMPLETE = "INVESTIGATION_COMPLETE"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"
    VERIFIED = "VERIFIED"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
    UNCLUSTERED = "UNCLUSTERED"
    # Phase 3 fix: a confirmed duplicate previously had NO matching enum
    # value and was reported as UNCLUSTERED here despite the underlying
    # Alert.workflow_status correctly saying "DUPLICATE" — a real
    # inconsistency between the Alert model and the final ProcessingResult.
    DUPLICATE = "DUPLICATE"
    PROCESSING_ERROR = "PROCESSING_ERROR"


SignalType = Literal[
    "temporal_proximity",
    "shared_service",
    "dependency_path",
    "fingerprint_similarity",
    "metric_log_symptom",
    "historical_pattern",
]


class CorrelationSignal(BaseModel):
    signal_type: SignalType
    description: str
    weight: float
    matched: bool


class TriageResult(BaseModel):
    alert_id: str
    decision: Literal["grouped", "unclustered", "duplicate"]
    candidate_group_id: Optional[str] = None
    seed_alert_id: Optional[str] = None
    duplicate_of: Optional[str] = None
    evidence_signals: list[CorrelationSignal] = Field(default_factory=list)
    raw_score: float
    uncertainty_flags: list[str] = Field(default_factory=list)


class InvestigationResult(BaseModel):
    model_config = ConfigDict(protected_namespaces=())  # "model_used" field name is intentional, not a pydantic internal

    candidate_group_id: str
    status: Literal["insufficient_evidence", "hypothesis_formed"]
    observed_facts: list[str] = Field(default_factory=list)
    derived_relationships: list[str] = Field(default_factory=list)
    historical_similarities: list[str] = Field(default_factory=list)
    probable_cause_hypothesis: Optional[str] = None
    missing_or_contradictory: list[str] = Field(default_factory=list)
    unresolved_unknowns: list[str] = Field(default_factory=list)
    evidence_source: Literal["deterministic_fallback", "llm"] = "deterministic_fallback"
    model_used: Optional[str] = None
    ai_status: Literal[
        "AI_NOT_RUN", "AI_DISABLED", "AI_CALLED", "AI_BUDGET_EXHAUSTED", "AI_CALL_FAILED"
    ] = "AI_NOT_RUN"
    ai_reason: str = "Deterministic evidence was sufficient; AI-assisted analysis was not needed."
    ai_alerts_considered: int = 0
    ai_historical_cases_considered: int = 0


class VerificationResult(BaseModel):
    candidate_group_id: str
    outcome: Literal["verified", "rejected", "pending"]
    reasons: list[str] = Field(default_factory=list)
    validated_alert_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)


class ProcessingResult(BaseModel):
    alert_id: str
    workflow_status: WorkflowStatus
    triage: Optional[TriageResult] = None
    investigation: Optional[InvestigationResult] = None
    verification: Optional[VerificationResult] = None
    incident_id: Optional[str] = None
    is_synthetic: bool = False
    error: Optional[str] = None
    processing_ms: Optional[float] = None
    processed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Phase 3 (section 5): the final result must not contradict the
    # alert's own disposition — these two fields mirror Alert.disposition /
    # Alert.duplicate_of so that a confirmed duplicate's ProcessingResult
    # is self-consistent with the Alert model without a second read.
    # Literal["unique", "redundant", "uncertain"], kept as a plain string
    # here (not the models.AlertDisposition enum) to avoid schemas/
    # importing from models — see orchestrator.py for how it's populated.
    disposition: Optional[Literal["unique", "redundant", "uncertain"]] = None
    duplicate_of: Optional[str] = None
