"""Tests for POST /api/pipeline/incidents/{id}/investigate — the endpoint
that lets an engineer select one existing candidate incident and explicitly
request an AI-assisted investigation of it on demand.

Uses a mocked provider throughout. No network calls or credentials are
involved in any test here (the autouse `_no_live_ai_by_default` fixture in
conftest.py also forces AI off at the environment level regardless).

Resolves against the dataset registry's ACTIVE dataset (see
dataset_registry.py) — a real bug was found and fixed where this endpoint
always looked in the default demo dataset's store, 404ing for any incident
that belonged to a different, selected-active uploaded dataset. These tests
register a throwaway dataset and set it active to cover that path.
"""
from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi import HTTPException

from app.ai.schemas import ModelInvestigationResponse
from app.dataset_registry import DatasetRecord, registry
from app.orchestrator import Orchestrator
from app.routers import pipeline as pipeline_router
from app.store import Store


class _MockProvider:
    name = "mock"
    model = "mock-model"

    def __init__(self, response: ModelInvestigationResponse):
        self._response = response
        self.call_count = 0

    def invoke_investigation(self, evidence, max_output_tokens, timeout_seconds):
        self.call_count += 1
        return self._response, {"input_tokens": 10, "output_tokens": 5, "tokens_are_estimated": False, "latency_ms": 1.0}


def _ai_enabled_orchestrator(store: Store, provider: _MockProvider) -> Orchestrator:
    orch = Orchestrator(store)
    enabled_config = replace(orch.ai_config, enabled=True, provider="mock", model="mock-model", api_key_configured=True)
    orch.ai_config = enabled_config
    orch.investigation_agent.ai_config = enabled_config
    orch.investigation_agent.provider = provider
    return orch


@pytest.fixture()
def active_dataset(monkeypatch):
    """Registers a throwaway dataset, built from the demo fixtures, as the
    ACTIVE one — exercising the same resolution path a real uploaded,
    selected dataset goes through, not the "default" special-case.
    """
    store = Store(seed_demo_fixtures=True)
    record = DatasetRecord(id="test-active-dataset", name="Test Dataset", provenance="uploaded", lifecycle_state="loaded")
    record.store = store
    registry.datasets[record.id] = record
    monkeypatch.setattr(registry, "active_dataset_id", record.id)
    try:
        yield record
    finally:
        registry.datasets.pop(record.id, None)


def test_investigate_existing_demo_incident_with_ai(active_dataset):
    store = active_dataset.store
    mock_response = ModelInvestigationResponse(
        incident_id="INC-2026-001",
        probable_origin="primary-postgres-db",
        affected_services=["primary-postgres-db"],
        supporting_evidence_ids=["ALT-A-01", "ALT-A-10"],
        historical_case_ids=[],
        hypotheses=["Disk I/O saturation on primary-postgres-db cascaded upstream."],
        contradictory_evidence=[],
        missing_evidence=[],
        uncertainty_reason=None,
        recommended_next_steps=["Check disk I/O metrics around the incident window."],
    )
    provider = _MockProvider(mock_response)
    active_dataset.orchestrator = _ai_enabled_orchestrator(store, provider)

    result = pipeline_router.investigate_incident("INC-2026-001")

    assert provider.call_count == 1
    assert result.investigation.evidence_source == "llm"
    assert result.investigation.ai_status == "AI_CALLED"
    assert result.investigation.model_used == "mock:mock-model"
    assert result.incident.id == "INC-2026-001"
    assert result.incident.ai_status == "AI_CALLED"
    # The model must never be allowed to invent alert IDs: only ones it was
    # actually given (ALT-A-01..12) may appear as supporting evidence.
    assert set(mock_response.supporting_evidence_ids) <= {a.id for a in store.incidents["INC-2026-001"].related_alerts}


def test_investigate_unknown_incident_returns_404(active_dataset):
    active_dataset.orchestrator = Orchestrator(active_dataset.store)

    with pytest.raises(HTTPException) as exc_info:
        pipeline_router.investigate_incident("NOT-A-REAL-INCIDENT")
    assert exc_info.value.status_code == 404


def test_investigate_incident_not_found_in_inactive_dataset_404s(active_dataset):
    """An incident ID that only exists in a DIFFERENT dataset than the
    active one must 404, not silently resolve against the wrong dataset —
    this is exactly the bug this endpoint originally had.
    """
    active_dataset.orchestrator = Orchestrator(active_dataset.store)

    other_store = Store(seed_demo_fixtures=True)
    other_record = DatasetRecord(id="other-dataset", name="Other", provenance="uploaded", lifecycle_state="loaded")
    other_record.store = other_store
    other_record.orchestrator = Orchestrator(other_store)
    registry.datasets[other_record.id] = other_record
    try:
        # INC-2026-001 exists in other_store but NOT in the active dataset's
        # store if we rename/remove it there first.
        del active_dataset.store.incidents["INC-2026-001"]
        with pytest.raises(HTTPException) as exc_info:
            pipeline_router.investigate_incident("INC-2026-001")
        assert exc_info.value.status_code == 404
    finally:
        registry.datasets.pop(other_record.id, None)


def test_investigate_incident_falls_back_to_deterministic_when_model_invents_evidence(active_dataset):
    """A model that cites an alert ID not present in the evidence it was
    given must never be trusted — the endpoint must fall back to the
    deterministic result rather than surfacing a fabricated reference.
    """
    store = active_dataset.store
    bad_response = ModelInvestigationResponse(
        incident_id="INC-2026-001",
        probable_origin="primary-postgres-db",
        affected_services=["primary-postgres-db"],
        supporting_evidence_ids=["ALT-DOES-NOT-EXIST"],
        historical_case_ids=[],
        hypotheses=["Fabricated hypothesis."],
        contradictory_evidence=[],
        missing_evidence=[],
        uncertainty_reason=None,
        recommended_next_steps=[],
    )
    provider = _MockProvider(bad_response)
    active_dataset.orchestrator = _ai_enabled_orchestrator(store, provider)

    result = pipeline_router.investigate_incident("INC-2026-001")

    assert result.investigation.ai_status == "AI_CALL_FAILED"
    assert result.investigation.evidence_source == "deterministic_fallback"
    # The alert group is preserved either way — nothing was discarded.
    assert len(result.incident.related_alerts) == 12
