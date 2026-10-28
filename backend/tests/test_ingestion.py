"""End-to-end ingestion tests against THREE genuinely distinct datasets,
per the milestone's explicit requirement that this not only work for one
canonical-format fixture. Also covers CSV, JSONL, and a multi-file ZIP
bundle, and the full import -> schema -> mapping -> validate -> load ->
replay lifecycle for real (no mocking of the ingestion layer itself).
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.dataset_registry import DatasetNotFoundError, registry
from app.ingestion.base import UnsupportedFormatError
from app.ingestion.field_mapping import classify_file, suggest_field_mappings
from app.ingestion.import_service import (
    ImportError_,
    confirm_mapping,
    expand_uploaded_files,
    import_dataset,
    load_dataset,
    run_replay,
    validate_dataset,
)
from app.ingestion.zip_bundle_adapter import UnsafeZipError, extract_zip_bundle
from app.schemas.dataset import FieldMappingEntry


# --- Dataset 1: canonical field names, JSONL (matches the product's own vocabulary exactly) ---

DATASET_1_JSONL = "\n".join(
    json.dumps(r)
    for r in [
        {"id": "D1-A1", "timestamp": "2026-03-01T10:00:00Z", "service": "checkout-svc", "signal": "CPU > 95%", "severity": "critical"},
        {"id": "D1-A2", "timestamp": "2026-03-01T10:02:00Z", "service": "payments-db", "signal": "Write latency > 2s", "severity": "critical"},
        {"id": "D1-A3", "timestamp": "2026-03-01T10:04:00Z", "service": "checkout-svc", "signal": "Queue backlog growing", "severity": "high"},
    ]
).encode("utf-8")


# --- Dataset 2: different field names and column ordering (CSV) ---

DATASET_2_CSV = (
    "level,application,alert_name,event_time,host,details\r\n"
    "Major,orders-api,LatencySpike,2026-03-02T08:00:00Z,host-7,p99 latency above threshold\r\n"
    "Critical,orders-db,DiskFull,2026-03-02T08:01:30Z,host-8,disk usage at 97 percent\r\n"
).encode("utf-8")


# --- Dataset 3: ambiguous/abbreviated fields requiring manual mapping ---

DATASET_3_AMBIGUOUS = json.dumps(
    {
        "events": [
            {"t": "2026-03-03T12:00:00Z", "svc": "search-idx", "lvl": "2", "desc": "index rebuild stalled"},
            {"t": "2026-03-03T12:05:00Z", "svc": "search-idx", "lvl": "2", "desc": "index rebuild still stalled"},
        ]
    }
).encode("utf-8")


def test_dataset1_canonical_names_auto_classified_and_mapped():
    record = import_dataset("Dataset 1 — Canonical JSONL", [("alerts.jsonl", DATASET_1_JSONL)])
    mapping = record.file_mappings["alerts.jsonl"]
    assert mapping.category == "alerts"
    mapped_canonical_fields = {m.canonical_field for m in mapping.field_mappings}
    assert {"timestamp", "service_name", "severity", "message"} <= mapped_canonical_fields

    validated = validate_dataset(record.id)
    assert validated.validation_report.valid_records == 3
    assert validated.validation_report.invalid_records == 0

    loaded = load_dataset(record.id)
    assert loaded.lifecycle_state == "loaded"
    assert loaded.store is not None

    run = run_replay(record.id)
    assert len(run.results) == 3
    assert all(r.error is None for r in run.results)


def test_dataset2_different_field_names_map_to_same_canonical_schema():
    record = import_dataset("Dataset 2 — Renamed CSV", [("alerts.csv", DATASET_2_CSV)])
    mapping = record.file_mappings["alerts.csv"]
    assert mapping.category == "alerts"
    by_canonical = {m.canonical_field: m.source_field for m in mapping.field_mappings}
    assert by_canonical["timestamp"] == "event_time"
    assert by_canonical["service_name"] == "application"
    assert by_canonical["severity"] == "level"
    assert by_canonical["rule_name"] == "alert_name"
    assert by_canonical["resource_id"] == "host"

    validated = validate_dataset(record.id)
    assert validated.validation_report.valid_records == 2
    assert validated.canonical_alerts[0].service_name == "orders-api"
    assert validated.canonical_alerts[1].severity.value == "critical"  # "Critical" -> coerced


def test_dataset3_ambiguous_fields_require_explicit_mapping():
    record = import_dataset("Dataset 3 — Ambiguous", [("events.json", DATASET_3_AMBIGUOUS)])
    mapping = record.file_mappings["events.json"]
    # abbreviated field names ("t", "svc", "lvl", "desc") don't match any
    # synonym — the system must not guess silently.
    assert mapping.category == "unknown"
    assert mapping.field_mappings == []

    # Validating without a confirmed mapping normalizes nothing usable —
    # the "unknown" category has no normalizer, so nothing is silently
    # treated as alerts data.
    validated = validate_dataset(record.id)
    assert validated.canonical_alerts == []
    assert any("no normalizer" in w for w in validated.validation_report.warnings)

    # The user explicitly maps the fields instead of the system guessing.
    confirm_mapping(
        record.id,
        "events.json",
        "alerts",
        [
            FieldMappingEntry(source_field="t", canonical_field="timestamp"),
            FieldMappingEntry(source_field="svc", canonical_field="service_name"),
            FieldMappingEntry(source_field="desc", canonical_field="message"),
        ],
    )
    revalidated = validate_dataset(record.id)
    assert revalidated.validation_report.valid_records == 2
    assert revalidated.canonical_alerts[0].service_name == "search-idx"


def test_zip_bundle_with_alerts_and_dependencies():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("alerts.jsonl", DATASET_1_JSONL)
        zf.writestr("dependencies.csv", "source_service,target_service\r\ncheckout-svc,payments-db\r\n")
        zf.writestr("README.txt", "not a dataset file")  # should be skipped, not error

    files = extract_zip_bundle(buf.getvalue())
    names = {name for name, _ in files}
    assert names == {"alerts.jsonl", "dependencies.csv"}  # README.txt correctly excluded

    record = import_dataset("Dataset Bundle", files)
    assert record.file_mappings["dependencies.csv"].category == "dependencies"
    validated = validate_dataset(record.id)
    assert len(validated.canonical_dependencies) == 1
    assert validated.canonical_dependencies[0].source_service == "checkout-svc"

    loaded = load_dataset(record.id)
    assert any(d.service == "checkout-svc" and d.depends_on == "payments-db" for d in loaded.store.dependencies)


def test_zip_path_traversal_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../evil.jsonl", DATASET_1_JSONL)
    with pytest.raises(UnsafeZipError):
        extract_zip_bundle(buf.getvalue())


def test_unsupported_format_fails_clearly():
    with pytest.raises(UnsupportedFormatError):
        import_dataset("Bad Format", [("data.xlsx", b"not supported")])


def test_malformed_json_record_counted_not_silently_dropped():
    bad = b'{"alerts": [{"id":"ok","timestamp":"2026-01-01T00:00:00Z","service":"x","signal":"s","severity":"high"}, "not-an-object"]}'
    record = import_dataset("Malformed JSON", [("alerts.json", bad)])
    validated = validate_dataset(record.id)
    assert validated.validation_report.total_records_examined == 2
    assert validated.validation_report.valid_records == 1
    assert validated.validation_report.invalid_records == 1


def test_datasets_remain_isolated_from_each_other():
    d1 = import_dataset("Isolation A", [("alerts.jsonl", DATASET_1_JSONL)])
    d2 = import_dataset("Isolation B", [("alerts.csv", DATASET_2_CSV)])
    validate_dataset(d1.id)
    validate_dataset(d2.id)
    load_dataset(d1.id)
    load_dataset(d2.id)
    run_replay(d1.id)
    run_replay(d2.id)

    d1_record = registry.get(d1.id)
    d2_record = registry.get(d2.id)
    assert d1_record.store is not d2_record.store
    d1_alert_ids = set(d1_record.store.all_alerts.keys())
    d2_alert_ids = set(d2_record.store.all_alerts.keys())
    assert d1_alert_ids.isdisjoint(d2_alert_ids)


def test_switching_active_dataset_changes_what_routers_see():
    d1 = import_dataset("Switch Test", [("alerts.jsonl", DATASET_1_JSONL)])
    validate_dataset(d1.id)
    load_dataset(d1.id)

    assert registry.active_dataset_id == "default"
    registry.set_active(d1.id)
    assert registry.get_active().id == d1.id
    assert registry.get_active().store.seed_demo_fixtures is False

    registry.set_active("default")  # restore for other tests sharing the module-level registry
    assert registry.get_active().id == "default"


def test_repeated_import_does_not_duplicate_within_a_dataset():
    """Idempotency: validating the same dataset twice must not double its
    record counts (deterministic event_ids collapse on re-normalization,
    and validate_dataset rebuilds canonical_alerts from scratch each call
    rather than appending).
    """
    record = import_dataset("Repeat Validate", [("alerts.jsonl", DATASET_1_JSONL)])
    first = validate_dataset(record.id)
    second = validate_dataset(record.id)
    assert len(first.canonical_alerts) == len(second.canonical_alerts) == 3
    assert {a.event_id for a in first.canonical_alerts} == {a.event_id for a in second.canonical_alerts}


def test_unknown_dataset_id_raises():
    with pytest.raises(DatasetNotFoundError):
        registry.get("does-not-exist")


def test_load_before_validate_fails_clearly():
    record = import_dataset("Load Too Early", [("alerts.jsonl", DATASET_1_JSONL)])
    with pytest.raises(ImportError_):
        load_dataset(record.id)


def test_expand_uploaded_files_flattens_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("alerts.jsonl", DATASET_1_JSONL)
    expanded = expand_uploaded_files([("bundle.zip", buf.getvalue())])
    assert [name for name, _ in expanded] == ["alerts.jsonl"]


def test_default_dataset_untouched_and_still_shows_demo_fixtures():
    """Backward compatibility: the pre-existing global store/orchestrator
    (used directly by tests in test_pipeline.py etc.) must still work
    exactly as before, wrapped as the "default" dataset.
    """
    default = registry.get("default")
    assert default.provenance == "synthetic_demo"
    assert default.lifecycle_state == "loaded"
    assert len(default.store.incidents) == 3  # the 3 built-in fixture incidents, untouched
