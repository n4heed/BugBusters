"""Orchestrator: runs the Triage -> Investigation -> Verification workflow
for a single incoming alert and keeps the shared store consistent.

Safety invariant this module exists to protect: an alert is registered in
the store (`store.register_alert`) BEFORE any agent runs. Every branch
below — duplicate, unclustered, grouped-but-rejected, or an outright
exception — leaves that record in place. Nothing in this file deletes an
alert; only a human approval (see store.approve_incident) ever changes an
incident's human-facing status, and even that doesn't remove source
alerts.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from .agents.investigation_agent import InvestigationAgent
from .agents.triage_agent import TriageAgent
from .agents.verification_agent import VerificationAgent
from .ai.budget import ModelCallBudget
from .ai.config import load_ai_config_safe
from .ai.provider import get_configured_provider
from .ai.usage_tracker import UsageTracker
from .models import Alert, AlertDisposition, AlertSeverity
from .redaction import redact_customer, redact_host, redact_ip
from .schemas.agent_results import ProcessingResult, WorkflowStatus
from .services.normalization import NormalizationError, compute_fingerprint, normalize_alert
from .store import Store, store as _default_store

_VERIFICATION_TO_WORKFLOW = {
    "verified": WorkflowStatus.VERIFIED,
    "pending": WorkflowStatus.PENDING_HUMAN_REVIEW,
    "rejected": WorkflowStatus.PENDING_HUMAN_REVIEW,
}

_VERIFICATION_TO_INCIDENT_STATUS = {
    "verified": "VERIFIED",
    "pending": "PENDING_HUMAN_REVIEW",
    "rejected": "REJECTED",
}


class Orchestrator:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.triage_agent = TriageAgent()
        self.ai_config = load_ai_config_safe()
        self.ai_budget = ModelCallBudget(max_calls=self.ai_config.max_calls_per_run)
        self.ai_usage = UsageTracker()
        self.investigation_agent = InvestigationAgent(
            provider=get_configured_provider(self.ai_config),
            ai_config=self.ai_config,
            budget=self.ai_budget,
            usage_tracker=self.ai_usage,
        )
        self.verification_agent = VerificationAgent()

    def process_alert(
        self, raw: dict, is_synthetic: bool = False, operator_requested_ai: bool = False
    ) -> ProcessingResult:
        start = time.perf_counter()

        try:
            alert = normalize_alert(raw, is_synthetic=is_synthetic)
        except NormalizationError as exc:
            return self._handle_malformed(raw, exc, is_synthetic, start)

        # Idempotency: a raw payload carrying an ID we've already fully
        # processed is not reprocessed — return the cached result instead
        # of creating a second candidate group or duplicate incident state.
        cached = self.store.processing_results.get(alert.id)
        if cached is not None:
            return cached

        self.store.register_alert(alert)

        try:
            result = self._run_pipeline(alert, is_synthetic, start, operator_requested_ai)
        except Exception as exc:  # noqa: BLE001 - must not crash the ingestion path
            result = ProcessingResult(
                alert_id=alert.id,
                workflow_status=WorkflowStatus.PROCESSING_ERROR,
                is_synthetic=is_synthetic,
                error=f"{type(exc).__name__}: {exc}",
                processing_ms=round((time.perf_counter() - start) * 1000, 3),
            )

        self.store.record_processing_result(result)
        return result

    def _run_pipeline(
        self, alert: Alert, is_synthetic: bool, start: float, operator_requested_ai: bool = False
    ) -> ProcessingResult:
        triage = self.triage_agent.process(
            alert,
            self.store.candidate_groups,
            self.store.dependencies,
            self.store.recent_alerts(),
            self.store.safety_queue_alerts,
        )

        if triage.decision == "duplicate":
            # Phase 3 fix: this alert IS a confirmed duplicate — its
            # disposition, workflow_status, AND ProcessingResult.
            # workflow_status must all agree on that. Previously the
            # ProcessingResult reported UNCLUSTERED here despite
            # Alert.workflow_status correctly saying "DUPLICATE",
            # contradicting the alert's own recorded disposition.
            alert.workflow_status = "DUPLICATE"
            alert.duplicate_of = triage.duplicate_of
            alert.disposition = AlertDisposition.redundant
            return ProcessingResult(
                alert_id=alert.id,
                workflow_status=WorkflowStatus.DUPLICATE,
                triage=triage,
                is_synthetic=is_synthetic,
                disposition="redundant",
                duplicate_of=triage.duplicate_of,
                processing_ms=round((time.perf_counter() - start) * 1000, 3),
            )

        if triage.decision == "unclustered":
            alert.workflow_status = "UNCLUSTERED"
            alert.disposition = AlertDisposition.unique
            reason = (
                f"No sufficiently supported grouping found "
                f"(best correlation score {triage.raw_score:.2f})."
            )
            self.store.add_to_safety_queue(alert, reason)
            return ProcessingResult(
                alert_id=alert.id,
                workflow_status=WorkflowStatus.UNCLUSTERED,
                triage=triage,
                is_synthetic=is_synthetic,
                disposition="unique",
                processing_ms=round((time.perf_counter() - start) * 1000, 3),
            )

        # triage.decision == "grouped"
        if triage.candidate_group_id is not None:
            group_id = triage.candidate_group_id
            self.store.add_to_candidate_group(group_id, alert)
        else:
            # Bootstrap: form a brand-new group from a previously-unclustered
            # seed alert plus this one.
            assert triage.seed_alert_id is not None
            seed_alert = self.store.remove_from_safety_queue(triage.seed_alert_id)
            if seed_alert is None:
                # Defensive (Phase 3, section 4D): the seed vanished between
                # triage scoring it and this bootstrap step. This should
                # not happen in the current single-threaded flow, but a
                # meaningless single-member "group" must never be created
                # either way — fall back to leaving this alert standalone
                # rather than fabricating a group of one.
                alert.workflow_status = "UNCLUSTERED"
                alert.disposition = AlertDisposition.unique
                reason = "Correlated seed alert was no longer available when forming the group."
                self.store.add_to_safety_queue(alert, reason)
                return ProcessingResult(
                    alert_id=alert.id,
                    workflow_status=WorkflowStatus.UNCLUSTERED,
                    triage=triage,
                    is_synthetic=is_synthetic,
                    disposition="unique",
                    processing_ms=round((time.perf_counter() - start) * 1000, 3),
                )
            group_id = self.store.new_candidate_group_id()
            self.store.candidate_groups[group_id] = [seed_alert, alert]
            seed_alert.incident_id = group_id
            seed_alert.workflow_status = "INVESTIGATION_PENDING"
            # seed_alert.disposition is left exactly as triage last set it
            # (normally "unique", from the unclustered branch above) —
            # group membership is never allowed to upgrade or override an
            # alert's own duplicate-detection disposition.

        alert.incident_id = group_id
        alert.workflow_status = "INVESTIGATION_PENDING"
        alert.disposition = AlertDisposition.unique

        group_alerts = self.store.candidate_groups[group_id]
        investigation = self.investigation_agent.investigate(
            group_id,
            group_alerts,
            self.store.dependencies,
            self.store.historical_pattern_titles(),
            triage,
            historical_pattern_cases=self.store.historical_pattern_cases(),
            operator_requested_ai=operator_requested_ai,
        )

        verification = self.verification_agent.verify(
            group_id, group_alerts, self.store.dependencies, investigation, self.store.known_alert_ids()
        )

        incident_workflow_status = _VERIFICATION_TO_INCIDENT_STATUS[verification.outcome]
        self.store.upsert_incident_from_group(
            group_id, group_alerts, investigation, verification, incident_workflow_status
        )
        for a in group_alerts:
            a.workflow_status = incident_workflow_status
            if a.id != alert.id:
                # Phase 3 fix: every OTHER member of this group (e.g. the
                # seed alert this group just bootstrapped from) already had
                # its own ProcessingResult returned and persisted when IT
                # was originally processed — at that time it had no group
                # yet. Without this, that stored result would permanently
                # disagree with the Alert model's now-correct incident_id/
                # workflow_status (the Alert <-> ProcessingResult
                # consistency section 5 requires). The member's own
                # triage/investigation/verification history from when it
                # was actually processed is preserved; only the fields that
                # are now stale are corrected.
                prior = self.store.processing_results.get(a.id)
                if prior is not None:
                    corrected = prior.model_copy(
                        update={
                            "workflow_status": _VERIFICATION_TO_WORKFLOW[verification.outcome],
                            "incident_id": group_id,
                        }
                    )
                    self.store.correct_processing_result(corrected)

        return ProcessingResult(
            alert_id=alert.id,
            workflow_status=_VERIFICATION_TO_WORKFLOW[verification.outcome],
            triage=triage,
            investigation=investigation,
            verification=verification,
            incident_id=group_id,
            is_synthetic=is_synthetic,
            disposition=alert.disposition.value,
            processing_ms=round((time.perf_counter() - start) * 1000, 3),
        )

    def _handle_malformed(
        self, raw: dict, exc: NormalizationError, is_synthetic: bool, start: float
    ) -> ProcessingResult:
        """Schema validation failed. The raw payload is still preserved —
        as an unclustered, explicitly-flagged record — rather than dropped.
        """
        malformed_id = str(raw.get("id") or f"malformed-{uuid.uuid4().hex[:8]}")
        service = str(raw.get("service") or "unknown-service")
        signal = str(raw.get("signal") or f"Schema validation failed: {exc}")

        placeholder = Alert(
            id=malformed_id,
            timestamp=datetime.now(timezone.utc),
            service=service,
            signal=signal,
            severity=AlertSeverity.unknown,
            host_raw=raw.get("host"),
            ip_raw=raw.get("ip"),
            customer_raw=raw.get("customer"),
            host_redacted=redact_host(raw.get("host")),
            ip_redacted=redact_ip(raw.get("ip")),
            customer_redacted=redact_customer(raw.get("customer")),
            incident_id=None,
            is_safety_queue=False,
            safety_reason=None,
            workflow_status="PROCESSING_ERROR",
            duplicate_of=None,
            fingerprint=compute_fingerprint(service, signal),
            is_synthetic=is_synthetic,
            # A schema-invalid record is exactly the "pipeline cannot
            # establish a reliable disposition" case — never silently
            # treated as "unique" or "redundant" (section 2B).
            disposition=AlertDisposition.uncertain,
        )
        self.store.register_alert(placeholder)
        self.store.add_to_safety_queue(placeholder, f"Schema validation failed: {exc}")

        result = ProcessingResult(
            alert_id=malformed_id,
            workflow_status=WorkflowStatus.PROCESSING_ERROR,
            is_synthetic=is_synthetic,
            disposition="uncertain",
            error=str(exc),
            processing_ms=round((time.perf_counter() - start) * 1000, 3),
        )
        self.store.record_processing_result(result)
        return result


orchestrator = Orchestrator(_default_store)
