"""Per-case error analysis — not just averages. Every finding carries a
stable case reference, the stage responsible, and an actionable category
(from the milestone's own taxonomy) rather than a bare pass/fail.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..schemas.agent_results import WorkflowStatus
from ..store import Store

CATEGORIES = (
    "incorrect_timestamp_normalization",
    "missing_dependency_information",
    "weak_correlation_rules",
    "inappropriate_threshold_selection",
    "irrelevant_historical_retrieval",
    "unsupported_model_conclusion",
    "invalid_model_output",
    "processing_or_persistence_failure",
)


@dataclass
class ErrorCase:
    case_ref: str
    stage: str
    category: str
    observed: str
    expected: str
    alert_ids: list[str]


def analyze_against_expected_status(
    store: Store, expected_status: dict[str, str], case_prefix: str = "REPLAY"
) -> list[ErrorCase]:
    """Compares each alert's actual final workflow_status against the
    documented expectation (fixtures_pipeline.EXPECTED_ALERT_WORKFLOW_STATUS)
    and classifies any mismatch.
    """
    findings: list[ErrorCase] = []
    for alert_id, expected in expected_status.items():
        alert = store.all_alerts.get(alert_id)
        if alert is None:
            findings.append(
                ErrorCase(
                    case_ref=f"{case_prefix}-{alert_id}",
                    stage="orchestrator",
                    category="processing_or_persistence_failure",
                    observed="Alert not found in store after processing.",
                    expected=f"workflow_status={expected}",
                    alert_ids=[alert_id],
                )
            )
            continue
        actual = alert.workflow_status
        if actual != expected:
            category = "weak_correlation_rules" if expected == "VERIFIED" and actual == "UNCLUSTERED" else "inappropriate_threshold_selection"
            findings.append(
                ErrorCase(
                    case_ref=f"{case_prefix}-{alert_id}",
                    stage="triage_or_verification",
                    category=category,
                    observed=f"workflow_status={actual}",
                    expected=f"workflow_status={expected}",
                    alert_ids=[alert_id],
                )
            )
    return findings


def analyze_processing_errors(store: Store, case_prefix: str = "REPLAY") -> list[ErrorCase]:
    """Any ProcessingResult that ended in PROCESSING_ERROR is, by
    definition, an unresolved failure worth surfacing — even though the
    safety invariant (alert still preserved) held.
    """
    findings: list[ErrorCase] = []
    for alert_id, result in store.processing_results.items():
        if result.workflow_status == WorkflowStatus.PROCESSING_ERROR:
            findings.append(
                ErrorCase(
                    case_ref=f"{case_prefix}-{alert_id}",
                    stage="normalization_or_orchestrator",
                    category="processing_or_persistence_failure",
                    observed=result.error or "Unknown processing error.",
                    expected="Successful normalization/processing.",
                    alert_ids=[alert_id],
                )
            )
    return findings


def analyze_order_sensitivity(case_prefix: str = "RELIABILITY") -> list[ErrorCase]:
    """Known limitation (see tests/test_failure_injection.py): a bootstrap-
    formed candidate group does not re-score previously-unclustered
    straggler alerts against it after formation, so correlation results
    can depend on arrival order. Runs the reversed-order reproduction and
    records a finding if the straggler is, as expected, left stranded.
    """
    from ..fixtures_pipeline import REPLAY_ALERTS
    from ..orchestrator import Orchestrator
    from ..store import Store

    related = [dict(a) for a in REPLAY_ALERTS if a["id"] in ("R-A1", "R-A2", "R-A3")]
    store = Store()
    orchestrator = Orchestrator(store)
    for raw in reversed(related):
        orchestrator.process_alert(raw)

    group_ids = {store.all_alerts[a["id"]].incident_id for a in related}
    if len(group_ids) <= 1 and None not in group_ids:
        return []  # would mean the limitation has been fixed — nothing to report

    stranded = [a["id"] for a in related if store.all_alerts[a["id"]].incident_id is None]
    return [
        ErrorCase(
            case_ref=f"{case_prefix}-order-sensitivity",
            stage="triage_agent",
            category="weak_correlation_rules",
            observed=(
                f"Alert(s) {stranded} remained stranded/unclustered when the same 3 related alerts "
                "were processed in reverse arrival order, even though forward order groups all 3 correctly."
            ),
            expected="Grouping should be order-independent for a fixed set of alerts and timestamps.",
            alert_ids=[a["id"] for a in related],
        )
    ]


def analyze_ai_call_outcomes(store: Store, case_prefix: str = "REPLAY") -> list[ErrorCase]:
    """Cases where AI was called but failed, or was called and did not
    change/improve the result — both worth recording, per the milestone's
    explicit request for "cases where AI was called but did not improve
    the result".
    """
    findings: list[ErrorCase] = []
    for alert_id, result in store.processing_results.items():
        inv = result.investigation
        if inv is None:
            continue
        if inv.ai_status == "AI_CALL_FAILED":
            findings.append(
                ErrorCase(
                    case_ref=f"{case_prefix}-{alert_id}",
                    stage="investigation_agent",
                    category="invalid_model_output",
                    observed=inv.ai_reason,
                    expected="A validated model response or a clean AI_DISABLED/AI_NOT_RUN status.",
                    alert_ids=[alert_id],
                )
            )
        elif inv.ai_status == "AI_CALLED" and inv.evidence_source == "llm" and not inv.probable_cause_hypothesis:
            findings.append(
                ErrorCase(
                    case_ref=f"{case_prefix}-{alert_id}",
                    stage="investigation_agent",
                    category="unsupported_model_conclusion",
                    observed="AI was called but returned no usable hypothesis.",
                    expected="A probable-cause hypothesis or an explicit insufficient_evidence status.",
                    alert_ids=[alert_id],
                )
            )
    return findings
