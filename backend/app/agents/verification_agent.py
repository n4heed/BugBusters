"""Verification Agent: the last deterministic gate before a candidate
grouping is shown to a human as ready for review. It checks the
Investigation Agent's output against the actual supplied records — it
does not re-derive the hypothesis, and it is not the sole authority that
can remove an alert from the visible feed: a rejection here still leaves
every source alert visible (as a pending/unclustered candidate), it never
deletes them.
"""
from __future__ import annotations

from ..models import Alert, ServiceDependency
from ..schemas.agent_results import InvestigationResult, VerificationResult


class VerificationAgent:
    def verify(
        self,
        candidate_group_id: str,
        group_alerts: list[Alert],
        dependencies: list[ServiceDependency],
        investigation: InvestigationResult,
        known_alert_ids: set[str],
    ) -> VerificationResult:
        reasons: list[str] = []
        missing_evidence: list[str] = []

        group_alert_ids = [a.id for a in group_alerts]
        unknown_ids = [aid for aid in group_alert_ids if aid not in known_alert_ids]
        if unknown_ids:
            return VerificationResult(
                candidate_group_id=candidate_group_id,
                outcome="rejected",
                reasons=[f"Referenced alert ID(s) not found in the store: {', '.join(unknown_ids)}"],
                validated_alert_ids=[],
                missing_evidence=[],
            )

        if investigation.status == "insufficient_evidence":
            return VerificationResult(
                candidate_group_id=candidate_group_id,
                outcome="pending",
                reasons=["Investigation reported insufficient evidence; cannot verify an unformed hypothesis."],
                validated_alert_ids=group_alert_ids,
                missing_evidence=investigation.missing_or_contradictory,
            )

        valid_edges = {f"{d.service} -> {d.depends_on}" for d in dependencies}
        unresolved_edges = [e for e in investigation.derived_relationships if e not in valid_edges]
        if unresolved_edges:
            return VerificationResult(
                candidate_group_id=candidate_group_id,
                outcome="rejected",
                reasons=[f"Referenced dependency edge(s) do not exist in the supplied topology: {', '.join(unresolved_edges)}"],
                validated_alert_ids=group_alert_ids,
                missing_evidence=[],
            )

        if group_alerts:
            first_seen = min(a.timestamp for a in group_alerts)
            last_seen = max(a.timestamp for a in group_alerts)
            if first_seen > last_seen:
                return VerificationResult(
                    candidate_group_id=candidate_group_id,
                    outcome="rejected",
                    reasons=["Internal timestamp inconsistency: first_seen is after last_seen."],
                    validated_alert_ids=group_alert_ids,
                    missing_evidence=[],
                )

        if not investigation.probable_cause_hypothesis:
            missing_evidence.append("No probable-cause hypothesis was produced despite a hypothesis_formed status.")
            return VerificationResult(
                candidate_group_id=candidate_group_id,
                outcome="pending",
                reasons=["Investigation status claims a hypothesis was formed but none is present."],
                validated_alert_ids=group_alert_ids,
                missing_evidence=missing_evidence,
            )

        reasons.append("All referenced alert IDs and dependency edges resolved against supplied records.")
        return VerificationResult(
            candidate_group_id=candidate_group_id,
            outcome="verified",
            reasons=reasons,
            validated_alert_ids=group_alert_ids,
            missing_evidence=investigation.missing_or_contradictory,
        )
