from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..dataset_registry import registry
from ..models import ApprovalResult, Incident, IncidentSummary

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


def _to_summary(incident: Incident) -> IncidentSummary:
    return IncidentSummary(
        id=incident.id,
        title=incident.title,
        severity=incident.severity,
        related_alert_count=len(incident.related_alerts),
        origin_hypothesis=incident.origin_hypothesis,
        affected_service_count=len(incident.affected_services),
        status=incident.status,
        last_activity=incident.last_seen,
        workflow_status=incident.workflow_status,
        is_candidate=incident.is_candidate,
    )


@router.get("", response_model=list[IncidentSummary])
def list_incidents() -> list[IncidentSummary]:
    store = registry.get_active().store
    if store is None:
        return []
    return [_to_summary(i) for i in store.list_incidents()]


@router.get("/safety-queue/alerts")
def list_safety_queue():
    store = registry.get_active().store
    return store.safety_queue_alerts if store is not None else []


@router.get("/{incident_id}", response_model=Incident)
def get_incident(incident_id: str) -> Incident:
    store = registry.get_active().store
    incident = store.get_incident(incident_id) if store is not None else None
    if incident is None:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    return incident


@router.post("/{incident_id}/approve", response_model=ApprovalResult)
def approve_incident(incident_id: str) -> ApprovalResult:
    store = registry.get_active().store
    if store is None:
        raise HTTPException(status_code=400, detail="The active dataset has not been loaded yet.")
    result = store.approve_incident(incident_id)
    if not result.success:
        raise HTTPException(status_code=404, detail=result.message)
    return result
