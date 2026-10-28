"""Tests for the SQLite dataset-persistence layer (app/persistence/).

Each test gets its OWN fresh temporary database (via the `db_path` fixture
below), distinct from the shared session-level test database the rest of
the suite uses (see conftest.py's `_test_database_path`) — these tests
specifically need to simulate "close the process, reopen the database" by
constructing fresh DatasetRegistry/repository calls against the same file.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.dataset_registry import DatasetRegistry
from app.ingestion.import_service import confirm_mapping, import_dataset, load_dataset, validate_dataset
from app.persistence import repository
from app.persistence.db import get_connection
from app.schemas.dataset import FieldMappingEntry

ALERTS_JSONL = "\n".join(
    json.dumps(r)
    for r in [
        {"id": "P-A1", "timestamp": "2026-06-01T10:00:00Z", "service": "checkout-svc", "signal": "CPU > 95%", "severity": "critical"},
        {"id": "P-A2", "timestamp": "2026-06-01T10:02:00Z", "service": "payments-db", "signal": "Write latency > 2s", "severity": "critical"},
    ]
).encode("utf-8")


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "persistence_test.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    return path


# 1. Import a dataset; fresh registry/repository instance against the same
#    DB; the dataset still exists.
def test_dataset_survives_a_fresh_registry_against_the_same_db(db_path):
    record = import_dataset("Persistence Test 1", [("alerts.jsonl", ALERTS_JSONL)])
    dataset_id = record.id

    fresh_registry = DatasetRegistry()  # simulates a brand-new process
    fresh_registry.hydrate_from_db()

    assert dataset_id in fresh_registry.datasets
    restored = fresh_registry.datasets[dataset_id]
    assert restored.name == "Persistence Test 1"
    assert restored.provenance == "uploaded"


# 2. Original file bytes survive recreation and can be used for validation.
def test_raw_file_bytes_survive_and_can_be_revalidated(db_path):
    record = import_dataset("Persistence Test 2", [("alerts.jsonl", ALERTS_JSONL)])
    dataset_id = record.id

    fresh_registry = DatasetRegistry()
    fresh_registry.hydrate_from_db()
    restored = fresh_registry.datasets[dataset_id]

    assert restored.raw_file_bytes["alerts.jsonl"] == ALERTS_JSONL

    # Revalidating after "restart" must work off the restored raw bytes —
    # validate_dataset() reads from registry.get(), so point the real
    # module-level registry at the restored record to exercise this
    # exactly as the API would after a real process restart.
    from app.dataset_registry import registry as live_registry
    live_registry.datasets[dataset_id] = restored
    try:
        validated = validate_dataset(dataset_id)
        assert validated.validation_report.valid_records == 2
    finally:
        live_registry.datasets.pop(dataset_id, None)


# 3. Suggested and confirmed mappings persist correctly.
def test_suggested_and_confirmed_mappings_persist(db_path):
    record = import_dataset("Persistence Test 3", [("alerts.jsonl", ALERTS_JSONL)])
    dataset_id = record.id

    suggested = repository.load_all_datasets()
    suggested_record = next(r for r in suggested if r.id == dataset_id)
    assert suggested_record.file_mappings["alerts.jsonl"].category_confidence == "suggested"
    assert {m.canonical_field for m in suggested_record.file_mappings["alerts.jsonl"].field_mappings} >= {
        "timestamp", "service_name", "severity", "message",
    }

    confirm_mapping(
        dataset_id, "alerts.jsonl", "alerts",
        [FieldMappingEntry(source_field="service", canonical_field="service_name"),
         FieldMappingEntry(source_field="timestamp", canonical_field="timestamp")],
    )

    reloaded = repository.load_all_datasets()
    reloaded_record = next(r for r in reloaded if r.id == dataset_id)
    assert reloaded_record.file_mappings["alerts.jsonl"].category_confidence == "confirmed"
    confirmed_fields = {m.canonical_field for m in reloaded_record.file_mappings["alerts.jsonl"].field_mappings}
    assert confirmed_fields == {"service_name", "timestamp"}
    assert reloaded_record.lifecycle_state == "mapped"


# 4. Canonical records and validation reports persist correctly.
def test_canonical_records_and_validation_report_persist(db_path):
    record = import_dataset("Persistence Test 4", [("alerts.jsonl", ALERTS_JSONL)])
    validate_dataset(record.id)

    reloaded = repository.load_all_datasets()
    reloaded_record = next(r for r in reloaded if r.id == record.id)
    assert len(reloaded_record.canonical_alerts) == 2
    assert {a.service_name for a in reloaded_record.canonical_alerts} == {"checkout-svc", "payments-db"}
    assert reloaded_record.validation_report is not None
    assert reloaded_record.validation_report.valid_records == 2


# 5. Revalidation replaces previous normalized records without duplicating them.
def test_revalidation_replaces_not_accumulates(db_path):
    record = import_dataset("Persistence Test 5", [("alerts.jsonl", ALERTS_JSONL)])
    validate_dataset(record.id)
    validate_dataset(record.id)
    validate_dataset(record.id)

    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM canonical_records WHERE dataset_id = ? AND category = 'alerts'", (record.id,)
        ).fetchone()[0]
    assert count == 2  # not 6


# 6. The active dataset selection persists.
def test_active_dataset_selection_persists(db_path):
    from app.dataset_registry import registry as live_registry

    record = import_dataset("Persistence Test 6", [("alerts.jsonl", ALERTS_JSONL)])
    live_registry.set_active(record.id)
    try:
        assert repository.load_active_dataset_id() == record.id

        fresh_registry = DatasetRegistry()
        fresh_registry.hydrate_from_db()
        assert fresh_registry.active_dataset_id == record.id
    finally:
        live_registry.set_active("default")


# 7. A failed import or validation transaction does not leave inconsistent
#    partial data.
def test_failed_validation_transaction_leaves_prior_data_intact(db_path, monkeypatch):
    record = import_dataset("Persistence Test 7", [("alerts.jsonl", ALERTS_JSONL)])
    validate_dataset(record.id)  # 2 good rows persisted

    def _boom(*args, **kwargs):
        raise ValueError("simulated failure mid-transaction")

    # sqlite3.Connection is an immutable C type — its methods can't be
    # monkeypatched directly. Breaking json.dumps instead fails the row
    # serialization step, which happens AFTER save_validation()'s DELETE
    # has already executed but still inside the same `with conn:` block —
    # so the exception still forces that DELETE to roll back too.
    monkeypatch.setattr("app.persistence.repository.json.dumps", _boom)
    by_category = {"alerts": record.canonical_alerts}
    with pytest.raises(ValueError):
        repository.save_validation(record.id, by_category, record.validation_report, "validated")
    # No manual monkeypatch.undo() here: this fixture's monkeypatch
    # instance is shared with the `db_path` fixture above, and undo()
    # reverts EVERY patch made through that instance, including
    # DATABASE_PATH — pytest restores everything automatically at
    # teardown, which is the correct place for it.

    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM canonical_records WHERE dataset_id = ? AND category = 'alerts'", (record.id,)
        ).fetchone()[0]
    assert count == 2  # the DELETE was rolled back along with the failed INSERT — old rows survived


# 8. The synthetic default dataset and multiple uploaded datasets remain isolated.
def test_default_dataset_never_persisted_and_uploads_stay_isolated(db_path):
    d1 = import_dataset("Persistence Test 8a", [("alerts.jsonl", ALERTS_JSONL)])
    d2 = import_dataset("Persistence Test 8b", [("alerts.jsonl", ALERTS_JSONL)])
    validate_dataset(d1.id)
    validate_dataset(d2.id)

    with get_connection() as conn:
        ids = {row[0] for row in conn.execute("SELECT id FROM dataset_meta")}
    assert "default" not in ids
    assert {d1.id, d2.id} <= ids

    fresh_registry = DatasetRegistry()
    fresh_registry.hydrate_from_db()
    assert fresh_registry.datasets["default"].provenance == "synthetic_demo"
    assert len(fresh_registry.datasets["default"].store.incidents) == 3
    assert fresh_registry.datasets[d1.id].canonical_alerts is not fresh_registry.datasets[d2.id].canonical_alerts


# 9. Existing ingestion and dataset API tests continue passing — covered by
# running `pytest tests/` as a whole (see the full-suite run in the final
# report); nothing additional needed in this file for that.
