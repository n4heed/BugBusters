"""KPI metric calculation.

Notification-reduction definition (no official one exists for this challenge):
each correlated incident card plus each independently surfaced safety alert
counts as one actionable notification; reduction is measured against the raw
alert count. Tuning recommendations are summaries for human review, not
immediate paging notifications, so they are not counted as reduced noise.

Incident recall and grouping precision require ground-truth-labelled
evaluation data this demo does not have, so they are reported as
"not measured" rather than fabricated.
"""
from __future__ import annotations

from .models import EvaluationMetrics
from .store import Store


def compute_metrics(store: Store) -> EvaluationMetrics:
    raw = store.raw_alert_count
    correlated_incidents = len(store.incidents)
    safety_alerts = len(store.safety_queue_alerts)
    notifications = correlated_incidents + safety_alerts
    reduction = None
    if raw > 0:
        reduction = round((1 - (notifications / raw)) * 100, 1)

    return EvaluationMetrics(
        raw_alerts_24h=raw,
        correlated_incidents=correlated_incidents,
        notification_reduction_pct=reduction,
        incident_recall_pct=None,
        grouping_precision_pct=None,
        is_demo_data=True,
    )
