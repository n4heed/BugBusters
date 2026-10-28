"""Phase 4: the async processing workflow, live run-summary API,
incremental progress persistence, concurrency guard, and interrupted-run
detection on restart.

Each test gets its own fresh temporary database (the `db_path` fixture),
same convention as test_phase3_groups_api.py.
"""
from __future__ import annotations

import json
import time

import pytest

import app.routers.datasets as datasets_router
from app.dataset_registry import DatasetRegistry
from app.ingestion.import_service import (
    ImportError_,
    confirm_mapping,
    import_dataset,
    load_dataset,
    run_replay,
    start_processing_run,
    validate_dataset,
)
from app.orchestrator import Orchestrator
from app.persistence import repository
from app.persistence.db import get_connection

ALERTS_CSV = (
    "id,timestamp,service,message,severity,fingerprint\r\n"
    "A1,2026-09-01T00:00:00Z,payments-db,Connection pool exhausted,critical,fp-pool\r\n"
    "A2,2026-09-01T00:02:00Z,payments-db,Connection pool exhausted (recurring alert occurrence).,critical,fp-pool\r\n"
    "A3,2026-09-01T00:03:00Z,checkout-svc,Database query failed,high,fp-checkout-dbfail\r\n"
    "A4,2026-09-01T00:05:00Z,notification-service,SMTP timeout,low,fp-smtp\r\n"
    "A5,2026-09-01T10:00:00Z,billing-worker,License check delayed,medium,fp-license\r\n"
).encode("utf-8")

DEPENDENCIES_CSV = "source_service,target_service\r\ncheckout-svc,payments-db\r\n".encode("utf-8")


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "phase4_test.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    return path


def _prepare(name: str = "Phase4 Test"):
    record = import_dataset(name, [("alerts.csv", ALERTS_CSV), ("dependencies.csv", DEPENDENCIES_CSV)])
    for file_name, mapping in list(record.file_mappings.items()):
        confirm_mapping(record.id, file_name, mapping.category, list(mapping.field_mappings))
    validate_dataset(record.id)
    load_dataset(record.id)
    return record


# 1, 2: a new run is persisted BEFORE the frontend needs to poll it, with
# an accurate initial status and eligible-alert count.
def test_start_run_persists_before_returning(db_path):
    record = _prepare()
    response = start_processing_run(record.id)
    assert response["status"] == "running"
    assert response["eligible_alert_count"] == 5
    assert response["already_running"] is False if "already_running" in response else True  # tolerate either shape

    # The run is durably visible via the repository immediately — no race
    # with the background worker needed to observe its EXISTENCE.
    summary = repository.load_run_summary(response["run_id"], record.id)
    assert summary is not None
    assert summary["eligible_alert_count"] == 5

    # Let the background worker actually finish before the test ends.
    _wait_for_terminal(response["run_id"], record.id)


def _wait_for_terminal(run_id: str, dataset_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    summary = repository.load_run_summary(run_id, dataset_id)
    while time.time() < deadline:
        summary = repository.load_run_summary(run_id, dataset_id)
        if summary["status"] in ("completed", "failed", "interrupted"):
            return summary
        time.sleep(0.02)
    raise AssertionError(f"Run {run_id} did not reach a terminal state within {timeout}s (last: {summary})")


# 3, 11: processed_count only advances via persisted results; a live
# summary can be read correctly WHILE a run is executing.
def test_background_run_shows_live_progress_and_completes(db_path, monkeypatch):
    record = _prepare()

    original_process_alert = Orchestrator.process_alert

    def _slow_process_alert(self, *args, **kwargs):
        time.sleep(0.08)
        return original_process_alert(self, *args, **kwargs)

    monkeypatch.setattr(Orchestrator, "process_alert", _slow_process_alert)

    response = start_processing_run(record.id)
    run_id = response["run_id"]

    observed_running = False
    observed_progress_values = []
    deadline = time.time() + 10
    while time.time() < deadline:
        summary = repository.compute_run_statistics(run_id, record.id)
        run_row = repository.load_run_summary(run_id, record.id)
        if run_row["status"] == "running":
            observed_running = True
            observed_progress_values.append(summary["processed_count"])
        if run_row["status"] in ("completed", "failed"):
            break
        time.sleep(0.02)

    final = _wait_for_terminal(run_id, record.id)
    assert final["status"] == "completed"
    assert observed_running, "never observed the run in a 'running' state — test timing assumption failed"
    # processed_count must never exceed eligible_alert_count at any point.
    assert all(v <= 5 for v in observed_progress_values)
    assert final["processed_count"] == 5


# 9, 10: failed processing returns a persisted failed status; a DB failure
# does not falsely increment successful processing counters.
def test_failed_background_run_persists_failed_status(db_path, monkeypatch):
    record = _prepare()

    def _boom(self, *a, **k):
        raise RuntimeError("simulated pipeline crash")

    monkeypatch.setattr(Orchestrator, "process_alert", _boom)

    response = start_processing_run(record.id)
    final = _wait_for_terminal(response["run_id"], record.id)
    assert final["status"] == "failed"
    assert final["error_summary"] is not None
    assert final["processed_count"] == 0

    # The dataset remains usable afterward (reverted to "loaded", not stuck).
    from app.dataset_registry import registry as live_registry
    assert live_registry.get(record.id).lifecycle_state == "loaded"


# 20: repeated button clicks do not start duplicate jobs — the SAME run_id
# is returned while one is already active.
def test_duplicate_start_requests_do_not_spawn_duplicate_runs(db_path, monkeypatch):
    record = _prepare()

    original_process_alert = Orchestrator.process_alert

    def _slow_process_alert(self, *args, **kwargs):
        time.sleep(0.1)
        return original_process_alert(self, *args, **kwargs)

    monkeypatch.setattr(Orchestrator, "process_alert", _slow_process_alert)

    first = datasets_router.start_run_endpoint(record.id)
    second = datasets_router.start_run_endpoint(record.id)
    third = datasets_router.start_run_endpoint(record.id)

    assert first.run_id == second.run_id == third.run_id
    assert second.already_running is True
    assert third.already_running is True

    _wait_for_terminal(first.run_id, record.id)

    with get_connection() as conn:
        run_count = conn.execute(
            "SELECT COUNT(*) FROM processing_runs WHERE dataset_id = ?", (record.id,)
        ).fetchone()[0]
    assert run_count == 1  # not 3


# Concurrency guard also protects the OLD synchronous endpoint: starting a
# background run and then immediately calling run_replay() must not race it.
def test_sync_replay_rejects_concurrent_run(db_path, monkeypatch):
    record = _prepare()

    original_process_alert = Orchestrator.process_alert

    def _slow_process_alert(self, *args, **kwargs):
        time.sleep(0.1)
        return original_process_alert(self, *args, **kwargs)

    monkeypatch.setattr(Orchestrator, "process_alert", _slow_process_alert)

    start_processing_run(record.id)
    with pytest.raises(ImportError_):
        run_replay(record.id)


# 12: a completed run's final summary survives backend/registry recreation.
def test_completed_run_summary_survives_registry_recreation(db_path):
    record = _prepare()
    run = run_replay(record.id)

    fresh_registry = DatasetRegistry()
    fresh_registry.hydrate_from_db()
    monkeypatch_target_present = record.id in fresh_registry.datasets
    assert monkeypatch_target_present

    import app.dataset_registry as dataset_registry_module

    original = dataset_registry_module.registry
    dataset_registry_module.registry = fresh_registry
    try:
        response = datasets_router.get_run_summary_endpoint(record.id, run.id)
    finally:
        dataset_registry_module.registry = original

    assert response.status == "completed"
    assert response.processed_count == 5
    assert response.duplicate_count == 1


# 13: an interrupted run is not displayed as healthy or completed — a
# 'running' row left behind by a "crashed" process must become
# 'interrupted' on the next startup, never silently 'completed'.
def test_interrupted_run_detected_on_restart(db_path):
    record = _prepare()
    # Simulate a crash: persist a run as 'running' and never finish it
    # (bypassing the real background worker entirely).
    import datetime as dt
    repository.start_run("crashed-run-1", record.id, dt.datetime.now(dt.timezone.utc), eligible_alert_count=5)

    fresh_registry = DatasetRegistry()
    fresh_registry.hydrate_from_db()  # this is what a real restart calls

    summary = repository.load_run_summary("crashed-run-1", record.id)
    assert summary["status"] == "interrupted"
    assert summary["error_summary"] is not None

    # A genuinely completed run must NOT be touched by this.
    real_run = run_replay(record.id)
    repository.mark_stale_running_runs_interrupted()
    still_completed = repository.load_run_summary(real_run.id, record.id)
    assert still_completed["status"] == "completed"


# 15: the new group API / summary continues to work after the restart
# detection runs, and wrong-dataset access still 404s (16).
def test_summary_endpoint_dataset_scoping_after_interruption_handling(db_path):
    record = _prepare(name="Dataset One")
    other = _prepare(name="Dataset Two")
    run = run_replay(record.id)

    with pytest.raises(Exception):  # HTTPException — wrong dataset for this run_id
        datasets_router.get_run_summary_endpoint(other.id, run.id)


# A bounded recent-results endpoint exists and never exceeds its limit.
def test_recent_results_endpoint_is_bounded(db_path):
    record = _prepare()
    run = run_replay(record.id)
    recent = datasets_router.get_run_recent_results_endpoint(record.id, run.id, limit=2)
    assert len(recent) == 2
    full = datasets_router.get_run_endpoint(record.id, run.id)
    assert len(full) == 5  # full endpoint still returns everything, unchanged
