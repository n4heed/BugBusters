from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..dataset_registry import registry
from ..evaluation.replay import compute_quick_replay_evaluation
from ..fixtures_pipeline import REPLAY_ALERTS
from ..models import EvaluationMetrics, Incident
from ..orchestrator import orchestrator
from ..schemas.agent_results import InvestigationResult, ProcessingResult, TriageResult, VerificationResult
from ..store import store

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

# Mirrors orchestrator._VERIFICATION_TO_INCIDENT_STATUS — duplicated rather
# than imported since that name is orchestrator-internal, and this endpoint
# re-runs verification independently of a normal ingest pass.
_VERIFICATION_TO_INCIDENT_STATUS = {
    "verified": "VERIFIED",
    "pending": "PENDING_HUMAN_REVIEW",
    "rejected": "REJECTED",
}


class IngestAlertRequest(BaseModel):
    id: str | None = None
    timestamp: str | None = None
    service: str
    signal: str
    severity: str | None = None
    host: str | None = None
    ip: str | None = None
    customer: str | None = None
    force_ai: bool = Field(
        default=False,
        description=(
            "Explicit operator request for AI-assisted investigation on this alert's candidate group. "
            "Still subject to the shared call budget and bounded context limits — this does not bypass them."
        ),
    )


class ReplayResponse(BaseModel):
    results: list[ProcessingResult]
    evaluation: EvaluationMetrics


class InvestigateIncidentResponse(BaseModel):
    incident: Incident
    investigation: InvestigationResult
    verification: VerificationResult


@router.post("/ingest", response_model=ProcessingResult)
def ingest_alert(payload: IngestAlertRequest) -> ProcessingResult:
    """Runs a single alert through Triage -> Investigation -> Verification."""
    raw = payload.model_dump(exclude={"force_ai"})
    return orchestrator.process_alert(raw, is_synthetic=False, operator_requested_ai=payload.force_ai)


@router.post("/replay", response_model=ReplayResponse)
def run_replay() -> ReplayResponse:
    """Runs the deterministic replay scenario (fixtures_pipeline.py) through
    the real pipeline, in order, and returns per-alert results plus
    evaluation metrics against its explicit synthetic ground truth.

    Idempotent against already-processed replay alert IDs: re-running after
    a prior replay returns the cached results for those IDs rather than
    double-processing (see Orchestrator.process_alert's idempotency check).
    """
    for a in REPLAY_ALERTS:
        orchestrator.process_alert(dict(a), is_synthetic=False)
    # Re-fetch final results AFTER the whole loop — a seed alert processed
    # earlier (e.g. R-A1) can be retroactively corrected once a later
    # alert (R-A2) bootstraps a group from it (see Orchestrator.
    # _run_pipeline / Store.correct_processing_result); collecting each
    # call's immediate return value would persist that alert's stale,
    # still-unclustered snapshot instead of its true final state.
    results = [store.processing_results[a["id"]] for a in REPLAY_ALERTS if a["id"] in store.processing_results]
    replay_ids = [a["id"] for a in REPLAY_ALERTS]
    store.last_replay_alert_ids = replay_ids
    evaluation = compute_quick_replay_evaluation(store, replay_ids)
    store.last_evaluation = evaluation
    return ReplayResponse(results=results, evaluation=evaluation)


@router.get("/evaluation", response_model=EvaluationMetrics | None)
def get_last_evaluation() -> EvaluationMetrics | None:
    return store.last_evaluation


@router.post("/incidents/{incident_id}/investigate", response_model=InvestigateIncidentResponse)
def investigate_incident(incident_id: str) -> InvestigateIncidentResponse:
    """Lets an engineer select one existing candidate incident and explicitly
    request an AI-assisted investigation of it, on demand — distinct from
    `/ingest`'s `force_ai`, which only applies at the moment a NEW alert
    arrives. Re-runs Investigation + Verification for the incident's
    existing alert group, using the same bounded-evidence, budget-limited
    path as the rest of the pipeline (ai/evidence.py, ai/budget.py) — this
    never sends the full dataset, never bypasses the shared call budget,
    and never lets an alert disappear if the investigation is inconclusive.

    Resolves against the ACTIVE dataset (not always the demo default) — an
    incident selected while a different dataset is loaded/active lives in
    that dataset's own isolated store, never in the default one.
    """
    active = registry.get_active()
    if active.store is None or active.orchestrator is None:
        raise HTTPException(status_code=400, detail="The active dataset has not been loaded yet.")
    active_store, active_orchestrator = active.store, active.orchestrator

    incident = active_store.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found in the active dataset")

    group_alerts = active_store.candidate_groups.get(incident_id) or incident.related_alerts
    if not group_alerts:
        raise HTTPException(status_code=400, detail=f"Incident {incident_id} has no associated alerts to investigate")

    # should_use_ai short-circuits to True on operator_requested regardless
    # of raw_score, so this placeholder TriageResult only needs to satisfy
    # the type contract — its score is never actually consulted.
    triage = TriageResult(
        alert_id=group_alerts[0].id,
        decision="grouped",
        candidate_group_id=incident_id,
        raw_score=1.0,
    )

    investigation = active_orchestrator.investigation_agent.investigate(
        incident_id,
        group_alerts,
        active_store.dependencies,
        active_store.historical_pattern_titles(),
        triage,
        historical_pattern_cases=active_store.historical_pattern_cases(),
        operator_requested_ai=True,
    )
    verification = active_orchestrator.verification_agent.verify(
        incident_id, group_alerts, active_store.dependencies, investigation, active_store.known_alert_ids()
    )
    workflow_status = _VERIFICATION_TO_INCIDENT_STATUS[verification.outcome]
    updated_incident = active_store.upsert_incident_from_group(
        incident_id, group_alerts, investigation, verification, workflow_status
    )
    return InvestigateIncidentResponse(incident=updated_incident, investigation=investigation, verification=verification)


@router.get("/ai/status")
def get_ai_status() -> dict:
    """Safe-to-render AI configuration summary — never includes the API key.
    Also exposes the current run's model-call budget usage. Reflects the
    ACTIVE dataset's own Orchestrator, not always the demo default.
    """
    active = registry.get_active()
    if active.orchestrator is None:
        return {"ai_enabled": False, "provider": None, "model": None, "api_key_configured": False}
    config_summary = active.orchestrator.ai_config.summary()
    config_summary["budget_used"] = active.orchestrator.ai_budget.used
    config_summary["budget_remaining"] = active.orchestrator.ai_budget.remaining()
    return config_summary


@router.get("/ai/usage")
def get_ai_usage() -> dict:
    """Operational metadata for every model call made so far this process —
    never prompts, never raw evidence, never credentials. Reflects the
    ACTIVE dataset's own Orchestrator, not always the demo default.
    """
    active = registry.get_active()
    if active.orchestrator is None:
        return {"summary": None, "records": []}
    return {
        "summary": active.orchestrator.ai_usage.summary(),
        "records": [r.model_dump(mode="json") for r in active.orchestrator.ai_usage.records],
    }
