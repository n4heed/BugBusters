"""Phase 2: processing a dataset's own persisted canonical alert records
through the real pipeline, and persisting the run/results/groups to SQLite.

Each test gets its own fresh temporary database (the `db_path` fixture),
same convention as test_persistence.py.
"""
from __future__ import annotations

import json

import pytest

from app.dataset_registry import DatasetNotFoundError, DatasetRegistry
from app.fixtures_pipeline import REPLAY_ALERTS
from app.ingestion.import_service import (
    ImportError_,
    confirm_mapping,
    import_dataset,
    load_dataset,
    run_replay,
    validate_dataset,
)
from app.persistence import repository
from app.persistence.db import get_connection
from app.schemas.dataset import FieldMappingEntry

ALERTS_JSONL = "\n".join(
    json.dumps(r)
    for r in [
        {"id": "PP-A1", "timestamp": "2026-07-01T10:00:00Z", "service": "checkout-svc", "signal": "CPU > 95%", "severity": "critical"},
        {"id": "PP-A2", "timestamp": "2026-07-01T10:02:00Z", "service": "payments-db", "signal": "Write latency > 2s", "severity": "critical"},
        {"id": "PP-A3", "timestamp": "2026-07-01T10:04:00Z", "service": "checkout-svc", "signal": "Queue backlog growing", "severity": "high"},
    ]
).encode("utf-8")


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "processing_test.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    return path


def _prepare_validated_loaded_dataset(name: str = "Processing Test"):
    record = import_dataset(name, [("alerts.jsonl", ALERTS_JSONL)])
    validate_dataset(record.id)
    load_dataset(record.id)
    return record


# 1 & 2. A validated dataset can be loaded and processed using its persisted
# canonical alert records; result count matches eligible alert count.
def test_processing_uses_persisted_canonical_records(db_path):
    record = _prepare_validated_loaded_dataset()
    run = run_replay(record.id)
    assert len(run.results) == len(record.canonical_alerts) == 3


# 3. Imported processing never invokes the synthetic global replay fixture.
def test_processing_never_touches_the_demo_replay_fixture(db_path):
    record = _prepare_validated_loaded_dataset()
    run = run_replay(record.id)
    processed_alert_ids = {r.alert_id for r in run.results}
    fixture_ids = {a["id"] for a in REPLAY_ALERTS}
    assert processed_alert_ids.isdisjoint(fixture_ids)


# 4. The imported dataset's ID is preserved throughout processing.
def test_dataset_id_preserved_through_processing(db_path):
    record = _prepare_validated_loaded_dataset()
    run = run_replay(record.id)
    assert run.dataset_id == record.id
    summary = repository.load_run_summary(run.id, record.id)
    assert summary["dataset_id"] == record.id


# 5. The persisted run contains the actual per-alert ProcessingResult output.
def test_persisted_run_contains_real_processing_results(db_path):
    record = _prepare_validated_loaded_dataset()
    run = run_replay(record.id)
    persisted = repository.load_run_results(run.id, record.id)
    assert persisted is not None
    assert len(persisted) == 3
    assert {r.alert_id for r in persisted} == {r.alert_id for r in run.results}
    # Real structured output, not a flattened string — triage decisions are present.
    assert all(r.triage is not None for r in persisted)


# 6. Run summaries return accurate counts derived from persisted results.
def test_run_summary_counts_are_accurate(db_path):
    record = _prepare_validated_loaded_dataset()
    run = run_replay(record.id)
    summary = repository.load_run_summary(run.id, record.id)
    assert summary["status"] == "completed"
    assert summary["eligible_alert_count"] == 3
    assert summary["processed_count"] == 3
    assert sum(summary["workflow_status_counts"].values()) == 3


# 7. Per-alert results survive destruction and recreation of the
# registry/repository context.
def test_results_survive_fresh_registry_recreation(db_path):
    record = _prepare_validated_loaded_dataset()
    run = run_replay(record.id)

    fresh_registry = DatasetRegistry()
    fresh_registry.hydrate_from_db()
    assert record.id in fresh_registry.datasets
    # The fresh registry's in-memory .runs is empty (never persisted there)
    # — results must come from the repository directly, which is what the
    # restart-safe endpoint (tested separately below) actually does.
    assert fresh_registry.datasets[record.id].runs == {}
    persisted = repository.load_run_results(run.id, record.id)
    assert persisted is not None and len(persisted) == 3


# 8 & 9. The existing run-results endpoint retrieves a persisted run after a
# simulated backend restart, including when the in-memory Store/Orchestrator
# were lost (not just DatasetRecord.runs).
def test_get_run_endpoint_is_restart_safe(db_path, monkeypatch):
    import app.routers.datasets as datasets_router

    record = _prepare_validated_loaded_dataset()
    run = run_replay(record.id)

    fresh_registry = DatasetRegistry()
    fresh_registry.hydrate_from_db()
    restored = fresh_registry.datasets[record.id]
    assert restored.store is None and restored.orchestrator is None  # runtime state genuinely lost
    assert restored.runs == {}  # in-memory run history genuinely lost

    monkeypatch.setattr(datasets_router, "registry", fresh_registry)
    results = datasets_router.get_run_endpoint(record.id, run.id)
    assert len(results) == 3
    assert {r.alert_id for r in results} == {r.alert_id for r in run.results}

    # And processing works again from this "post-restart" state, without
    # erroring just because .store/.orchestrator were None.
    run2 = run_replay(record.id)
    assert len(run2.results) == 3
    assert run2.id != run.id


# 10. Multiple datasets remain isolated, including the default synthetic-demo
# dataset, which is never processable via this canonical-records path.
def test_default_dataset_is_never_processed_and_datasets_stay_isolated(db_path):
    from app.dataset_registry import registry as live_registry

    with pytest.raises(ImportError_):
        run_replay("default")  # has no canonical_alerts — must error, never silently no-op

    d1 = _prepare_validated_loaded_dataset("Isolation A")
    d2 = _prepare_validated_loaded_dataset("Isolation B")
    run1 = run_replay(d1.id)
    run2 = run_replay(d2.id)

    r1 = repository.load_run_results(run1.id, d1.id)
    r2 = repository.load_run_results(run2.id, d2.id)
    assert {r.alert_id for r in r1}.isdisjoint({r.alert_id for r in r2})
    # A run_id belonging to d1 must not resolve under d2's dataset scope.
    assert repository.load_run_results(run1.id, d2.id) is None
    assert live_registry.datasets["default"].canonical_alerts == []


# 11. Repeated processing requests: chosen behavior is two independent,
# isolated, fully-reprocessed runs (documented in run_replay()'s docstring).
def test_repeated_processing_creates_independent_runs(db_path):
    record = _prepare_validated_loaded_dataset()
    run1 = run_replay(record.id)
    run2 = run_replay(record.id)
    assert run1.id != run2.id
    assert len(run1.results) == len(run2.results) == 3

    with get_connection() as conn:
        run_count = conn.execute(
            "SELECT COUNT(*) FROM processing_runs WHERE dataset_id = ?", (record.id,)
        ).fetchone()[0]
        result_rows = conn.execute(
            "SELECT COUNT(*) FROM processing_results WHERE dataset_id = ?", (record.id,)
        ).fetchone()[0]
    assert run_count == 2
    assert result_rows == 6  # 3 per run, not double-counted within either run


# 12. Failed or interrupted runs cannot be returned as completed.
def test_failed_run_is_marked_failed_not_completed(db_path, monkeypatch):
    record = _prepare_validated_loaded_dataset()

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated pipeline crash")

    # Fail inside the actual processing loop (after load_dataset() rebuilds
    # a fresh orchestrator) by breaking the orchestrator class method itself.
    import app.orchestrator as orchestrator_module
    monkeypatch.setattr(orchestrator_module.Orchestrator, "process_alert", _boom)

    with pytest.raises(ImportError_):
        run_replay(record.id)

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT status, processed_count FROM processing_runs WHERE dataset_id = ?", (record.id,)
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "failed"
    assert rows[0][1] == 0

    # The dataset itself must remain usable afterward (reverted, not stuck).
    from app.dataset_registry import registry as live_registry
    assert live_registry.get(record.id).lifecycle_state == "loaded"


# 13. Database write failures do not leave an inconsistent successful
# in-memory state — regression tests for the Section 7 fix.
def test_confirm_mapping_failure_leaves_in_memory_record_untouched(db_path, monkeypatch):
    record = import_dataset("Consistency Test", [("alerts.jsonl", ALERTS_JSONL)])
    original_mapping = record.file_mappings["alerts.jsonl"]

    def _boom(*a, **k):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr("app.persistence.repository.get_connection", _boom)
    with pytest.raises(RuntimeError):
        confirm_mapping(
            record.id, "alerts.jsonl", "alerts",
            [FieldMappingEntry(source_field="service", canonical_field="service_name")],
        )

    # In-memory state must be EXACTLY what it was before the failed call.
    assert record.file_mappings["alerts.jsonl"] is original_mapping
    assert record.lifecycle_state == "imported"


def test_set_active_failure_leaves_in_memory_selection_untouched(db_path, monkeypatch):
    from app.dataset_registry import registry as live_registry

    record = import_dataset("Consistency Test 2", [("alerts.jsonl", ALERTS_JSONL)])
    live_registry.set_active("default")
    assert live_registry.active_dataset_id == "default"

    def _boom(*a, **k):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr("app.persistence.repository.get_connection", _boom)
    with pytest.raises(RuntimeError):
        live_registry.set_active(record.id)

    assert live_registry.active_dataset_id == "default"  # unchanged, not half-switched
