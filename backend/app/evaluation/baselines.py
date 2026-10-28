"""Four baselines, run on the IDENTICAL input (fixtures_pipeline.REPLAY_ALERTS)
so their metrics are directly comparable, per the milestone's requirement.

Baseline 4 (hybrid) uses a mocked provider, never a live API — this module
must never spend real API credit or require credentials to run.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..agents.investigation_agent import InvestigationAgent
from ..ai.budget import ModelCallBudget
from ..ai.config import AIConfig
from ..ai.schemas import ModelInvestigationResponse
from ..ai.usage_tracker import UsageTracker
from ..orchestrator import Orchestrator
from ..services.normalization import compute_fingerprint
from ..store import Store
from .metrics import alert_pairs


@dataclass
class BaselineResult:
    name: str
    predicted_pairs: set[frozenset[str]]
    predicted_incidents: list[set[str]]  # groups shown as one notification
    notification_count: int
    ai_calls_made: int = 0
    verification_outcomes: list[str] = field(default_factory=list)
    total_latency_ms: float = 0.0
    notes: str = ""


def baseline_no_grouping(raw_alerts: list[dict]) -> BaselineResult:
    """Every alert is its own notification. No correlation at all — the
    reference point for notification-reduction and for how little a
    "do nothing" system recovers.
    """
    ids = [a["id"] for a in raw_alerts]
    incidents = [{i} for i in ids]
    return BaselineResult(
        name="no_grouping",
        predicted_pairs=set(),
        predicted_incidents=incidents,
        notification_count=len(ids),
        notes="Each alert shown individually; no correlation logic runs at all.",
    )


def baseline_exact_dedup(raw_alerts: list[dict]) -> BaselineResult:
    """Group only by exact fingerprint (service+signal) — no temporal,
    dependency, or semantic correlation. What's achievable without the
    correlation engine at all.
    """
    fingerprint_groups: dict[str, set[str]] = {}
    for a in raw_alerts:
        fp = compute_fingerprint(a["service"], a["signal"])
        fingerprint_groups.setdefault(fp, set()).add(a["id"])

    incidents = list(fingerprint_groups.values())
    pairs = alert_pairs([g for g in incidents if len(g) > 1])
    return BaselineResult(
        name="exact_dedup",
        predicted_pairs=pairs,
        predicted_incidents=incidents,
        notification_count=len(incidents),
        notes=(
            "Pairs formed here are exact-fingerprint duplicates, not semantic incident "
            "relationships — they are not expected to match the incident ground truth pairs, "
            "and will typically score as false positives under the strict pairwise definition "
            "even though merging true duplicates is correct product behavior."
        ),
    )


def _run_pipeline(raw_alerts: list[dict], ai_enabled: bool, mock_response: ModelInvestigationResponse | None) -> tuple[Store, Orchestrator, int]:
    store = Store()
    orchestrator = Orchestrator(store)

    ai_calls = 0
    if ai_enabled:
        ai_config = AIConfig(
            enabled=True,
            provider="mock",
            model="mock-model",
            base_url=None,
            api_key_configured=True,
            max_calls_per_run=10,
            max_alerts_per_incident=12,
            max_historical_cases=5,
            max_output_tokens=1000,
            max_retries=1,
            request_timeout_seconds=5.0,
        )

        class _CountingMockProvider:
            name = "mock"
            model = "mock-model"

            def invoke_investigation(self, evidence, max_output_tokens, timeout_seconds):
                nonlocal ai_calls
                ai_calls += 1
                response = mock_response or ModelInvestigationResponse(
                    incident_id=evidence.candidate_group_id,
                    probable_origin=None,
                    hypotheses=[],
                    uncertainty_reason="Mock provider returned no opinion for this evaluation baseline.",
                )
                return response, {"input_tokens": 40, "output_tokens": 15, "tokens_are_estimated": False, "latency_ms": 1.0}

        orchestrator.ai_config = ai_config
        orchestrator.ai_budget = ModelCallBudget(max_calls=ai_config.max_calls_per_run)
        orchestrator.ai_usage = UsageTracker()
        orchestrator.investigation_agent = InvestigationAgent(
            provider=_CountingMockProvider(),
            ai_config=ai_config,
            budget=orchestrator.ai_budget,
            usage_tracker=orchestrator.ai_usage,
        )

    for raw in raw_alerts:
        orchestrator.process_alert(raw, is_synthetic=False)

    return store, orchestrator, ai_calls


def baseline_deterministic(raw_alerts: list[dict]) -> BaselineResult:
    """The implemented rules-based Triage/Investigation/Verification
    pipeline, AI disabled — baseline 3.
    """
    universe = {a["id"] for a in raw_alerts}
    store, orchestrator, _ = _run_pipeline(raw_alerts, ai_enabled=False, mock_response=None)

    incidents = [{a.id for a in g if a.id in universe} for g in store.candidate_groups.values()]
    incidents = [g for g in incidents if g]
    grouped_ids = {i for g in incidents for i in g}
    standalone = universe - grouped_ids
    # a duplicate alert doesn't add a new notification — it collapses into
    # the original's existing notification
    standalone_notifications = {
        aid for aid in standalone if store.all_alerts[aid].workflow_status != "DUPLICATE"
    }

    total_latency = sum(r.processing_ms or 0.0 for r in store.processing_results.values())

    return BaselineResult(
        name="deterministic_correlation",
        predicted_pairs=alert_pairs(incidents),
        predicted_incidents=incidents,
        notification_count=len(incidents) + len(standalone_notifications),
        verification_outcomes=list(store.verification_outcomes),
        total_latency_ms=round(total_latency, 3),
        notes="The implemented rules-based pipeline (services/correlation.py), AI disabled.",
    )


def baseline_hybrid(raw_alerts: list[dict], mock_response: ModelInvestigationResponse | None = None) -> BaselineResult:
    """Deterministic correlation + selective AI-assisted investigation —
    baseline 4. Uses a mocked provider (no live API call, no credentials
    required) so this baseline is always runnable as part of normal tests.
    """
    universe = {a["id"] for a in raw_alerts}
    store, orchestrator, ai_calls = _run_pipeline(raw_alerts, ai_enabled=True, mock_response=mock_response)

    incidents = [{a.id for a in g if a.id in universe} for g in store.candidate_groups.values()]
    incidents = [g for g in incidents if g]
    grouped_ids = {i for g in incidents for i in g}
    standalone = universe - grouped_ids
    standalone_notifications = {
        aid for aid in standalone if store.all_alerts[aid].workflow_status != "DUPLICATE"
    }

    total_latency = sum(r.processing_ms or 0.0 for r in store.processing_results.values())

    return BaselineResult(
        name="selective_hybrid",
        predicted_pairs=alert_pairs(incidents),
        predicted_incidents=incidents,
        notification_count=len(incidents) + len(standalone_notifications),
        ai_calls_made=ai_calls,
        verification_outcomes=list(store.verification_outcomes),
        total_latency_ms=round(total_latency, 3),
        notes=(
            "Deterministic correlation plus selective AI-assisted investigation (mocked provider). "
            "AI only assists the Investigation stage, which runs AFTER Triage has already decided "
            "group membership — so AI cannot change which alerts are grouped together in this "
            "architecture, only the narrative/hypothesis text and (if the model's response fails "
            "reference validation) the verification outcome for an already-formed group."
        ),
    )
