"""Typed contracts for the model-assisted investigation path.

`EvidencePackage` is the ONLY thing ever sent to an external provider — it
is deliberately small, already redacted, and carries its own bounds/omitted
counts so a truncated context is disclosed rather than silently implied to
be complete. `ModelInvestigationResponse` is what the provider must return;
it is schema-validated and every reference in it is checked against the
records actually supplied (see ai/provider.py and agents/investigation_agent.py).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class EvidenceAlertRef(BaseModel):
    """A single alert as exposed to the model — already redacted, no raw
    host/IP/customer fields exist on this type at all.
    """

    id: str
    timestamp: str
    service: str
    signal: str
    severity: str


class EvidenceHistoricalCaseRef(BaseModel):
    id: str
    title: str
    origin_hypothesis: str


class EvidencePackage(BaseModel):
    candidate_group_id: str
    alerts: list[EvidenceAlertRef]
    omitted_alert_count: int = 0
    dependency_edges: list[str]  # "service -> depends_on"
    historical_cases: list[EvidenceHistoricalCaseRef]
    omitted_historical_case_count: int = 0

    def alert_ids(self) -> set[str]:
        return {a.id for a in self.alerts}

    def historical_case_ids(self) -> set[str]:
        return {c.id for c in self.historical_cases}


class ModelInvestigationResponse(BaseModel):
    """Must match the schema example in the milestone spec. Every ID in
    `supporting_evidence_ids` / `historical_case_ids` must resolve to a
    record that was actually in the EvidencePackage sent to the model —
    enforced by the Investigation Agent, not trusted from the model.
    """

    incident_id: str
    probable_origin: Optional[str] = None
    affected_services: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    historical_case_ids: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    contradictory_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    uncertainty_reason: Optional[str] = None
    recommended_next_steps: list[str] = Field(default_factory=list)
