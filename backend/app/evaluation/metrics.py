"""Correctly-defined evaluation metrics.

Every function here documents its unit, its denominator, and what it
returns when that denominator is zero — `None` ("not applicable"), never a
fabricated 0% or 100%. These are pure functions: no agent, no store, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Optional


def alert_pairs(groups: list[set[str]]) -> set[frozenset[str]]:
    """All within-group alert pairs across a list of groups (incidents)."""
    pairs: set[frozenset[str]] = set()
    for group in groups:
        for a, b in combinations(sorted(group), 2):
            pairs.add(frozenset({a, b}))
    return pairs


@dataclass(frozen=True)
class PairMetrics:
    precision_pct: Optional[float]
    recall_pct: Optional[float]
    true_positives: int
    false_positives: int
    false_negatives: int


def alert_pair_precision_recall(predicted: set[frozenset[str]], truth: set[frozenset[str]]) -> PairMetrics:
    """Unit: a pair of alert IDs. Positive = ground truth says same incident.

    Precision = TP / (TP + FP), over PREDICTED pairs — undefined (None) if
    the system predicted zero pairs (nothing to be precise about).
    Recall = TP / (TP + FN), over TRUE pairs — undefined (None) if there are
    zero true pairs in this evaluation scope.
    """
    tp = len(predicted & truth)
    fp = len(predicted - truth)
    fn = len(truth - predicted)
    precision = round(tp / (tp + fp) * 100, 1) if (tp + fp) > 0 else None
    recall = round(tp / (tp + fn) * 100, 1) if (tp + fn) > 0 else None
    return PairMetrics(precision, recall, tp, fp, fn)


@dataclass(frozen=True)
class IncidentMatch:
    true_incident: frozenset[str]
    matched_predicted: Optional[frozenset[str]]
    match_kind: str  # "exact" | "partial_superset" | "partial_subset" | "none"


def incident_level_recall(
    predicted_incidents: list[set[str]], true_incidents: list[set[str]]
) -> tuple[Optional[float], list[IncidentMatch]]:
    """Unit: one ground-truth incident. Matching criterion (explicit, not
    "any shared alert"): a true incident counts as surfaced only if some
    predicted group is an EXACT match of its alert-ID set. A predicted
    group that is a strict superset (merged in extra unrelated alerts) or
    strict subset (split the incident) is recorded as a partial match in
    the detail list, but does NOT count toward recall — those are false
    merges / split incidents, tracked separately in error analysis, not
    silently credited as a successful recall.

    Returns (recall_pct_or_None_if_no_true_incidents, per-incident detail).
    """
    details: list[IncidentMatch] = []
    hits = 0
    predicted_sets = [frozenset(p) for p in predicted_incidents]

    for true_inc in true_incidents:
        true_fs = frozenset(true_inc)
        exact = next((p for p in predicted_sets if p == true_fs), None)
        if exact is not None:
            details.append(IncidentMatch(true_fs, exact, "exact"))
            hits += 1
            continue
        superset = next((p for p in predicted_sets if true_fs < p), None)
        if superset is not None:
            details.append(IncidentMatch(true_fs, superset, "partial_superset"))
            continue
        subset = next((p for p in predicted_sets if p < true_fs and p), None)
        if subset is not None:
            details.append(IncidentMatch(true_fs, subset, "partial_subset"))
            continue
        details.append(IncidentMatch(true_fs, None, "none"))

    recall = round(hits / len(true_incidents) * 100, 1) if true_incidents else None
    return recall, details


def notification_reduction_pct(raw_alert_count: int, notification_count: int) -> Optional[float]:
    """Definition (documented once here, reused everywhere): a
    "notification" is one incident-level investigation card OR one
    independently-surfaced safety alert OR one standalone/unclustered
    alert the product would actually show on its own — NOT a tuning
    recommendation (those are review summaries, not paging notifications).
    Duplicate alerts collapse into their original's existing notification
    and do not add a new one.

    None if raw_alert_count is 0 (nothing to reduce from).
    """
    if raw_alert_count <= 0:
        return None
    return round((1 - notification_count / raw_alert_count) * 100, 1)


def verification_failure_rate_pct(outcomes: list[str]) -> Optional[float]:
    """Unit: one verification call. None if zero verification calls occurred."""
    if not outcomes:
        return None
    failures = sum(1 for o in outcomes if o == "rejected")
    return round(failures / len(outcomes) * 100, 1)


def percentage(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100, 1)
