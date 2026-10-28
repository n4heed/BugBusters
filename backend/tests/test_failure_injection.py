"""Additional failure-injection tests from the milestone 3 checklist not
already covered by test_pipeline.py / test_ai_pipeline.py / test_evaluation.py:
determinism under repeated evaluation, out-of-order event arrival, and
sensitive-field redaction actually taking effect end to end.
"""
from __future__ import annotations

import pytest

from app.evaluation.replay import run_deterministic_replay
from app.orchestrator import Orchestrator
from app.store import Store


# 11. The same dataset is evaluated twice — results must be identical (reproducibility).
def test_same_dataset_evaluated_twice_gives_identical_results():
    store_a, results_a = run_deterministic_replay()
    store_b, results_b = run_deterministic_replay()

    statuses_a = {r.alert_id: r.workflow_status for r in results_a}
    statuses_b = {r.alert_id: r.workflow_status for r in results_b}
    assert statuses_a == statuses_b

    groups_a = sorted(sorted(a.id for a in g) for g in store_a.candidate_groups.values())
    groups_b = sorted(sorted(a.id for a in g) for g in store_b.candidate_groups.values())
    assert groups_a == groups_b


# 13. An event arrives late / out of timestamp order.
#
# FINDING (discovered running this test, not assumed): correlation is
# arrival-order sensitive. Bootstrapping a new candidate group only ever
# pairs an incoming alert with its single BEST-scoring safety-queue seed
# (triage_agent.py's bootstrap path) — it doesn't re-evaluate OTHER
# already-unclustered alerts against the group once it forms. In arrival
# order [A3, A2, A1]: A3 sits alone in the safety queue; A2 scores only
# 0.30 against A3 (below the 0.50 threshold) and also lands in the safety
# queue; A1 then scores 0.60 against A3 and bootstraps a group with it —
# but nothing ever goes back and re-scores A2 against the now-formed
# {A1, A3} group, so A2 is permanently stranded as a straggler even though
# it belongs with them. In the real (forward) arrival order this never
# happens to matter for THIS dataset, but it is a genuine, order-dependent
# reliability gap: a correlation engine for a live, out-of-order alert
# stream should re-score unclustered alerts against newly-formed groups
# when they're no longer singletons, not only at the moment they first
# arrive. Recorded as `xfail` (not silently skipped) so this regresses
# loudly if anyone "fixes" it without updating this test.
@pytest.mark.xfail(
    reason="Known limitation: bootstrap-formed groups do not re-score previously-unclustered "
    "straggler alerts against them after formation. See test docstring/comment above.",
    strict=True,
)
def test_out_of_order_arrival_still_groups_correctly():
    """Feed the related-group alerts in reverse order — correlation keys on
    alert timestamps, not arrival order, so the final grouping SHOULD match
    regardless of which one the orchestrator sees first. It currently does
    not; see the FINDING comment above.
    """
    from app.fixtures_pipeline import REPLAY_ALERTS

    related = [dict(a) for a in REPLAY_ALERTS if a["id"] in ("R-A1", "R-A2", "R-A3")]
    reversed_order = list(reversed(related))

    store = Store()
    orchestrator = Orchestrator(store)
    for raw in reversed_order:
        orchestrator.process_alert(raw)

    group_ids = {store.all_alerts[a["id"]].incident_id for a in related}
    assert len(group_ids) == 1
    assert None not in group_ids
    for a in related:
        assert store.all_alerts[a["id"]].workflow_status == "VERIFIED"


# 14. A sensitive field appears in an alert payload — redaction must mask it
# before it's ever visible outside normalize_alert, and the raw value must
# never be present on the serialized Alert.
def test_sensitive_fields_are_redacted_end_to_end():
    store = Store()
    orchestrator = Orchestrator(store)
    raw = {
        "id": "SENSITIVE-1",
        "service": "svc-x",
        "signal": "test",
        "severity": "high",
        "host": "db-prod-47.internal.example.com",
        "ip": "10.42.17.9",
        "customer": "customer-acme-corp-id-99881",
    }
    orchestrator.process_alert(raw)
    alert = store.all_alerts["SENSITIVE-1"]

    assert alert.host_redacted == "[REDACTED_HOST]"
    assert alert.ip_redacted == "[REDACTED_IP]"
    assert alert.customer_redacted == "[REDACTED]"

    serialized = alert.model_dump_json()
    assert "db-prod-47" not in serialized
    assert "10.42.17.9" not in serialized
    assert "customer-acme-corp-id-99881" not in serialized
