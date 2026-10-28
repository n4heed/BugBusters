"""Deterministic, explainable correlation scoring.

This is intentionally a transparent weighted-rule scorer, not a black box.
Weights and the grouping threshold are module-level constants so they are
easy to find, tune and test. The resulting `raw_score` is a heuristic sum
of matched signal weights — it is NOT a calibrated probability and must
never be presented to a user as one.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from ..models import Alert, ServiceDependency
from ..schemas.agent_results import CorrelationSignal

TEMPORAL_WINDOW_MINUTES = 10
TEMPORAL_WEIGHT = 0.30
SHARED_SERVICE_WEIGHT = 0.25
DEPENDENCY_PATH_WEIGHT = 0.30
FINGERPRINT_WEIGHT = 0.15

GROUPING_THRESHOLD = 0.50

# Phase 3 fix: temporal_proximity and shared_service are deliberately weak,
# generic signals — "roughly the same time" and "the same service name" —
# and must never be sufficient ON THEIR OWN to form a group, even though
# their weights alone (0.30 + 0.25 = 0.55) already clear GROUPING_THRESHOLD.
# A real incident group requires at least one STRONG signal: a genuine
# causal relationship (dependency_path) or a matching event identity
# (fingerprint_similarity). See `_has_corroborating_evidence` and
# `correlate()` below for where this is enforced, and
# tests/test_phase3_disposition.py::test_shared_service_and_severity_alone_do_not_group
# for the regression this fixes.
_STRONG_SIGNAL_TYPES = frozenset({"dependency_path", "fingerprint_similarity"})


def _build_adjacency(dependencies: list[ServiceDependency]) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {}
    for dep in dependencies:
        adjacency.setdefault(dep.service, set()).add(dep.depends_on)
        adjacency.setdefault(dep.depends_on, set()).add(dep.service)
    return adjacency


def score_against_group(
    alert: Alert,
    group_alerts: list[Alert],
    adjacency: dict[str, set[str]],
    window_minutes: int = TEMPORAL_WINDOW_MINUTES,
) -> tuple[list[CorrelationSignal], float]:
    signals: list[CorrelationSignal] = []

    window = timedelta(minutes=window_minutes)
    temporal_match = any(abs(alert.timestamp - g.timestamp) <= window for g in group_alerts)
    signals.append(
        CorrelationSignal(
            signal_type="temporal_proximity",
            description=f"Within {window_minutes} minutes of an existing alert in this group"
            if temporal_match
            else f"No group alert falls within {window_minutes} minutes",
            weight=TEMPORAL_WEIGHT,
            matched=temporal_match,
        )
    )

    group_services = {g.service for g in group_alerts}
    shared_service_match = alert.service in group_services
    signals.append(
        CorrelationSignal(
            signal_type="shared_service",
            description=f"Alert service '{alert.service}' already present in this group"
            if shared_service_match
            else f"Alert service '{alert.service}' not yet seen in this group",
            weight=SHARED_SERVICE_WEIGHT,
            matched=shared_service_match,
        )
    )

    neighbors = adjacency.get(alert.service, set())
    dependency_match = bool(neighbors & group_services)
    signals.append(
        CorrelationSignal(
            signal_type="dependency_path",
            description=f"'{alert.service}' has a modeled dependency edge to a service in this group"
            if dependency_match
            else f"No modeled dependency edge between '{alert.service}' and this group's services",
            weight=DEPENDENCY_PATH_WEIGHT,
            matched=dependency_match,
        )
    )

    fingerprint_match = alert.fingerprint is not None and any(
        g.fingerprint == alert.fingerprint for g in group_alerts
    )
    signals.append(
        CorrelationSignal(
            signal_type="fingerprint_similarity",
            description="Matches the alert fingerprint (service+signal) of a group member"
            if fingerprint_match
            else "No matching alert fingerprint in this group",
            weight=FINGERPRINT_WEIGHT,
            matched=fingerprint_match,
        )
    )

    raw_score = sum(s.weight for s in signals if s.matched)
    return signals, round(raw_score, 4)


def _has_corroborating_evidence(signals: list[CorrelationSignal]) -> bool:
    """True only if a STRONG signal (dependency_path or
    fingerprint_similarity) matched — see GROUPING_THRESHOLD's comment
    above for why temporal_proximity + shared_service can never satisfy
    this on their own, regardless of their combined weight.
    """
    return any(s.matched and s.signal_type in _STRONG_SIGNAL_TYPES for s in signals)


def correlate(
    alert: Alert,
    candidate_groups: dict[str, list[Alert]],
    dependencies: list[ServiceDependency],
    window_minutes: int = TEMPORAL_WINDOW_MINUTES,
    threshold: float = GROUPING_THRESHOLD,
) -> tuple[Optional[str], list[CorrelationSignal], float]:
    """Score `alert` against every open candidate group and return the best
    VALID match — one that both clears `threshold` AND has corroborating
    strong evidence (see `_has_corroborating_evidence`).

    Returns (group_id_or_None, signals_for_best_candidate, best_score).
    group_id is None when no group qualifies. In that case the returned
    signals/score are still the actual overall best-scoring candidate
    (even if it failed the corroboration gate) — callers display these for
    explainability (e.g. "best candidate score 0.55 did not clear..."),
    so an alert that was correctly rejected for lacking strong evidence
    still gets an honest, non-zero explanation rather than a blank one.
    """
    adjacency = _build_adjacency(dependencies)

    best_group_id: Optional[str] = None
    best_signals: list[CorrelationSignal] = []
    best_score = 0.0
    overall_best_signals: list[CorrelationSignal] = []
    overall_best_score = 0.0

    for group_id, group_alerts in candidate_groups.items():
        signals, score = score_against_group(alert, group_alerts, adjacency, window_minutes)
        if score > overall_best_score:
            overall_best_signals = signals
            overall_best_score = score
        if score >= threshold and score > best_score and _has_corroborating_evidence(signals):
            best_group_id = group_id
            best_signals = signals
            best_score = score

    if best_group_id is not None:
        return best_group_id, best_signals, best_score
    return None, overall_best_signals, overall_best_score
