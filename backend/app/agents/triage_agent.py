"""Triage Agent: normalizes have already happened upstream (see
services/normalization.py) by the time this runs. This agent's job is
narrower — decide whether an already-normalized alert is a duplicate of a
recent alert, belongs to an existing candidate group, or must stay
unclustered — and to expose exactly which signals drove that decision.

Grouping is deterministic and rule-based (see services/correlation.py).
Nothing here automatically merges alerts merely because they are close in
time: every grouping decision must clear the documented, weighted
threshold and carries its matched/unmatched signals for inspection.
"""
from __future__ import annotations

from datetime import timedelta

from ..models import Alert, ServiceDependency
from ..schemas.agent_results import TriageResult
from ..services import correlation

# Read via the module (correlation.GROUPING_THRESHOLD), never imported by
# name: `from ..services.correlation import GROUPING_THRESHOLD` would copy
# the value at import time, so a later `correlation.GROUPING_THRESHOLD = x`
# (as evaluation/calibration.py's threshold sweep does) would silently have
# no effect here. This bug was found by actually running the sweep tool.

DUPLICATE_WINDOW_MINUTES = 30


class TriageAgent:
    """Stateless; all state (candidate groups, recent alerts) is passed in
    by the orchestrator so this agent is trivially unit-testable.
    """

    def process(
        self,
        alert: Alert,
        candidate_groups: dict[str, list[Alert]],
        dependencies: list[ServiceDependency],
        recent_alerts: list[Alert],
        safety_queue_alerts: list[Alert],
    ) -> TriageResult:
        duplicate_of = self._find_duplicate(alert, recent_alerts)
        if duplicate_of is not None:
            return TriageResult(
                alert_id=alert.id,
                decision="duplicate",
                duplicate_of=duplicate_of,
                raw_score=1.0,
                uncertainty_flags=[],
            )

        uncertainty_flags: list[str] = []
        if alert.severity == "unknown":
            uncertainty_flags.append("Alert severity could not be determined from the raw payload.")

        # 1. Does this alert belong in an already-formed candidate group?
        group_id, signals, score = correlation.correlate(
            alert,
            candidate_groups,
            dependencies,
            window_minutes=correlation.TEMPORAL_WINDOW_MINUTES,
            threshold=correlation.GROUPING_THRESHOLD,
        )
        if group_id is not None:
            return TriageResult(
                alert_id=alert.id,
                decision="grouped",
                candidate_group_id=group_id,
                evidence_signals=signals,
                raw_score=score,
                uncertainty_flags=uncertainty_flags,
            )

        # 2. No formed group matched. Does it correlate strongly enough with
        # a single previously-unclustered alert to bootstrap a brand-new
        # candidate group? This is how grouping gets off the ground without
        # ever merging alerts "merely because they occur close together" —
        # the same weighted signal scoring and threshold apply here too.
        pseudo_groups = {a.id: [a] for a in safety_queue_alerts if a.id != alert.id and a.fingerprint}
        seed_id, seed_signals, seed_score = correlation.correlate(
            alert,
            pseudo_groups,
            dependencies,
            window_minutes=correlation.TEMPORAL_WINDOW_MINUTES,
            threshold=correlation.GROUPING_THRESHOLD,
        )
        if seed_id is not None:
            return TriageResult(
                alert_id=alert.id,
                decision="grouped",
                seed_alert_id=seed_id,
                evidence_signals=seed_signals,
                raw_score=seed_score,
                uncertainty_flags=uncertainty_flags,
            )

        best_score = max(score, seed_score)
        best_signals = signals if score >= seed_score else seed_signals
        return TriageResult(
            alert_id=alert.id,
            decision="unclustered",
            evidence_signals=best_signals,
            raw_score=best_score,
            uncertainty_flags=uncertainty_flags
            + [
                f"Best candidate score {best_score:.2f} did not clear the "
                f"{correlation.GROUPING_THRESHOLD:.2f} grouping threshold."
            ],
        )

    @staticmethod
    def _find_duplicate(alert: Alert, recent_alerts: list[Alert]) -> str | None:
        if alert.fingerprint is None:
            return None
        window = timedelta(minutes=DUPLICATE_WINDOW_MINUTES)
        for other in recent_alerts:
            if other.id == alert.id:
                continue
            if other.fingerprint == alert.fingerprint and abs(alert.timestamp - other.timestamp) <= window:
                return other.id
        return None
