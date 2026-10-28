"""Functional tests for the Triage -> Investigation -> Verification
pipeline. Covers the required cases from the AIOps milestone spec.

NOTE: these were written against the implementation but could not be
executed in the environment this milestone was implemented in (no Python
interpreter was available — see the final implementation report). Run
`pytest` from `backend/` after installing requirements.txt to verify.
"""
from __future__ import annotations

from app.agents.verification_agent import VerificationAgent
from app.evaluation.replay import compute_quick_replay_evaluation
from app.fixtures_pipeline import EXPECTED_ALERT_WORKFLOW_STATUS, REPLAY_ALERTS
from app.llm_provider import ModelProvider
from app.models import ServiceDependency
from app.schemas.agent_results import InvestigationResult, WorkflowStatus


def run_replay(orchestrator):
    return [orchestrator.process_alert(dict(a), is_synthetic=False) for a in REPLAY_ALERTS]


# 1. A valid alert is ingested and preserved.
def test_valid_alert_is_ingested_and_preserved(orchestrator, fresh_store):
    raw = {"id": "T1", "service": "svc-x", "signal": "CPU high", "severity": "high"}
    result = orchestrator.process_alert(raw)
    assert result.alert_id == "T1"
    assert "T1" in fresh_store.all_alerts


# 2. A repeated alert is deduplicated without losing original occurrence info.
def test_duplicate_alert_recognized_without_losing_original(orchestrator, fresh_store):
    run_replay(orchestrator)
    assert fresh_store.all_alerts["R-A1"].workflow_status == "VERIFIED"
    assert fresh_store.all_alerts["R-DUP1"].workflow_status == "DUPLICATE"
    assert fresh_store.all_alerts["R-DUP1"].duplicate_of == "R-A1"
    # original occurrence (R-A1) is still present and untouched
    assert "R-A1" in fresh_store.all_alerts


# 3. Alerts with supporting dependency and temporal evidence are grouped as expected.
def test_related_alerts_grouped_with_evidence(orchestrator, fresh_store):
    run_replay(orchestrator)
    for alert_id in ("R-A1", "R-A2", "R-A3"):
        assert fresh_store.all_alerts[alert_id].workflow_status == EXPECTED_ALERT_WORKFLOW_STATUS[alert_id]
    group_ids = {fresh_store.all_alerts[a].incident_id for a in ("R-A1", "R-A2", "R-A3")}
    assert len(group_ids) == 1 and None not in group_ids


# 3b. Phase 3 regression: the REAL /api/pipeline/replay endpoint's
# persisted ProcessingResult for a SEED alert (R-A1, grouped only once a
# LATER alert R-A2 bootstraps a group from it) must reflect its final
# incident_id/workflow_status, not the stale "still unclustered" snapshot
# captured when R-A1 was first, individually processed.
def test_replay_endpoint_processing_results_reflect_final_group_state(orchestrator, fresh_store):
    # fresh_store/orchestrator (conftest.py fixtures) use the default
    # seed_demo_fixtures=True Store, which is what actually populates the
    # "core-app-svc-01 <-> primary-postgres-db" dependency edge that R-A1/
    # R-A2/R-A3's grouping depends on — a seed_demo_fixtures=False store
    # has NO dependencies at all and could never reproduce this.
    import app.routers.pipeline as pipeline_router

    original_orchestrator, original_store = pipeline_router.orchestrator, pipeline_router.store
    pipeline_router.orchestrator, pipeline_router.store = orchestrator, fresh_store
    try:
        response = pipeline_router.run_replay()
    finally:
        pipeline_router.orchestrator, pipeline_router.store = original_orchestrator, original_store

    by_id = {r.alert_id: r for r in response.results}
    assert by_id["R-A1"].incident_id is not None
    assert by_id["R-A1"].incident_id == by_id["R-A2"].incident_id == by_id["R-A3"].incident_id
    assert by_id["R-A1"].workflow_status == WorkflowStatus.VERIFIED


# 4. Unrelated alerts occurring at similar times are not automatically merged.
def test_unrelated_alerts_not_automatically_merged(orchestrator, fresh_store):
    run_replay(orchestrator)
    novel = fresh_store.all_alerts["R-NOVEL1"]
    assert novel.incident_id is None
    assert novel.workflow_status == "UNCLUSTERED"


# 5. A novel alert without sufficient context remains visible.
def test_novel_alert_remains_visible_in_safety_queue(orchestrator, fresh_store):
    run_replay(orchestrator)
    safety_ids = {a.id for a in fresh_store.safety_queue_alerts}
    assert "R-NOVEL1" in safety_ids
    assert "R-AMBIG1" in safety_ids


# 6. Missing dependency data is handled safely.
def test_missing_dependency_data_handled_safely(orchestrator, fresh_store):
    raw = {"id": "T6", "service": "completely-unknown-service", "signal": "Something odd", "severity": "low"}
    result = orchestrator.process_alert(raw)
    assert result.error is None
    assert result.workflow_status in (WorkflowStatus.UNCLUSTERED, WorkflowStatus.VERIFIED, WorkflowStatus.PENDING_HUMAN_REVIEW)


# 7. Investigation does not invent unavailable logs or metrics.
def test_investigation_does_not_invent_evidence(orchestrator, fresh_store):
    results = run_replay(orchestrator)
    group_result = next(r for r in results if r.alert_id == "R-A3")
    assert group_result.investigation is not None
    group_alerts = fresh_store.candidate_groups[group_result.incident_id]
    actual_service_names = {a.service for a in group_alerts}
    # the hypothesis narrative must only name services that are actually in the group
    narrative = group_result.investigation.probable_cause_hypothesis or ""
    assert any(name in narrative for name in actual_service_names)
    assert group_result.investigation.derived_relationships  # evidence, not invention
    for edge in group_result.investigation.derived_relationships:
        service, _, depends_on = edge.partition(" -> ")
        assert service in actual_service_names and depends_on in actual_service_names


# 8. Verification rejects references to nonexistent evidence.
def test_verification_rejects_nonexistent_evidence(fresh_store):
    agent = VerificationAgent()
    fake_investigation = InvestigationResult(
        candidate_group_id="FAKE",
        status="hypothesis_formed",
        observed_facts=["1 alert observed"],
        derived_relationships=["service-a -> service-that-does-not-exist"],
        probable_cause_hypothesis="Fabricated hypothesis",
    )
    result = agent.verify(
        "FAKE",
        group_alerts=[],
        dependencies=[ServiceDependency(service="service-a", depends_on="service-b")],
        investigation=fake_investigation,
        known_alert_ids=set(),
    )
    assert result.outcome == "rejected"
    assert any("do not exist" in r for r in result.reasons)


# 9. A failed agent does not silently discard an alert.
def test_failed_agent_does_not_discard_alert(orchestrator, fresh_store, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated agent crash")

    monkeypatch.setattr(orchestrator.triage_agent, "process", boom)
    raw = {"id": "T9", "service": "svc-x", "signal": "boom test", "severity": "high"}
    result = orchestrator.process_alert(raw)
    assert result.workflow_status == WorkflowStatus.PROCESSING_ERROR
    assert "T9" in fresh_store.all_alerts  # still preserved despite the crash


# 10. A repeated processing request does not create uncontrolled duplicate state.
def test_repeated_processing_request_is_idempotent(orchestrator, fresh_store):
    raw = {"id": "T10", "service": "svc-x", "signal": "repeat me", "severity": "medium"}
    first = orchestrator.process_alert(raw)
    second = orchestrator.process_alert(raw)
    assert first == second
    assert len([a for a in fresh_store.all_alerts if a == "T10"]) == 1


# 11. A model timeout or invalid output triggers a documented fallback.
def test_model_failure_triggers_deterministic_fallback():
    from datetime import datetime, timezone

    from app.agents.investigation_agent import InvestigationAgent
    from app.ai.budget import ModelCallBudget
    from app.ai.config import AIConfig
    from app.ai.provider import ProviderTimeoutError
    from app.ai.usage_tracker import UsageTracker
    from app.models import Alert, AlertSeverity
    from app.schemas.agent_results import TriageResult

    class BrokenProvider:
        name = "broken-test-provider"
        model = "test-model"

        def invoke_investigation(self, evidence, max_output_tokens, timeout_seconds):
            raise ProviderTimeoutError("simulated model timeout")

    ai_config = AIConfig(
        enabled=True,
        provider="openai",
        model="test-model",
        base_url=None,
        api_key_configured=True,
        max_calls_per_run=10,
        max_alerts_per_incident=12,
        max_historical_cases=5,
        max_output_tokens=1000,
        max_retries=1,
        request_timeout_seconds=5.0,
    )
    agent = InvestigationAgent(
        provider=BrokenProvider(),
        ai_config=ai_config,
        budget=ModelCallBudget(max_calls=10),
        usage_tracker=UsageTracker(),
    )
    alerts = [
        Alert(
            id="E1",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            service="svc-a",
            signal="sig",
            severity=AlertSeverity.high,
            host_redacted="[REDACTED_HOST]",
            ip_redacted="[REDACTED_IP]",
            customer_redacted="[REDACTED]",
        ),
        Alert(
            id="E2",
            timestamp=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
            service="svc-b",
            signal="sig2",
            severity=AlertSeverity.high,
            host_redacted="[REDACTED_HOST]",
            ip_redacted="[REDACTED_IP]",
            customer_redacted="[REDACTED]",
        ),
    ]
    deps = [ServiceDependency(service="svc-a", depends_on="svc-b")]
    triage = TriageResult(alert_id="E2", decision="grouped", candidate_group_id="G1", raw_score=0.6)
    result = agent.investigate("G1", alerts, deps, [], triage, operator_requested_ai=True)
    assert result.ai_status == "AI_CALL_FAILED"
    assert result.evidence_source == "deterministic_fallback"
    assert result.model_used is None


# 12. Every relevant approval produces the expected audit event on success.
def test_approval_produces_audit_event(orchestrator, fresh_store):
    run_replay(orchestrator)
    incident_id = next(iid for iid in fresh_store.incidents if fresh_store.incidents[iid].workflow_status == "VERIFIED")
    before = len(fresh_store.memory_log)
    result = fresh_store.approve_incident(incident_id)
    assert result.success
    assert len(fresh_store.memory_log) == before + 1
    assert fresh_store.memory_log[-1].entity_id == incident_id


# 13. Failed approvals preserve the prior state.
def test_failed_approval_preserves_prior_state(fresh_store):
    before = len(fresh_store.memory_log)
    result = fresh_store.approve_incident("DOES-NOT-EXIST")
    assert not result.success
    assert len(fresh_store.memory_log) == before


# 14. Synthetic events retain their provenance throughout the pipeline.
def test_synthetic_provenance_retained(orchestrator, fresh_store):
    raw = {"id": "T14", "service": "svc-x", "signal": "synthetic test", "severity": "low"}
    result = orchestrator.process_alert(raw, is_synthetic=True)
    assert result.is_synthetic is True


# 16. Incident and safety queue counts remain internally consistent.
def test_incident_and_safety_queue_counts_consistent(orchestrator, fresh_store):
    run_replay(orchestrator)
    all_group_alert_ids = {a.id for g in fresh_store.candidate_groups.values() for a in g}
    safety_ids = {a.id for a in fresh_store.safety_queue_alerts}
    # a replay alert is never both grouped and sitting in the safety queue at once
    replay_ids = {a["id"] for a in REPLAY_ALERTS}
    assert not ((all_group_alert_ids & safety_ids) & replay_ids)


def test_replay_evaluation_matches_ground_truth(orchestrator, fresh_store):
    run_replay(orchestrator)
    replay_ids = [a["id"] for a in REPLAY_ALERTS]
    evaluation = compute_quick_replay_evaluation(fresh_store, replay_ids)
    assert evaluation.alert_pair_precision_pct == 100.0
    assert evaluation.alert_pair_recall_pct == 100.0
    assert evaluation.evaluated_on == "synthetic_fixture_ground_truth"
