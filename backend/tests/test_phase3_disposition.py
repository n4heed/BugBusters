"""Phase 3, unit-level: disposition (unique/redundant/uncertain),
duplicate-representative selection, and consistency between the Alert
model, TriageResult, and the final ProcessingResult. Uses Orchestrator +
Store directly (no SQLite needed for these).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import AlertDisposition, alert_relationship_label
from app.orchestrator import Orchestrator
from app.schemas.agent_results import WorkflowStatus
from app.store import Store

BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _payload(i, service, signal, severity="high", minutes=0, fingerprint=None):
    p = {
        "id": f"A{i}",
        "timestamp": (BASE + timedelta(minutes=minutes)).isoformat(),
        "service": service,
        "signal": signal,
        "severity": severity,
    }
    if fingerprint:
        p["fingerprint"] = fingerprint
    return p


def _fresh():
    store = Store(seed_demo_fixtures=False)
    return store, Orchestrator(store)


# 1. Two alerts with the same valid fingerprint are classified as duplicates.
def test_same_fingerprint_classified_as_duplicate():
    store, orch = _fresh()
    r1 = orch.process_alert(_payload(1, "svc-a", "Disk full", fingerprint="fp-x"))
    r2 = orch.process_alert(_payload(2, "svc-a", "Disk full (again)", minutes=5, fingerprint="fp-x"))
    assert r1.disposition == "unique"
    assert r2.disposition == "redundant"
    assert r2.workflow_status == WorkflowStatus.DUPLICATE


# 2. A duplicate points to its canonical representative and never itself.
def test_duplicate_references_representative_not_itself():
    store, orch = _fresh()
    orch.process_alert(_payload(1, "svc-a", "Disk full", fingerprint="fp-x"))
    r2 = orch.process_alert(_payload(2, "svc-a", "Disk full", minutes=1, fingerprint="fp-x"))
    assert r2.duplicate_of == "A1"
    assert r2.duplicate_of != r2.alert_id
    assert store.all_alerts["A2"].duplicate_of == "A1"


# 3. Duplicate classification is consistent across Alert, TriageResult, and
# the final ProcessingResult — the exact bug flagged at the start of Phase 3
# (DUPLICATE alert + UNCLUSTERED result) must no longer be reproducible.
def test_duplicate_disposition_consistent_across_representations():
    store, orch = _fresh()
    orch.process_alert(_payload(1, "svc-a", "Disk full", fingerprint="fp-x"))
    r2 = orch.process_alert(_payload(2, "svc-a", "Disk full", minutes=1, fingerprint="fp-x"))
    alert = store.all_alerts["A2"]
    assert alert.workflow_status == "DUPLICATE"
    assert alert.disposition == AlertDisposition.redundant
    assert r2.workflow_status == WorkflowStatus.DUPLICATE
    assert r2.workflow_status != WorkflowStatus.UNCLUSTERED  # the original bug
    assert r2.disposition == "redundant"
    assert r2.triage.decision == "duplicate"
    assert alert_relationship_label(alert) == "redundant"


# 7. Multiple repeated copies produce ONE canonical representative and the
# correct redundant-alert count.
def test_multiple_copies_produce_one_representative_and_correct_count():
    store, orch = _fresh()
    results = [
        orch.process_alert(_payload(i, "svc-a", "Disk full", fingerprint="fp-x", minutes=i)) for i in range(5)
    ]
    redundant = [r for r in results if r.disposition == "redundant"]
    unique = [r for r in results if r.disposition == "unique"]
    assert len(unique) == 1
    assert len(redundant) == 4
    assert {r.duplicate_of for r in redundant} == {"A0"}


# 8. Transitive duplicate relationships resolve consistently, without cycles
# — a later duplicate must reference the ORIGINAL representative, never an
# intermediate duplicate.
def test_transitive_duplicates_resolve_without_cycles():
    store, orch = _fresh()
    orch.process_alert(_payload(1, "svc-a", "Disk full", fingerprint="fp-x"))
    orch.process_alert(_payload(2, "svc-a", "Disk full", minutes=1, fingerprint="fp-x"))
    r3 = orch.process_alert(_payload(3, "svc-a", "Disk full", minutes=2, fingerprint="fp-x"))
    assert r3.duplicate_of == "A1"
    representative = store.all_alerts[r3.duplicate_of]
    assert representative.disposition == AlertDisposition.unique
    assert representative.duplicate_of is None  # no cycle: the representative references no one


# 9. Records with missing fingerprints still follow a deterministic fallback.
def test_missing_fingerprint_falls_back_deterministically():
    store, orch = _fresh()
    orch.process_alert(_payload(1, "svc-a", "Disk full"))  # no fingerprint supplied
    r2 = orch.process_alert(_payload(2, "svc-a", "Disk full", minutes=1))  # identical text, also none
    assert store.all_alerts["A1"].fingerprint == store.all_alerts["A2"].fingerprint
    assert r2.disposition == "redundant"


# 10. Missing/malformed optional fields do not crash and do not cause a
# false-positive merge with a real, subsequent record.
def test_malformed_record_does_not_crash_or_false_merge():
    store, orch = _fresh()
    bad = {"timestamp": BASE.isoformat(), "severity": "high"}  # missing required service/signal
    result = orch.process_alert(bad)
    assert result.workflow_status == WorkflowStatus.PROCESSING_ERROR
    assert result.disposition == "uncertain"

    good = orch.process_alert(_payload(1, "svc-a", "Disk full"))
    assert good.disposition == "unique"
    assert good.duplicate_of is None


# Two unrelated alerts do not get grouped simply because they share a
# service or severity (section 9, test 5 — unit-level half; see the
# integration suite for the dependency-evidence-based grouping case).
def test_shared_service_and_severity_alone_do_not_group():
    store, orch = _fresh()
    orch.process_alert(_payload(1, "svc-a", "Disk I/O elevated", severity="high", minutes=0))
    r2 = orch.process_alert(_payload(2, "svc-a", "Auth token expired", severity="high", minutes=1))
    # Same service, same severity, close in time — but a genuinely distinct
    # event type/fingerprint and no dependency evidence: must NOT merge.
    assert r2.triage.decision == "unclustered"
    assert store.all_alerts["A2"].incident_id is None


# 18. Synthetic-demo processing continues to work, and provenance is
# correctly recorded on the Alert itself (section 2A).
def test_synthetic_demo_processing_still_works_and_is_marked_synthetic():
    store, orch = _fresh()
    r = orch.process_alert(_payload(1, "svc-a", "Disk full"), is_synthetic=True)
    assert store.all_alerts["A1"].is_synthetic is True
    assert r.is_synthetic is True
    # An imported-style call (is_synthetic=False) must be distinguishable.
    r2 = orch.process_alert(_payload(2, "svc-b", "Other event", minutes=1), is_synthetic=False)
    assert store.all_alerts["A2"].is_synthetic is False
    assert r2.is_synthetic is False
