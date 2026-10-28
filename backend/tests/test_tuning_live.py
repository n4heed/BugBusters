"""Chronic-noise tuning recommendations must reflect LIVE alert traffic
(demo simulation, dataset replay, or direct ingest) — not just the static
demo-seed numbers, which never changed no matter how many alerts the
simulation generated. This was a real, reported gap: `store.tuning` was
populated once from fixtures.build_tuning_recommendations() and nothing in
the pipeline ever touched it again.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import IncidentStatus, RecommendationStatus
from app.orchestrator import Orchestrator
from app.store import TUNING_RECURRING_THRESHOLD, Store

BASE = datetime(2026, 5, 1, tzinfo=timezone.utc)


def _alert_payload(i: int, minutes_apart: int = 10) -> dict:
    return {
        "id": f"T{i}",
        "timestamp": (BASE + timedelta(minutes=i * minutes_apart)).isoformat(),
        "service": "notif-dispatch",
        "signal": "Queue backlog growing",
        "severity": "medium",
    }


def test_recurring_alert_creates_live_tuning_recommendation():
    store = Store(seed_demo_fixtures=False)
    orch = Orchestrator(store)

    for i in range(TUNING_RECURRING_THRESHOLD):
        orch.process_alert(_alert_payload(i), is_synthetic=True)

    live_recs = [r for r in store.tuning.values() if r.rule_id.startswith("LIVE-")]
    assert len(live_recs) == 1
    rec = live_recs[0]
    assert rec.service == "notif-dispatch"
    assert rec.rule_name == "QUEUE_BACKLOG_GROWING"
    assert rec.count_24h == TUNING_RECURRING_THRESHOLD
    assert rec.status == RecommendationStatus.pending


def test_below_threshold_does_not_create_a_recommendation():
    store = Store(seed_demo_fixtures=False)
    orch = Orchestrator(store)

    for i in range(TUNING_RECURRING_THRESHOLD - 1):
        orch.process_alert(_alert_payload(i), is_synthetic=True)

    assert not any(r.rule_id.startswith("LIVE-") for r in store.tuning.values())


def test_count_keeps_growing_as_more_matching_alerts_arrive():
    store = Store(seed_demo_fixtures=False)
    orch = Orchestrator(store)

    for i in range(TUNING_RECURRING_THRESHOLD + 2):
        orch.process_alert(_alert_payload(i), is_synthetic=True)

    live_recs = [r for r in store.tuning.values() if r.rule_id.startswith("LIVE-")]
    assert len(live_recs) == 1
    assert live_recs[0].count_24h == TUNING_RECURRING_THRESHOLD + 2


def test_human_approval_on_a_live_rule_is_not_reset_by_later_alerts():
    store = Store(seed_demo_fixtures=False)
    orch = Orchestrator(store)

    for i in range(TUNING_RECURRING_THRESHOLD):
        orch.process_alert(_alert_payload(i), is_synthetic=True)

    rule_id = next(r.rule_id for r in store.tuning.values() if r.rule_id.startswith("LIVE-"))
    store.approve_tuning(rule_id)
    assert store.tuning[rule_id].status == RecommendationStatus.approved

    # Another matching alert arrives — the approval must survive.
    orch.process_alert(_alert_payload(TUNING_RECURRING_THRESHOLD), is_synthetic=True)
    assert store.tuning[rule_id].status == RecommendationStatus.approved
    assert store.tuning[rule_id].count_24h == TUNING_RECURRING_THRESHOLD + 1


def test_historical_links_counts_only_approved_incident_occurrences():
    store = Store(seed_demo_fixtures=False)
    orch = Orchestrator(store)

    for i in range(TUNING_RECURRING_THRESHOLD):
        orch.process_alert(_alert_payload(i), is_synthetic=True)
    rule_id = next(r.rule_id for r in store.tuning.values() if r.rule_id.startswith("LIVE-"))
    assert store.tuning[rule_id].historical_links == 0

    # Manually simulate one of those alerts belonging to an approved incident.
    from app.models import Evidence, SafetyDisclosure, Incident

    alert = store.all_alerts["T0"]
    store.incidents["FAKE-INC"] = Incident(
        id="FAKE-INC", title="x", severity="medium", origin_hypothesis="notif-dispatch",
        first_seen=BASE, last_seen=BASE, affected_services=["notif-dispatch"], status=IncidentStatus.approved,
        related_alerts=[alert], dependencies=[],
        evidence=Evidence(narrative="x", overlapping_time_windows=[], supporting_dependencies=[],
                           supporting_metrics=[], missing_or_contradictory=[], similar_historical_incidents=[],
                           unresolved_uncertainty=[]),
        safety=SafetyDisclosure(confidence_label="NOT MEASURED", evidence_completeness="partial"),
    )
    alert.incident_id = "FAKE-INC"

    # A new matching alert triggers a recompute of this rule group.
    orch.process_alert(_alert_payload(TUNING_RECURRING_THRESHOLD), is_synthetic=True)
    assert store.tuning[rule_id].historical_links == 1
