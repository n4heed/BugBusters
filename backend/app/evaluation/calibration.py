"""Correlation threshold calibration (milestone 3, section 8).

Decision: the threshold is NOT calibrated in this milestone, and
GROUPING_THRESHOLD stays at its existing value (see
services/correlation.py). Reason: a real calibration procedure needs a
labelled VALIDATION set distinct from the labelled TEST set used to report
results. The only labelled data that exists anywhere in this project is
the 6-alert deterministic replay scenario (fixtures_pipeline.py) — the
same scenario every test and baseline in this milestone already reports
results against. Tuning the threshold against it and then reporting the
resulting precision/recall as if unbiased would be exactly the test-set
leakage the milestone explicitly prohibits ("Do not tune thresholds
against the final test labels and then report the resulting score as an
unbiased test result").

If a genuinely separate validation set is supplied later (see
dataset_loader.py's Mode B interface), `run_threshold_sweep` below can be
used to examine the precision/recall/false-merge/missed-incident trade-off
at different thresholds — it is implemented and tested, just not invoked
against the test data for a final decision.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..orchestrator import Orchestrator
from ..services import correlation
from ..store import Store
from .ground_truth import TRUE_PAIRS
from .metrics import alert_pairs, alert_pair_precision_recall

CURRENT_THRESHOLD = correlation.GROUPING_THRESHOLD
CALIBRATION_STATUS = "NOT_CALIBRATED"
CALIBRATION_REASON = (
    "No validation split distinct from the only labelled test scenario exists in this project. "
    "Tuning against that scenario and reporting the result would be test-set leakage. "
    "The existing threshold is preserved unchanged."
)


@dataclass(frozen=True)
class ThresholdSweepPoint:
    threshold: float
    precision_pct: float | None
    recall_pct: float | None
    false_positive_pairs: int
    false_negative_pairs: int


def run_threshold_sweep(raw_alerts: list[dict], thresholds: list[float]) -> list[ThresholdSweepPoint]:
    """Diagnostic only — NOT used to select a threshold in this milestone.
    Runs the given alert set through a fresh pipeline at each candidate
    threshold and reports the precision/recall/false-merge/missed-incident
    trade-off, so the trade-off is at least visible and reproducible even
    though no change is made based on it.
    """
    points: list[ThresholdSweepPoint] = []
    original = correlation.GROUPING_THRESHOLD
    try:
        for t in thresholds:
            correlation.GROUPING_THRESHOLD = t
            store = Store()
            orchestrator = Orchestrator(store)
            universe = {a["id"] for a in raw_alerts}
            for raw in raw_alerts:
                orchestrator.process_alert(dict(raw))
            incidents = [{a.id for a in g if a.id in universe} for g in store.candidate_groups.values()]
            incidents = [g for g in incidents if g]
            predicted = alert_pairs(incidents)
            m = alert_pair_precision_recall(predicted, TRUE_PAIRS)
            points.append(
                ThresholdSweepPoint(
                    threshold=t,
                    precision_pct=m.precision_pct,
                    recall_pct=m.recall_pct,
                    false_positive_pairs=m.false_positives,
                    false_negative_pairs=m.false_negatives,
                )
            )
    finally:
        correlation.GROUPING_THRESHOLD = original
    return points
