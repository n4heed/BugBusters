"""Investigation Agent: builds an evidence-grounded probable-cause
hypothesis for a candidate group, or explicitly reports
`insufficient_evidence` instead of fabricating confidence.

The deterministic pass (services/evidence_retrieval.py) ALWAYS runs first.
A model is consulted afterward only when `ai.selection_policy.should_use_ai`
says the case is genuinely ambiguous or an operator explicitly asked for
AI-assisted analysis — never automatically for every alert, and never with
the whole dataset. See `ai/evidence.py` for the bounded, ground-truth-safe
context that gets built if and only if a call is going to happen.
"""
from __future__ import annotations

import time

from ..ai.budget import ModelCallBudget
from ..ai.config import AIConfig
from ..ai.evidence import build_evidence_package
from ..ai.provider import ModelProvider, ProviderCallError
from ..ai.schemas import ModelInvestigationResponse
from ..ai.selection_policy import should_use_ai
from ..ai.usage_tracker import ModelUsageRecord, UsageTracker
from ..models import Alert, ServiceDependency
from ..schemas.agent_results import InvestigationResult, TriageResult
from ..services.evidence_retrieval import gather_evidence

MIN_ALERTS_FOR_HYPOTHESIS = 2


class InvestigationAgent:
    def __init__(
        self,
        provider: ModelProvider | None,
        ai_config: AIConfig,
        budget: ModelCallBudget,
        usage_tracker: UsageTracker,
    ) -> None:
        self.provider = provider
        self.ai_config = ai_config
        self.budget = budget
        self.usage_tracker = usage_tracker

    def investigate(
        self,
        candidate_group_id: str,
        group_alerts: list[Alert],
        dependencies: list[ServiceDependency],
        historical_pattern_titles: list[str],
        triage: TriageResult,
        historical_pattern_cases: list[dict] | None = None,
        operator_requested_ai: bool = False,
    ) -> InvestigationResult:
        deterministic = self._deterministic_investigation(
            candidate_group_id, group_alerts, dependencies, historical_pattern_titles
        )

        decision = should_use_ai(self.ai_config, triage, deterministic, operator_requested_ai)
        if not decision.use_ai:
            status = "AI_DISABLED" if not self.ai_config.enabled else "AI_NOT_RUN"
            deterministic.ai_status = status
            deterministic.ai_reason = decision.reason
            return deterministic

        if self.provider is None:
            deterministic.ai_status = "AI_DISABLED"
            deterministic.ai_reason = "AI was selected as useful, but no model provider is configured."
            return deterministic

        if not self.budget.can_spend():
            deterministic.ai_status = "AI_BUDGET_EXHAUSTED"
            deterministic.ai_reason = (
                f"{decision.reason} However, the model-call budget "
                f"({self.budget.used}/{self.budget.max_calls}) is exhausted for this run; "
                "falling back to the deterministic result."
            )
            return deterministic

        return self._ai_assisted_investigation(
            candidate_group_id, group_alerts, dependencies, historical_pattern_cases or [], decision.reason
        )

    def _deterministic_investigation(
        self,
        candidate_group_id: str,
        group_alerts: list[Alert],
        dependencies: list[ServiceDependency],
        historical_pattern_titles: list[str],
    ) -> InvestigationResult:
        evidence = gather_evidence(group_alerts, dependencies, historical_pattern_titles)

        if len(group_alerts) < MIN_ALERTS_FOR_HYPOTHESIS or evidence.candidate_origin is None:
            return InvestigationResult(
                candidate_group_id=candidate_group_id,
                status="insufficient_evidence",
                observed_facts=evidence.observed_facts,
                derived_relationships=[],
                historical_similarities=evidence.historical_similarities,
                probable_cause_hypothesis=None,
                missing_or_contradictory=evidence.missing_or_contradictory
                + ["Fewer than the minimum alert count required to form a hypothesis."],
                unresolved_unknowns=["Root cause cannot be hypothesized from the evidence currently available."],
                evidence_source="deterministic_fallback",
                model_used=None,
            )

        narrative = (
            f"The proposed grouping contains {len(group_alerts)} alerts across "
            f"{len({a.service for a in group_alerts})} service(s). "
            + (
                f"Shared dependency paths ({'; '.join(evidence.supporting_dependencies)}) "
                if evidence.supporting_dependencies
                else "No modeled dependency path "
            )
            + f"support a possible failure originating at {evidence.candidate_origin}.\n\n"
            "This is a probable-cause hypothesis, not proof of causation. "
            "Review the linked evidence before confirming it."
        )

        return InvestigationResult(
            candidate_group_id=candidate_group_id,
            status="hypothesis_formed",
            observed_facts=evidence.observed_facts,
            derived_relationships=evidence.supporting_dependencies,
            historical_similarities=evidence.historical_similarities,
            probable_cause_hypothesis=narrative,
            missing_or_contradictory=evidence.missing_or_contradictory,
            unresolved_unknowns=["This is a probable-cause hypothesis, not confirmed causation."],
            evidence_source="deterministic_fallback",
            model_used=None,
        )

    def _ai_assisted_investigation(
        self,
        candidate_group_id: str,
        group_alerts: list[Alert],
        dependencies: list[ServiceDependency],
        historical_pattern_cases: list[dict],
        ai_reason: str,
    ) -> InvestigationResult:
        assert self.provider is not None

        evidence_package = build_evidence_package(
            candidate_group_id,
            group_alerts,
            dependencies,
            historical_pattern_cases,
            max_alerts=self.ai_config.max_alerts_per_incident,
            max_historical_cases=self.ai_config.max_historical_cases,
        )

        retries_used = 0
        last_error: ProviderCallError | None = None
        model_response: ModelInvestigationResponse | None = None
        usage_meta: dict = {}

        attempts_allowed = 1 + self.ai_config.max_retries
        for attempt in range(attempts_allowed):
            if not self.budget.can_spend():
                break
            try:
                self.budget.spend(f"investigation:{candidate_group_id}")
                start = time.perf_counter()
                candidate_response, usage_meta = self.provider.invoke_investigation(
                    evidence_package,
                    max_output_tokens=self.ai_config.max_output_tokens,
                    timeout_seconds=self.ai_config.request_timeout_seconds,
                )
                usage_meta.setdefault("latency_ms", round((time.perf_counter() - start) * 1000, 2))
                # Validate BEFORE trusting the response: do not assign it to
                # model_response until it has passed reference validation,
                # or a rejected response would still fall through to the
                # AI_CALLED path below with its invented references intact.
                self._validate_model_references(candidate_response, evidence_package)
                model_response = candidate_response
                self._record_usage(candidate_group_id, evidence_package, usage_meta, retries_used, "success")
                break
            except ProviderCallError as exc:
                last_error = exc
                retries_used = attempt
                self._record_usage(
                    candidate_group_id,
                    evidence_package,
                    {"latency_ms": 0.0},
                    retries_used,
                    "timeout" if "timed out" in str(exc) else "error",
                    error_summary=f"{type(exc).__name__}",
                )
                if not exc.retryable:
                    break
                continue

        if model_response is None:
            deterministic = self._deterministic_investigation(
                candidate_group_id, group_alerts, dependencies, [c["title"] for c in historical_pattern_cases]
            )
            deterministic.ai_status = "AI_CALL_FAILED"
            deterministic.ai_reason = (
                f"{ai_reason} The model call failed ({type(last_error).__name__ if last_error else 'unknown'}) "
                "after retries; falling back to the deterministic result. No alert was discarded."
            )
            deterministic.ai_alerts_considered = len(evidence_package.alerts)
            deterministic.ai_historical_cases_considered = len(evidence_package.historical_cases)
            return deterministic

        cited_alert_ids = set(model_response.supporting_evidence_ids)
        cited_facts = [f"{a.service}: {a.signal}" for a in group_alerts if a.id in cited_alert_ids]
        observed_facts = cited_facts or [f"{a.service}: {a.signal}" for a in group_alerts]

        return InvestigationResult(
            candidate_group_id=candidate_group_id,
            status="hypothesis_formed" if model_response.probable_origin else "insufficient_evidence",
            observed_facts=observed_facts,
            derived_relationships=evidence_package.dependency_edges,
            historical_similarities=[c.title for c in evidence_package.historical_cases if c.id in model_response.historical_case_ids],
            probable_cause_hypothesis=(
                (model_response.hypotheses[0] if model_response.hypotheses else None)
                or (f"Probable origin: {model_response.probable_origin}." if model_response.probable_origin else None)
            ),
            missing_or_contradictory=model_response.missing_evidence + model_response.contradictory_evidence,
            unresolved_unknowns=(
                [model_response.uncertainty_reason] if model_response.uncertainty_reason else []
            )
            + ["Model-assisted hypothesis — not confirmed causation."],
            evidence_source="llm",
            model_used=f"{self.provider.name}:{self.provider.model}",
            ai_status="AI_CALLED",
            ai_reason=ai_reason,
            ai_alerts_considered=len(evidence_package.alerts),
            ai_historical_cases_considered=len(evidence_package.historical_cases),
        )

    @staticmethod
    def _validate_model_references(response: ModelInvestigationResponse, evidence) -> None:
        """Never trust the model's own claims about what it referenced —
        every ID must resolve to something actually in the evidence package
        that was sent. Raises ProviderCallError (treated as a failed call,
        triggering the deterministic fallback) if the model invented one.
        """
        from ..ai.provider import ProviderResponseError

        alert_ids = evidence.alert_ids()
        case_ids = evidence.historical_case_ids()
        invented_alerts = set(response.supporting_evidence_ids) - alert_ids
        invented_cases = set(response.historical_case_ids) - case_ids
        if invented_alerts or invented_cases:
            raise ProviderResponseError(
                f"Model referenced evidence not present in the supplied context "
                f"(alerts={invented_alerts or None}, cases={invented_cases or None})"
            )

    def _record_usage(
        self,
        candidate_group_id: str,
        evidence_package,
        usage_meta: dict,
        retry_count: int,
        status: str,
        error_summary: str | None = None,
    ) -> None:
        if self.provider is None:
            return
        self.usage_tracker.record(
            ModelUsageRecord(
                provider=self.provider.name,
                model=self.provider.model,
                purpose="investigation",
                investigation_id=candidate_group_id,
                alert_count=len(evidence_package.alerts),
                historical_case_count=len(evidence_package.historical_cases),
                input_tokens=usage_meta.get("input_tokens"),
                output_tokens=usage_meta.get("output_tokens"),
                tokens_are_estimated=usage_meta.get("tokens_are_estimated", True),
                latency_ms=usage_meta.get("latency_ms", 0.0),
                retry_count=retry_count,
                status="success" if status == "success" else status,  # type: ignore[arg-type]
                error_summary=error_summary,
            )
        )
