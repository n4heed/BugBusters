"""In-memory application state for the hackathon demo.

Not a real database: state resets on process restart. Holds the seeded
synthetic fixtures plus whatever approvals/rejections happen during a run,
and the SEA memory audit log those approvals produce.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from . import fixtures
from .models import (
    Alert,
    ApprovalResult,
    Incident,
    IncidentStatus,
    RecommendationStatus,
    SeaMemoryEvent,
    ServiceDependency,
    TuningRecommendation,
)
from .schemas.agent_results import ProcessingResult

# "Chronic noise" tuning recommendations: live alerts (from any ingestion
# path — the demo simulation, a dataset replay, or direct REST ingest) are
# grouped by fingerprint (same identity used for duplicate detection — see
# services/normalization.compute_fingerprint) and tracked incrementally, not
# by rescanning the whole alert set on every arrival (that O(n^2) mistake
# was already made once this project and is not being repeated here).
TUNING_RECURRING_THRESHOLD = 3
TUNING_WINDOW_HOURS = 24


class Store:
    def __init__(self, seed_demo_fixtures: bool = True) -> None:
        self.seed_demo_fixtures = seed_demo_fixtures
        self.incidents: dict[str, Incident] = {}
        self.safety_queue_alerts: list[Alert] = []
        self.tuning: dict[str, TuningRecommendation] = {}
        self.memory_log: list[SeaMemoryEvent] = []
        self.raw_alert_count = 0

        # Pipeline state (Triage -> Investigation -> Verification).
        self.all_alerts: dict[str, Alert] = {}
        self.candidate_groups: dict[str, list[Alert]] = {}
        self.processing_results: dict[str, ProcessingResult] = {}
        self.dependencies: list[ServiceDependency] = []
        self._group_counter = 0
        self.processing_latencies_ms: list[float] = []
        self.verification_outcomes: list[str] = []
        self.last_evaluation = None
        self.last_replay_alert_ids: list[str] = []
        self.rule_occurrences: dict[str, list[Alert]] = {}

        self.reset()

    def reset(self) -> None:
        if not self.seed_demo_fixtures:
            # A dataset-backed store (see dataset_registry.py) starts
            # genuinely empty: no synthetic incidents, no synthetic safety
            # queue alert, no synthetic tuning recommendations. Imported
            # data populates it explicitly via the ingestion layer.
            self.incidents = {}
            self.safety_queue_alerts = []
            self.tuning = {}
            self.memory_log = []
            self.raw_alert_count = 0
            self.all_alerts = {}
            self.candidate_groups = {}
            self.processing_results = {}
            self.dependencies = []
            self._group_counter = 0
            self.processing_latencies_ms = []
            self.verification_outcomes = []
            self.last_evaluation = None
            self.last_replay_alert_ids = []
            self.rule_occurrences = {}
            return

        incidents = [fixtures.build_incident_a(), fixtures.build_incident_b(), fixtures.build_incident_c()]
        self.incidents = {i.id: i for i in incidents}
        self.safety_queue_alerts = [fixtures.build_safety_queue_alert()]
        tuning = fixtures.build_tuning_recommendations()
        self.tuning = {t.rule_id: t for t in tuning}
        self.memory_log = []
        self.raw_alert_count = fixtures.total_raw_alert_count()

        self.all_alerts = {}
        for incident in incidents:
            for a in incident.related_alerts:
                self.all_alerts[a.id] = a
        for a in self.safety_queue_alerts:
            self.all_alerts[a.id] = a

        self.candidate_groups = {}
        self.processing_results = {}
        self._group_counter = 0
        self.processing_latencies_ms = []
        self.verification_outcomes = []
        self.last_evaluation = None
        self.last_replay_alert_ids = []
        self.rule_occurrences = {}

        seen_edges: set[tuple[str, str]] = set()
        self.dependencies = []
        for incident in incidents:
            for dep in incident.dependencies:
                key = (dep.service, dep.depends_on)
                if key not in seen_edges:
                    seen_edges.add(key)
                    self.dependencies.append(dep)

    # ---- pipeline support -------------------------------------------------

    def known_alert_ids(self) -> set[str]:
        return set(self.all_alerts.keys())

    def register_alert(self, alert: Alert) -> None:
        self.all_alerts[alert.id] = alert
        self.raw_alert_count += 1
        self._track_rule_occurrence(alert)

    # ---- chronic-noise tuning recommendations ------------------------------

    @staticmethod
    def _derive_rule_name(signal: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9]+", "_", signal.strip()).strip("_").upper()
        return (slug or "UNNAMED_RULE")[:40]

    def _track_rule_occurrence(self, alert: Alert) -> None:
        """Incrementally updates the chronic-noise tuning view for this
        alert's rule group (identified by fingerprint — the same identity
        used for duplicate detection). O(group size) per call, NOT O(total
        alerts): only this one rule's own occurrence list is touched, never
        the full alert set.
        """
        if not alert.fingerprint:
            return
        occurrences = self.rule_occurrences.setdefault(alert.fingerprint, [])
        occurrences.append(alert)

        newest = max(a.timestamp for a in occurrences)
        cutoff = newest - timedelta(hours=TUNING_WINDOW_HOURS)
        occurrences[:] = [a for a in occurrences if a.timestamp >= cutoff]
        self.rule_occurrences[alert.fingerprint] = occurrences

        existing = self.tuning.get(f"LIVE-{alert.fingerprint[:10].upper()}")
        if existing is None and len(occurrences) < TUNING_RECURRING_THRESHOLD:
            return  # not yet "recurring" enough to surface as a new recommendation

        historical_links = sum(
            1
            for a in occurrences
            if a.incident_id is not None
            and (inc := self.incidents.get(a.incident_id)) is not None
            and inc.status == IncidentStatus.approved
        )

        rule_id = f"LIVE-{alert.fingerprint[:10].upper()}"
        rule_name = self._derive_rule_name(alert.signal)
        suggested_action = (
            f"Review {rule_name} on {alert.service}: occurred {len(occurrences)} times in the last "
            f"{TUNING_WINDOW_HOURS}h with no confirmed historical incident link."
            if historical_links == 0
            else f"Review {rule_name} on {alert.service}: occurred {len(occurrences)} times in the last "
            f"{TUNING_WINDOW_HOURS}h; {historical_links} occurrence(s) linked to an approved incident pattern."
        )

        prior = self.tuning.get(rule_id)
        self.tuning[rule_id] = TuningRecommendation(
            rule_id=rule_id,
            service=alert.service,
            rule_name=rule_name,
            count_24h=len(occurrences),
            historical_links=historical_links,
            suggested_action=suggested_action,
            # A human's prior approve/reject decision on this rule is never
            # silently reset just because another matching alert arrived.
            status=prior.status if prior is not None else RecommendationStatus.pending,
        )

    def recent_alerts(self, window_minutes: int = 120) -> list[Alert]:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        # Fixture alerts carry fixed 2026-10-28 timestamps outside any "now"
        # window, so recency is evaluated relative to the newest alert seen
        # rather than wall-clock time, to keep dedup meaningful in replay.
        if not self.all_alerts:
            return []
        newest = max(a.timestamp for a in self.all_alerts.values())
        cutoff = newest - timedelta(minutes=window_minutes)
        return [a for a in self.all_alerts.values() if a.timestamp >= cutoff]

    def new_candidate_group_id(self) -> str:
        self._group_counter += 1
        return f"CAND-{self._group_counter:04d}"

    def start_candidate_group(self, alert: Alert) -> str:
        group_id = self.new_candidate_group_id()
        self.candidate_groups[group_id] = [alert]
        return group_id

    def add_to_candidate_group(self, group_id: str, alert: Alert) -> None:
        self.candidate_groups.setdefault(group_id, []).append(alert)

    def historical_pattern_titles(self) -> list[str]:
        titles = [i.title for i in self.incidents.values() if i.status == IncidentStatus.approved]
        titles += [e.pattern for e in self.memory_log if e.pattern]
        return titles

    def historical_pattern_cases(self) -> list[dict]:
        """Human-approved incidents as candidate historical reference cases,
        for the AI-assisted investigation path. Plain dicts (not the ai
        package's typed schema) to keep store.py independent of app.ai —
        the caller (ai/evidence.py) converts and applies the ground-truth
        time cutoff before anything reaches a model.
        """
        return [
            {
                "id": i.id,
                "title": i.title,
                "origin_hypothesis": i.origin_hypothesis,
                "last_seen": i.last_seen,
            }
            for i in self.incidents.values()
            if i.status == IncidentStatus.approved
        ]

    def add_to_safety_queue(self, alert: Alert, reason: str) -> None:
        alert.is_safety_queue = True
        alert.safety_reason = reason
        self.safety_queue_alerts.append(alert)

    def remove_from_safety_queue(self, alert_id: str) -> Alert | None:
        for i, a in enumerate(self.safety_queue_alerts):
            if a.id == alert_id:
                removed = self.safety_queue_alerts.pop(i)
                removed.is_safety_queue = False
                removed.safety_reason = None
                return removed
        return None

    def record_processing_result(self, result: ProcessingResult) -> None:
        self.processing_results[result.alert_id] = result
        if result.processing_ms is not None:
            self.processing_latencies_ms.append(result.processing_ms)
        if result.verification is not None:
            self.verification_outcomes.append(result.verification.outcome)

    def correct_processing_result(self, result: ProcessingResult) -> None:
        """Phase 3 fix: when an existing group gains a new member, every
        OTHER member's own Alert.incident_id/workflow_status can change
        too (see orchestrator._run_pipeline) — but that member's own
        ProcessingResult was already returned and stored when IT was
        originally processed, and would otherwise go stale (still showing
        incident_id=None after the Alert itself now has one). This
        replaces the dict entry only, deliberately NOT appending to
        processing_latencies_ms / verification_outcomes again — this is a
        consistency correction for an alert that was already counted once
        when it was actually processed, not a second processing event.
        """
        self.processing_results[result.alert_id] = result

    def upsert_incident_from_group(
        self,
        group_id: str,
        group_alerts: list[Alert],
        investigation,
        verification,
        workflow_status: str,
    ) -> Incident:
        from .models import Evidence, SafetyDisclosure

        existing = self.incidents.get(group_id)
        affected_services = sorted({a.service for a in group_alerts})
        origin = next(
            (s for s in affected_services if any(s in e for e in investigation.derived_relationships)),
            affected_services[0] if affected_services else "unknown",
        )
        for a in group_alerts:
            a.incident_id = group_id

        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unknown": 4}
        top_severity = min((a.severity for a in group_alerts), key=lambda s: severity_order.get(s, 4))

        incident = Incident(
            id=group_id,
            title=f"Candidate Grouping via {origin}",
            severity=top_severity,
            origin_hypothesis=origin,
            first_seen=min(a.timestamp for a in group_alerts),
            last_seen=max(a.timestamp for a in group_alerts),
            affected_services=affected_services,
            status=existing.status if existing else IncidentStatus.pending_review,
            related_alerts=group_alerts,
            dependencies=[d for d in self.dependencies if d.service in affected_services and d.depends_on in affected_services],
            evidence=Evidence(
                narrative=investigation.probable_cause_hypothesis or "Insufficient evidence to form a hypothesis.",
                overlapping_time_windows=[],
                supporting_dependencies=investigation.derived_relationships,
                supporting_metrics=investigation.observed_facts,
                missing_or_contradictory=investigation.missing_or_contradictory,
                similar_historical_incidents=investigation.historical_similarities,
                unresolved_uncertainty=investigation.unresolved_unknowns,
            ),
            safety=SafetyDisclosure(
                confidence_label="DEMO ESTIMATE — NOT CALIBRATED",
                confidence_value=None,
                evidence_completeness="partial" if verification.outcome == "verified" else "insufficient",
                topology_bias_note="Origin hypothesis is derived only from the modeled dependency topology supplied to this demo.",
                matches_confirmed_pattern=bool(investigation.historical_similarities),
                weak_support_warning=None if verification.outcome == "verified" else "; ".join(verification.reasons) or None,
                missing_evidence_warning="; ".join(verification.missing_evidence) or None,
            ),
            workflow_status=workflow_status,
            is_candidate=workflow_status != "VERIFIED",
            ai_status=investigation.ai_status,
            ai_reason=investigation.ai_reason,
            ai_model_used=investigation.model_used,
            ai_alerts_considered=investigation.ai_alerts_considered,
            ai_historical_cases_considered=investigation.ai_historical_cases_considered,
        )
        self.incidents[group_id] = incident
        return incident

    def list_incidents(self) -> list[Incident]:
        return list(self.incidents.values())

    def get_incident(self, incident_id: str) -> Incident | None:
        return self.incidents.get(incident_id)

    def approve_incident(self, incident_id: str) -> ApprovalResult:
        incident = self.incidents.get(incident_id)
        if incident is None:
            return ApprovalResult(success=False, demo_only=True, message=f"Unknown incident {incident_id}")
        incident.status = IncidentStatus.approved
        event = SeaMemoryEvent(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            event_type="INCIDENT_PATTERN_APPROVED",
            entity_id=incident.id,
            decision="Human-approved grouping",
            pattern=incident.title,
            status="DEMO MEMORY ENTRY",
        )
        self.memory_log.append(event)
        return ApprovalResult(
            success=True,
            demo_only=True,
            message="APPROVED IN DEMO — grouping saved as a verified pattern. Not applied to production systems.",
            memory_event=event,
        )

    def approve_tuning(self, rule_id: str) -> ApprovalResult:
        rec = self.tuning.get(rule_id)
        if rec is None:
            return ApprovalResult(success=False, demo_only=True, message=f"Unknown rule {rule_id}")
        rec.status = RecommendationStatus.approved
        event = SeaMemoryEvent(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            event_type="TUNING_RECOMMENDATION_APPROVED",
            entity_id=rec.rule_id,
            decision="Recommendation approved for review",
            pattern=None,
            status="DEMO MEMORY ENTRY",
        )
        self.memory_log.append(event)
        return ApprovalResult(
            success=True,
            demo_only=True,
            message="APPROVED IN DEMO — NOT APPLIED TO PRODUCTION. No alert was muted or suppressed.",
            memory_event=event,
        )

    def reject_tuning(self, rule_id: str) -> ApprovalResult:
        rec = self.tuning.get(rule_id)
        if rec is None:
            return ApprovalResult(success=False, demo_only=True, message=f"Unknown rule {rule_id}")
        rec.status = RecommendationStatus.rejected
        event = SeaMemoryEvent(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            event_type="TUNING_RECOMMENDATION_REJECTED",
            entity_id=rec.rule_id,
            decision="Recommendation rejected on review",
            pattern=None,
            status="DEMO MEMORY ENTRY",
        )
        self.memory_log.append(event)
        return ApprovalResult(success=True, demo_only=True, message="Recommendation rejected.", memory_event=event)


store = Store()
