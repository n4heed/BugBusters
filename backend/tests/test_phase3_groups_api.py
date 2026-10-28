"""Phase 3, integration-level: the acceptance scenario's own dataset
(repeated event, related-but-distinct alert, unrelated alert, standalone
alert) processed end to end through import -> validate -> load -> replay,
with group/duplicate persistence, the new groups API, restart-safety, and
ground-truth isolation.

Dataset layout (mirrors the acceptance scenario in the Phase 3 brief):
  A1: payments-db, "Connection pool exhausted"            (representative)
  A2: payments-db, "Connection pool exhausted" (recurring) (duplicate of A1)
  A3: checkout-svc, "Database query failed"                (related, distinct — groups with A1 via dependency)
  A4: notification-service, "SMTP timeout"                 (unrelated — must NOT join the group)
  A5: billing-worker, "License check delayed", 10h later   (standalone — no correlation candidate at all)
Dependency: checkout-svc -> payments-db.
"""
from __future__ import annotations

import app.routers.datasets as datasets_router
from app.dataset_registry import DatasetRegistry
from app.ingestion.import_service import (
    confirm_mapping,
    import_dataset,
    load_dataset,
    run_replay,
    validate_dataset,
)
from app.persistence import repository
from app.persistence.db import get_connection

import pytest

ALERTS_CSV = (
    "id,timestamp,service,message,severity,fingerprint\r\n"
    "A1,2026-08-01T00:00:00Z,payments-db,Connection pool exhausted,critical,fp-pool\r\n"
    "A2,2026-08-01T00:02:00Z,payments-db,Connection pool exhausted (recurring alert occurrence).,critical,fp-pool\r\n"
    "A3,2026-08-01T00:03:00Z,checkout-svc,Database query failed,high,fp-checkout-dbfail\r\n"
    "A4,2026-08-01T00:05:00Z,notification-service,SMTP timeout,low,fp-smtp\r\n"
    "A5,2026-08-01T10:00:00Z,billing-worker,License check delayed,medium,fp-license\r\n"
).encode("utf-8")

# Same 5 alerts, rows shuffled — used by the reordering-determinism test.
ALERTS_CSV_REORDERED = (
    "id,timestamp,service,message,severity,fingerprint\r\n"
    "A5,2026-08-01T10:00:00Z,billing-worker,License check delayed,medium,fp-license\r\n"
    "A3,2026-08-01T00:03:00Z,checkout-svc,Database query failed,high,fp-checkout-dbfail\r\n"
    "A1,2026-08-01T00:00:00Z,payments-db,Connection pool exhausted,critical,fp-pool\r\n"
    "A4,2026-08-01T00:05:00Z,notification-service,SMTP timeout,low,fp-smtp\r\n"
    "A2,2026-08-01T00:02:00Z,payments-db,Connection pool exhausted (recurring alert occurrence).,critical,fp-pool\r\n"
).encode("utf-8")

DEPENDENCIES_CSV = "source_service,target_service\r\ncheckout-svc,payments-db\r\n".encode("utf-8")

# Deliberately FALSE ground truth: claims A4 and A5 belong to the same
# incident, even though nothing in the actual data supports that — used to
# verify ground truth never influences operational grouping (test 20).
GROUND_TRUTH_CSV = "alert_id,incident_id\r\nA4,FAKE-INC-1\r\nA5,FAKE-INC-1\r\n".encode("utf-8")


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "phase3_test.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    return path


def _prepare(alerts_csv: bytes = ALERTS_CSV, include_ground_truth: bool = False, name: str = "Phase3 Test"):
    files = [("alerts.csv", alerts_csv), ("dependencies.csv", DEPENDENCIES_CSV)]
    if include_ground_truth:
        files.append(("ground_truth.csv", GROUND_TRUTH_CSV))
    record = import_dataset(name, files)
    for file_name, mapping in list(record.file_mappings.items()):
        confirm_mapping(
            record.id, file_name, mapping.category,
            [m for m in mapping.field_mappings],
        )
    validate_dataset(record.id)
    load_dataset(record.id)
    return record


def _by_source_id(record, results):
    """ProcessingResult.alert_id is the pipeline's internal event_id hash,
    not the original CSV "id" column (A1/A2/...) — map back via the
    dataset's own persisted canonical_alerts (event_id -> source_alert_id)
    so tests can refer to alerts by their human-readable source IDs.
    """
    event_id_to_source = {c.event_id: c.source_alert_id for c in record.canonical_alerts}
    return {event_id_to_source[r.alert_id]: r for r in results if r.alert_id in event_id_to_source}


# 4, 5, 6: related-but-distinct alerts stay distinct within the same group;
# unrelated alerts don't merge just because of service/time proximity;
# a standalone alert with no correlation candidate stays standalone.
def test_acceptance_scenario_disposition_and_grouping(db_path):
    record = _prepare()
    run = run_replay(record.id)
    by_id = _by_source_id(record, run.results)

    # A: repeated event -> redundant, points to representative A1.
    assert by_id["A2"].disposition == "redundant"
    assert by_id["A2"].duplicate_of == by_id["A1"].alert_id
    assert by_id["A2"].duplicate_of != by_id["A2"].alert_id

    # B: the related-but-distinct alert remains its OWN unique record...
    assert by_id["A3"].disposition == "unique"
    # ...and shares a group with its representative A1 (section 4B: C
    # remains distinct even though it's in the same group as A/B).
    assert by_id["A1"].incident_id is not None
    assert by_id["A3"].incident_id == by_id["A1"].incident_id

    # D: the unrelated alert must NOT be merged into that group.
    assert by_id["A4"].incident_id != by_id["A1"].incident_id

    # E: the alert with zero correlation candidate remains standalone.
    assert by_id["A5"].incident_id is None
    assert by_id["A5"].disposition == "unique"

    # F: every input alert is accounted for.
    assert set(by_id) == {"A1", "A2", "A3", "A4", "A5"}

    # G: duplicate_count represents redundant RECORDS, not relationships —
    # exactly 1 here (A2), never e.g. counting the A1<->A2 relationship twice.
    summary = repository.load_run_summary(run.id, record.id)
    assert summary["duplicate_count"] == 1
    assert summary["group_count"] == 1
    assert summary["grouped_alert_count"] == 2  # A1 + A3
    assert summary["standalone_count"] == 2  # A4 + A5


# 13, 17: group member lists / per-alert group refs / persisted sizes
# agree; run-level counts match the actual stored rows.
def test_group_membership_and_run_counts_are_internally_consistent(db_path):
    record = _prepare()
    run = run_replay(record.id)
    summary = repository.load_run_summary(run.id, record.id)
    groups = repository.load_run_groups(run.id)

    assert len(groups) == summary["group_count"] == 1
    group = groups[0]
    assert group["size"] == 2
    source_by_event_id = {c.event_id: c.source_alert_id for c in record.canonical_alerts}
    assert {source_by_event_id[eid] for eid in group["member_alert_ids"]} == {"A1", "A3"}

    results = repository.load_run_results(run.id, record.id)
    for r in results:
        if r.incident_id is not None:
            assert r.alert_id in group["member_alert_ids"]

    with get_connection() as conn:
        stored_result_count = conn.execute(
            "SELECT COUNT(*) FROM processing_results WHERE run_id = ?", (run.id,)
        ).fetchone()[0]
    assert stored_result_count == summary["processed_count"] == 5


# 15, 16: the groups API is restart-safe and correctly scoped per dataset/run.
def test_groups_endpoint_restart_safe_and_dataset_scoped(db_path, monkeypatch):
    record = _prepare()
    run = run_replay(record.id)

    other = _prepare(name="Other Dataset")
    other_run = run_replay(other.id)

    fresh_registry = DatasetRegistry()
    fresh_registry.hydrate_from_db()
    monkeypatch.setattr(datasets_router, "registry", fresh_registry)

    details = datasets_router.get_run_groups_endpoint(record.id, run.id)
    assert len(details) == 1
    source_by_event_id = {c.event_id: c.source_alert_id for c in record.canonical_alerts}
    member_sources = {source_by_event_id[m.alert_id] for m in details[0].members}
    assert member_sources == {"A1", "A3"}
    assert details[0].unique_member_count == 2
    assert details[0].redundant_member_count == 0
    # service/severity are populated from persisted canonical records, not
    # the (gone, post-restart) live Store.
    by_source = {source_by_event_id[m.alert_id]: m for m in details[0].members}
    assert by_source["A1"].service == "payments-db"
    assert by_source["A3"].service == "checkout-svc"

    # 16: a run_id that belongs to a DIFFERENT dataset must 404, not leak.
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        datasets_router.get_run_groups_endpoint(record.id, other_run.id)
    assert exc_info.value.status_code == 404


# 14: persisted duplicate/group classifications survive registry recreation.
def test_classifications_survive_registry_recreation(db_path):
    record = _prepare()
    run = run_replay(record.id)

    fresh_registry = DatasetRegistry()
    fresh_registry.hydrate_from_db()
    assert fresh_registry.datasets[record.id].store is None  # runtime genuinely gone

    results = repository.load_run_results(run.id, record.id)
    by_id = _by_source_id(record, results)
    assert by_id["A2"].disposition == "redundant"
    source_by_event_id = {c.event_id: c.source_alert_id for c in record.canonical_alerts}
    assert source_by_event_id[by_id["A2"].duplicate_of] == "A1"
    assert by_id["A3"].incident_id == by_id["A1"].incident_id


# 11: reordering the SAME input dataset's rows does not change effective
# duplicate classifications or group membership (distinct timestamps, so
# the sort-before-process order is unaffected by source row order).
def test_reordering_input_rows_does_not_change_classification_or_grouping(db_path):
    r1 = _prepare(ALERTS_CSV, name="Order A")
    run1 = run_replay(r1.id)
    r2 = _prepare(ALERTS_CSV_REORDERED, name="Order B")
    run2 = run_replay(r2.id)

    by_id_1 = _by_source_id(r1, run1.results)
    by_id_2 = _by_source_id(r2, run2.results)

    for aid in ("A1", "A2", "A3", "A4", "A5"):
        assert by_id_1[aid].disposition == by_id_2[aid].disposition
    assert by_id_1["A2"].duplicate_of == by_id_1["A1"].alert_id
    assert by_id_2["A2"].duplicate_of == by_id_2["A1"].alert_id
    # Group membership (which alerts share a group) is the same, even
    # though the actual group_id string itself is run-specific.
    assert (by_id_1["A3"].incident_id == by_id_1["A1"].incident_id) == (
        by_id_2["A3"].incident_id == by_id_2["A1"].incident_id
    )
    assert by_id_1["A4"].incident_id != by_id_1["A1"].incident_id
    assert by_id_2["A4"].incident_id != by_id_2["A1"].incident_id


# Regression: the POST /replay endpoint's immediate HTTP response must
# include the Phase 3 counts (unique/uncertain/grouped_alert/standalone),
# not just duplicate/group/incident — found via live verification where
# the underlying persisted run row had the correct counts but the
# endpoint's ReplayRunSummary construction forgot to read them back out.
def test_replay_endpoint_response_includes_all_phase3_counts(db_path):
    record = _prepare()
    response = datasets_router.replay_dataset_endpoint(record.id)
    assert response.status == "completed"
    assert response.duplicate_count == 1
    assert response.unique_count == 4
    assert response.uncertain_count == 0
    assert response.group_count == 1
    assert response.grouped_alert_count == 2
    assert response.standalone_count == 2


# 12: repeated processing preserves isolation of completed historical runs
# (two independent runs; the first run's persisted results are untouched
# by the second).
def test_repeated_processing_preserves_prior_run_isolation(db_path):
    record = _prepare()
    run1 = run_replay(record.id)
    results1_before = repository.load_run_results(run1.id, record.id)

    run2 = run_replay(record.id)
    assert run2.id != run1.id

    results1_after = repository.load_run_results(run1.id, record.id)
    assert [r.model_dump(mode="json") for r in results1_before] == [r.model_dump(mode="json") for r in results1_after]


# 19, 20: imported datasets are processed without synthetic-fixture
# substitution, and ground-truth labels (even deliberately misleading ones)
# never influence operational disposition or grouping.
def test_ground_truth_never_influences_classification_or_grouping(db_path):
    with_gt = _prepare(include_ground_truth=True, name="With Ground Truth")
    without_gt = _prepare(include_ground_truth=False, name="Without Ground Truth")

    run_with = run_replay(with_gt.id)
    run_without = run_replay(without_gt.id)

    by_id_with = _by_source_id(with_gt, run_with.results)
    by_id_without = _by_source_id(without_gt, run_without.results)

    for aid in ("A1", "A2", "A3", "A4", "A5"):
        assert by_id_with[aid].disposition == by_id_without[aid].disposition
    # The FALSE ground truth claims A4+A5 are one incident — operational
    # grouping must not have been nudged toward that: both remain standalone.
    assert by_id_with["A4"].incident_id is None
    assert by_id_with["A5"].incident_id is None
    assert all(r.is_synthetic is False for r in run_with.results)
