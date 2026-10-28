"""Regression test for a real bug: an uploaded dataset's own fingerprint /
dedup-key column was silently discarded during import (not in the field
synonym table at all), forcing duplicate detection to fall back to hashing
service+message text. Two genuine re-occurrences of the same alert whose
message text differs even slightly (a common real-world pattern — e.g. a
"(recurring alert occurrence)" suffix) were then never recognized as
duplicates and flooded the Active Incidents / Safety Queue views instead.

Fix: the dataset's own fingerprint column, when mapped, now flows through
unchanged (ingestion/field_mapping.py -> ingestion/normalization.py ->
ingestion/import_service.canonical_alert_to_raw -> services/normalization.
normalize_alert) and is used as-is for duplicate detection instead of a
recomputed hash — but only when the source actually supplies one, so
demo/simulated alerts (which never include a "fingerprint" key) are
completely unaffected.
"""
from __future__ import annotations

from app.dataset_registry import registry
from app.ingestion.import_service import confirm_mapping, import_dataset, load_dataset, run_replay, validate_dataset
from app.schemas.dataset import FieldMappingEntry

CSV_WITH_FINGERPRINT = (
    "id,timestamp,service,message,severity,fingerprint\r\n"
    "A1,2026-04-01T09:00:00Z,billing-worker,Repeated health checks failed for this service.,warning,fp-billing-hc\r\n"
    "A2,2026-04-01T09:05:00Z,billing-worker,Repeated health checks failed for this service. (recurring alert occurrence).,warning,fp-billing-hc\r\n"
    "A3,2026-04-01T09:10:00Z,checkout-svc,CPU above threshold.,high,fp-checkout-cpu\r\n"
).encode("utf-8")


def _import_and_load(name: str, csv_bytes: bytes):
    record = import_dataset(name, [("alerts.csv", csv_bytes)])
    mapping = record.file_mappings["alerts.csv"]
    confirm_mapping(
        record.id,
        "alerts.csv",
        "alerts",
        [FieldMappingEntry(source_field=m.source_field, canonical_field=m.canonical_field, confidence="confirmed")
         for m in mapping.field_mappings],
    )
    validate_dataset(record.id)
    load_dataset(record.id)
    return record


def test_fingerprint_column_is_auto_suggested_for_mapping():
    record = import_dataset("fp-test", [("alerts.csv", CSV_WITH_FINGERPRINT)])
    mapping = record.file_mappings["alerts.csv"]
    mapped_canonical_fields = {m.canonical_field for m in mapping.field_mappings}
    assert "fingerprint" in mapped_canonical_fields


def test_dataset_fingerprint_drives_duplicate_detection():
    record = _import_and_load("fp-dedup-test", CSV_WITH_FINGERPRINT)
    try:
        run_replay(record.id)
        # The Alert's own workflow_status (not ProcessingResult.workflow_status,
        # whose WorkflowStatus enum has no DUPLICATE member — it reports
        # UNCLUSTERED for a duplicate by design) is what the Active
        # Incidents / Safety Queue views actually read.
        alerts_by_source_id = {a.id: a for a in record.store.all_alerts.values()}
        alert_a1 = next(a for a in alerts_by_source_id.values() if a.signal.startswith("Repeated health checks") and "recurring" not in a.signal)
        alert_a2 = next(a for a in alerts_by_source_id.values() if "recurring alert occurrence" in a.signal)
        alert_a3 = next(a for a in alerts_by_source_id.values() if a.service == "checkout-svc")

        # A2 shares A1's fingerprint and arrives 5 minutes later (well within
        # the 30-minute duplicate window) despite having different message
        # text — it must now be recognized as a duplicate, not a new alert.
        assert alert_a2.workflow_status == "DUPLICATE"
        assert alert_a2.duplicate_of == alert_a1.id
        # A3 has a genuinely different fingerprint and must NOT be flagged.
        assert alert_a3.workflow_status != "DUPLICATE"
    finally:
        registry.datasets.pop(record.id, None)
