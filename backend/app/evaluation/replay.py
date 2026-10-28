"""Reproducible replay: fixed input ordering, fixed clock (see
fixtures_pipeline.REPLAY_T0), no randomness — running it twice on a fresh
Store produces identical results. Used by both the quick in-API replay
(`routers/pipeline.py`'s `/pipeline/replay`) and the full evaluation report.
"""
from __future__ import annotations

from ..fixtures_pipeline import REPLAY_ALERTS
from ..models import EvaluationMetrics
from ..orchestrator import Orchestrator
from ..store import Store
from .ground_truth import TRUE_PAIRS
from .metrics import alert_pair_precision_recall, alert_pairs, verification_failure_rate_pct


def run_deterministic_replay(store: Store | None = None, orchestrator: Orchestrator | None = None) -> tuple[Store, list]:
    """Processes fixtures_pipeline.REPLAY_ALERTS in order against the given
    (or a fresh) store/orchestrator. Returns (store, processing_results).
    """
    store = store if store is not None else Store()
    orchestrator = orchestrator if orchestrator is not None else Orchestrator(store)
    results = [orchestrator.process_alert(dict(a), is_synthetic=False) for a in REPLAY_ALERTS]
    return store, results


def compute_quick_replay_evaluation(store: Store, replay_alert_ids: list[str]) -> EvaluationMetrics:
    """Same shape as before (used by the existing /api/pipeline/replay
    endpoint for an immediate summary), now built on the shared, correctly-
    defined metrics in evaluation/metrics.py instead of ad hoc inline math.
    """
    universe = set(replay_alert_ids)
    incidents = [{a.id for a in g if a.id in universe} for g in store.candidate_groups.values()]
    incidents = [g for g in incidents if g]
    predicted = alert_pairs(incidents)

    pair_metrics = alert_pair_precision_recall(predicted, TRUE_PAIRS)
    failure_rate = verification_failure_rate_pct(list(store.verification_outcomes))

    avg_latency = None
    if store.processing_latencies_ms:
        avg_latency = round(sum(store.processing_latencies_ms) / len(store.processing_latencies_ms), 2)

    return EvaluationMetrics(
        raw_alerts_24h=None,
        correlated_incidents=None,
        notification_reduction_pct=None,
        incident_recall_pct=pair_metrics.recall_pct,
        grouping_precision_pct=pair_metrics.precision_pct,
        alert_pair_precision_pct=pair_metrics.precision_pct,
        alert_pair_recall_pct=pair_metrics.recall_pct,
        verification_failure_rate_pct=failure_rate,
        avg_processing_latency_ms=avg_latency,
        is_demo_data=True,
        evaluated_on="synthetic_fixture_ground_truth",
    )
