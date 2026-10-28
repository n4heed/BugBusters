"""Builds the bounded, redacted EvidencePackage sent to a model — and only
a model; the deterministic agents use services/evidence_retrieval.py
directly and never go through this module.

Three safeguards live here, all required by the milestone spec:

1. Bounding: at most `max_alerts` alerts and `max_historical_cases` cases
   are ever included; omitted counts are recorded, never silently dropped.
2. Redaction: EvidenceAlertRef has no raw host/IP/customer fields at all —
   there's nothing to accidentally leak, by construction.
3. Ground-truth protection: a historical case is only offered as a
   reference if it was approved strictly before the earliest alert in the
   CURRENT candidate group — so a model investigating group G can never be
   handed G's own eventual human-approved outcome as a "prior example".
"""
from __future__ import annotations

from ..models import Alert, ServiceDependency
from .schemas import EvidenceAlertRef, EvidenceHistoricalCaseRef, EvidencePackage


def build_evidence_package(
    candidate_group_id: str,
    group_alerts: list[Alert],
    dependencies: list[ServiceDependency],
    historical_cases: list[dict],
    max_alerts: int,
    max_historical_cases: int,
) -> EvidencePackage:
    services = {a.service for a in group_alerts}

    ordered_alerts = sorted(group_alerts, key=lambda a: a.timestamp)
    selected_alerts = ordered_alerts[:max_alerts]
    omitted_alert_count = max(0, len(ordered_alerts) - len(selected_alerts))

    relevant_edges = [
        f"{d.service} -> {d.depends_on}" for d in dependencies if d.service in services and d.depends_on in services
    ]

    earliest = min((a.timestamp for a in group_alerts), default=None)
    eligible_cases = (
        [c for c in historical_cases if earliest is not None and c["last_seen"] < earliest] if historical_cases else []
    )
    eligible_cases.sort(key=lambda c: c["last_seen"], reverse=True)
    selected_cases = eligible_cases[:max_historical_cases]
    omitted_historical_count = max(0, len(eligible_cases) - len(selected_cases))

    return EvidencePackage(
        candidate_group_id=candidate_group_id,
        alerts=[
            EvidenceAlertRef(
                id=a.id,
                timestamp=a.timestamp.isoformat(),
                service=a.service,
                signal=a.signal,
                severity=str(a.severity),
            )
            for a in selected_alerts
        ],
        omitted_alert_count=omitted_alert_count,
        dependency_edges=relevant_edges,
        historical_cases=[
            EvidenceHistoricalCaseRef(id=c["id"], title=c["title"], origin_hypothesis=c["origin_hypothesis"])
            for c in selected_cases
        ],
        omitted_historical_case_count=omitted_historical_count,
    )
