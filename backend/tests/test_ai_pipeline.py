"""Tests for the selective AI integration: budget enforcement, bounded
evidence selection, ground-truth protection, selection policy, and
AI-disabled-by-default behavior. Uses a mocked provider — no real API
credentials or network calls are involved in any test here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.ai.budget import ModelCallBudget
from app.ai.config import AIConfig
from app.ai.evidence import build_evidence_package
from app.ai.schemas import ModelInvestigationResponse
from app.ai.selection_policy import should_use_ai
from app.ai.usage_tracker import UsageTracker
from app.agents.investigation_agent import InvestigationAgent
from app.models import Alert, AlertSeverity, ServiceDependency
from app.schemas.agent_results import InvestigationResult, TriageResult


def _alert(id_, service, signal, minutes_ago=0, severity=AlertSeverity.high):
    return Alert(
        id=id_,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc) - timedelta(minutes=minutes_ago),
        service=service,
        signal=signal,
        severity=severity,
        host_redacted="[REDACTED_HOST]",
        ip_redacted="[REDACTED_IP]",
        customer_redacted="[REDACTED]",
    )


def _enabled_config(**overrides) -> AIConfig:
    base = dict(
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
    base.update(overrides)
    return AIConfig(**base)


class MockProvider:
    name = "mock"
    model = "mock-model"

    def __init__(self, response: ModelInvestigationResponse | None = None, error: Exception | None = None):
        self._response = response
        self._error = error
        self.call_count = 0
        self.last_evidence = None

    def invoke_investigation(self, evidence, max_output_tokens, timeout_seconds):
        self.call_count += 1
        self.last_evidence = evidence
        if self._error is not None:
            raise self._error
        return self._response, {"input_tokens": 50, "output_tokens": 20, "tokens_are_estimated": False, "latency_ms": 5.0}


# 14. AI-disabled mode works without API credentials (default behavior).
def test_ai_disabled_by_default_never_calls_provider():
    config = AIConfig(
        enabled=False,
        provider="",
        model="",
        base_url=None,
        api_key_configured=False,
        max_calls_per_run=10,
        max_alerts_per_incident=12,
        max_historical_cases=5,
        max_output_tokens=1000,
        max_retries=1,
        request_timeout_seconds=5.0,
    )
    provider = MockProvider(error=AssertionError("should never be called"))
    agent = InvestigationAgent(provider=provider, ai_config=config, budget=ModelCallBudget(max_calls=10), usage_tracker=UsageTracker())
    alerts = [_alert("A1", "svc-a", "sig", 0), _alert("A2", "svc-b", "sig2", 1)]
    deps = [ServiceDependency(service="svc-a", depends_on="svc-b")]
    triage = TriageResult(alert_id="A2", decision="grouped", candidate_group_id="G1", raw_score=0.9)
    result = agent.investigate("G1", alerts, deps, [], triage)
    assert provider.call_count == 0
    assert result.ai_status == "AI_DISABLED"
    assert result.evidence_source == "deterministic_fallback"


# 3. An ambiguous candidate group can invoke the configured model.
def test_ambiguous_score_triggers_ai():
    triage = TriageResult(alert_id="X", decision="grouped", candidate_group_id="G1", raw_score=0.55)  # just above 0.50
    deterministic = InvestigationResult(candidate_group_id="G1", status="hypothesis_formed", observed_facts=["a"])
    decision = should_use_ai(_enabled_config(), triage, deterministic, operator_requested=False)
    assert decision.use_ai is True


def test_confidently_grouped_score_does_not_trigger_ai():
    triage = TriageResult(alert_id="X", decision="grouped", candidate_group_id="G1", raw_score=0.9)
    deterministic = InvestigationResult(candidate_group_id="G1", status="hypothesis_formed", observed_facts=["a"])
    decision = should_use_ai(_enabled_config(), triage, deterministic, operator_requested=False)
    assert decision.use_ai is False


def test_empty_evidence_does_not_trigger_ai_even_if_insufficient():
    triage = TriageResult(alert_id="X", decision="unclustered", raw_score=0.0)
    deterministic = InvestigationResult(candidate_group_id="G1", status="insufficient_evidence", observed_facts=[])
    decision = should_use_ai(_enabled_config(), triage, deterministic, operator_requested=False)
    assert decision.use_ai is False


# 5. The number of included alerts never exceeds the configured cap.
# 6. The number of retrieved historical cases never exceeds the configured cap.
def test_evidence_package_respects_caps_and_records_omissions():
    alerts = [_alert(f"A{i}", "svc-a", "sig", minutes_ago=i) for i in range(5)]
    deps: list[ServiceDependency] = []
    historical = [
        {"id": f"H{i}", "title": f"Case {i}", "origin_hypothesis": "svc-a", "last_seen": datetime(2025, 1, 1, tzinfo=timezone.utc)}
        for i in range(8)
    ]
    pkg = build_evidence_package("G1", alerts, deps, historical, max_alerts=3, max_historical_cases=2)
    assert len(pkg.alerts) == 3
    assert pkg.omitted_alert_count == 2
    assert len(pkg.historical_cases) == 2
    assert pkg.omitted_historical_case_count == 6


# 9. Ground-truth labels are not included in the investigation prompt.
def test_historical_case_after_current_alert_is_excluded_ground_truth():
    alerts = [_alert("A1", "svc-a", "sig", minutes_ago=0)]
    # alerts[0].timestamp is 2026-01-01 00:00 UTC; a "historical" case approved
    # AFTER that must never be offered as prior-art evidence.
    future_case = {
        "id": "FUTURE",
        "title": "This is the eventual answer for A1's own incident",
        "origin_hypothesis": "svc-a",
        "last_seen": datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc),
    }
    past_case = {
        "id": "PAST",
        "title": "A genuinely prior case",
        "origin_hypothesis": "svc-a",
        "last_seen": datetime(2025, 1, 1, tzinfo=timezone.utc),
    }
    pkg = build_evidence_package("G1", alerts, [], [future_case, past_case], max_alerts=12, max_historical_cases=5)
    ids = {c.id for c in pkg.historical_cases}
    assert "FUTURE" not in ids
    assert "PAST" in ids


# 11. Model output is schema-validated; an invented reference is rejected.
def test_invented_evidence_reference_is_rejected_and_falls_back():
    alerts = [_alert("A1", "svc-a", "sig", 0), _alert("A2", "svc-b", "sig2", 1)]
    deps = [ServiceDependency(service="svc-a", depends_on="svc-b")]
    bad_response = ModelInvestigationResponse(
        incident_id="G1",
        probable_origin="svc-a",
        supporting_evidence_ids=["A1", "A999-DOES-NOT-EXIST"],
        hypotheses=["svc-a caused this"],
    )
    provider = MockProvider(response=bad_response)
    agent = InvestigationAgent(
        provider=provider, ai_config=_enabled_config(max_retries=0), budget=ModelCallBudget(max_calls=10), usage_tracker=UsageTracker()
    )
    triage = TriageResult(alert_id="A2", decision="grouped", candidate_group_id="G1", raw_score=0.55)
    result = agent.investigate("G1", alerts, deps, [], triage)
    assert result.ai_status == "AI_CALL_FAILED"
    assert result.evidence_source == "deterministic_fallback"


# 4. Only selected incident records reach the model adapter (none of the
# other alerts in the system, only this group's own, bounded set).
def test_only_selected_group_alerts_reach_the_provider():
    alerts = [_alert("A1", "svc-a", "sig", 0), _alert("A2", "svc-b", "sig2", 1)]
    other_group_alert = _alert("Z1", "unrelated-service", "unrelated signal", 500)
    deps = [ServiceDependency(service="svc-a", depends_on="svc-b")]
    good_response = ModelInvestigationResponse(
        incident_id="G1", probable_origin="svc-a", supporting_evidence_ids=["A1", "A2"], hypotheses=["h"]
    )
    provider = MockProvider(response=good_response)
    agent = InvestigationAgent(
        provider=provider, ai_config=_enabled_config(), budget=ModelCallBudget(max_calls=10), usage_tracker=UsageTracker()
    )
    triage = TriageResult(alert_id="A2", decision="grouped", candidate_group_id="G1", raw_score=0.55)
    agent.investigate("G1", alerts, deps, [], triage)
    assert provider.last_evidence is not None
    sent_ids = provider.last_evidence.alert_ids()
    assert sent_ids == {"A1", "A2"}
    assert other_group_alert.id not in sent_ids


# 7. The model-call budget is enforced across agents.
def test_budget_exhaustion_falls_back_without_calling_provider():
    alerts = [_alert("A1", "svc-a", "sig", 0), _alert("A2", "svc-b", "sig2", 1)]
    deps = [ServiceDependency(service="svc-a", depends_on="svc-b")]
    provider = MockProvider(error=AssertionError("should not be called once budget is exhausted"))
    budget = ModelCallBudget(max_calls=1)
    budget.spend("pre-existing call")  # simulate budget already used up
    agent = InvestigationAgent(provider=provider, ai_config=_enabled_config(), budget=budget, usage_tracker=UsageTracker())
    triage = TriageResult(alert_id="A2", decision="grouped", candidate_group_id="G1", raw_score=0.55)
    result = agent.investigate("G1", alerts, deps, [], triage)
    assert provider.call_count == 0
    assert result.ai_status == "AI_BUDGET_EXHAUSTED"


# 16. Token usage and latency are recorded without exposing secrets.
def test_successful_call_records_usage_without_secrets():
    alerts = [_alert("A1", "svc-a", "sig", 0), _alert("A2", "svc-b", "sig2", 1)]
    deps = [ServiceDependency(service="svc-a", depends_on="svc-b")]
    good_response = ModelInvestigationResponse(
        incident_id="G1", probable_origin="svc-a", supporting_evidence_ids=["A1", "A2"], hypotheses=["h"]
    )
    provider = MockProvider(response=good_response)
    tracker = UsageTracker()
    agent = InvestigationAgent(provider=provider, ai_config=_enabled_config(), budget=ModelCallBudget(max_calls=10), usage_tracker=tracker)
    triage = TriageResult(alert_id="A2", decision="grouped", candidate_group_id="G1", raw_score=0.55)
    agent.investigate("G1", alerts, deps, [], triage)
    assert len(tracker.records) == 1
    record = tracker.records[0]
    assert record.status == "success"
    assert record.input_tokens == 50
    assert record.output_tokens == 20
    # the record is a Pydantic model with no credential field at all
    assert "api_key" not in record.model_dump()
