"""Mode A — synthetic smoke tests. Fast, deterministic, fixture-driven.
Labelled `SYNTHETIC TEST RESULT` everywhere they're surfaced — never
presented as a measurement on a real dataset.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..fixtures_pipeline import EXPECTED_ALERT_WORKFLOW_STATUS
from ..models import ServiceDependency
from ..orchestrator import Orchestrator
from ..services.correlation import correlate
from ..services.normalization import normalize_alert
from ..store import Store
from .replay import run_deterministic_replay

T0 = datetime(2026, 2, 1, tzinfo=timezone.utc)


@dataclass
class SmokeCheck:
    name: str
    category: str
    passed: bool
    detail: str


@dataclass
class SmokeReport:
    provenance: str = "SYNTHETIC TEST RESULT"
    checks: list[SmokeCheck] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def pass_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)


def _check_replay_categories() -> list[SmokeCheck]:
    """Related / unrelated / repeated / novel / ambiguous — all covered by
    the deterministic replay scenario, whose expected outcomes are
    documented in fixtures_pipeline.py.
    """
    store, _ = run_deterministic_replay()
    checks: list[SmokeCheck] = []

    category_by_alert = {
        "R-A1": "related_alerts",
        "R-A2": "related_alerts",
        "R-A3": "related_alerts",
        "R-DUP1": "repeated_alert_condition",
        "R-NOVEL1": "novel_unrelated_alert",
        "R-AMBIG1": "ambiguous_incident",
    }
    for alert_id, expected in EXPECTED_ALERT_WORKFLOW_STATUS.items():
        actual = store.all_alerts[alert_id].workflow_status
        checks.append(
            SmokeCheck(
                name=f"replay:{alert_id}",
                category=category_by_alert.get(alert_id, "replay"),
                passed=(actual == expected),
                detail=f"expected workflow_status={expected}, got {actual}",
            )
        )
    return checks


def _check_missing_telemetry() -> SmokeCheck:
    """An alert for a service with NO modeled dependency edges and no
    other alerts nearby must still be processed safely — no crash, stays
    visible (unclustered), not silently dropped.
    """
    store = Store()
    orchestrator = Orchestrator(store)
    raw = {
        "id": "SMOKE-MISSING-TELEM",
        "timestamp": (T0).isoformat(),
        "service": "totally-unmodeled-service-xyz",
        "signal": "Unknown condition",
        "severity": "medium",
    }
    result = orchestrator.process_alert(raw)
    alert_present = "SMOKE-MISSING-TELEM" in store.all_alerts
    no_crash = result.error is None
    passed = alert_present and no_crash
    return SmokeCheck(
        name="missing_telemetry",
        category="missing_telemetry",
        passed=passed,
        detail=f"alert_present={alert_present}, error={result.error}, workflow_status={result.workflow_status}",
    )


def _check_inconsistent_dependency_information() -> SmokeCheck:
    """A cyclic/self-referential dependency edge (A depends on B, B depends
    on A) must not hang or crash correlation scoring — dependency data from
    the real world is not guaranteed to be a clean DAG.
    """
    cyclic_deps = [
        ServiceDependency(service="svc-cycle-a", depends_on="svc-cycle-b"),
        ServiceDependency(service="svc-cycle-b", depends_on="svc-cycle-a"),
    ]
    alert_a = normalize_alert(
        {"id": "SMOKE-CYCLE-A", "timestamp": T0.isoformat(), "service": "svc-cycle-a", "signal": "sig", "severity": "high"}
    )
    alert_b = normalize_alert(
        {
            "id": "SMOKE-CYCLE-B",
            "timestamp": (T0 + timedelta(minutes=1)).isoformat(),
            "service": "svc-cycle-b",
            "signal": "sig2",
            "severity": "high",
        }
    )
    try:
        group_id, signals, score = correlate(alert_b, {"G1": [alert_a]}, cyclic_deps)
        passed = True
        detail = f"completed without error: group_id={group_id}, score={score}"
    except RecursionError as exc:
        passed = False
        detail = f"RecursionError: {exc}"
    return SmokeCheck(
        name="inconsistent_dependency_information",
        category="inconsistent_dependency_information",
        passed=passed,
        detail=detail,
    )


def _check_malformed_payload_preserved() -> SmokeCheck:
    """A payload missing required fields must raise NormalizationError
    (caught by the orchestrator) rather than silently producing a garbage
    alert — and the orchestrator path must still preserve a record of it.
    """
    store = Store()
    orchestrator = Orchestrator(store)
    before = len(store.all_alerts)
    result = orchestrator.process_alert({"id": "SMOKE-MALFORMED", "signal": "missing service field"})
    after = len(store.all_alerts)
    preserved = after == before + 1
    flagged = result.workflow_status.value == "PROCESSING_ERROR"
    return SmokeCheck(
        name="malformed_payload",
        category="missing_telemetry",
        passed=preserved and flagged,
        detail=f"preserved={preserved}, workflow_status={result.workflow_status}",
    )


def run_smoke_tests() -> SmokeReport:
    report = SmokeReport()
    report.checks.extend(_check_replay_categories())
    report.checks.append(_check_missing_telemetry())
    report.checks.append(_check_inconsistent_dependency_information())
    report.checks.append(_check_malformed_payload_preserved())
    return report
