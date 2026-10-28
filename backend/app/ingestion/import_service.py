"""Ties the adapters, schema detector, field mapper, validator and
normalizer together into the dataset import workflow (Stages A-F).

Importing NEVER automatically calls an LLM, runs the agent pipeline,
applies tuning recommendations, or marks anything human-approved — those
remain separate, explicit operations (validate/load/replay are distinct
calls; see routers/datasets.py).
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..dataset_registry import DatasetRecord, ReplayRun, registry
from ..models import ServiceDependency
from ..orchestrator import Orchestrator
from ..persistence import repository
from ..schemas.dataset import FieldMappingEntry, FileMapping
from ..store import Store
from .base import SourceAdapter, detect_file_type
from .csv_adapter import CsvAdapter
from .field_mapping import classify_file, suggest_field_mappings
from .json_adapter import JsonAdapter
from .jsonl_adapter import JsonlAdapter
from .schema_detector import build_file_info
from .validation import merge_reports, validate_and_normalize_file
from .zip_bundle_adapter import extract_zip_bundle

ADAPTERS: dict[str, SourceAdapter] = {
    "csv": CsvAdapter(),
    "json": JsonAdapter(),
    "jsonl": JsonlAdapter(),
}

MAX_FILES_PER_IMPORT = 20
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024


class ImportError_(ValueError):  # avoid shadowing builtins.ImportError
    pass


def expand_uploaded_files(uploads: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Expands any .zip entries in-place; non-zip files pass through
    unchanged. Enforces the per-import file-count and per-file size limits.
    """
    expanded: list[tuple[str, bytes]] = []
    for filename, content in uploads:
        if len(content) > MAX_FILE_SIZE_BYTES:
            raise ImportError_(f"{filename}: exceeds the maximum upload size of {MAX_FILE_SIZE_BYTES} bytes.")
        if filename.lower().endswith(".zip"):
            expanded.extend(extract_zip_bundle(content))
        else:
            expanded.append((filename, content))

    if len(expanded) > MAX_FILES_PER_IMPORT:
        raise ImportError_(f"Import contains {len(expanded)} files, exceeding the limit of {MAX_FILES_PER_IMPORT}.")
    for filename, content in expanded:
        if len(content) > MAX_FILE_SIZE_BYTES:
            raise ImportError_(f"{filename}: exceeds the maximum file size of {MAX_FILE_SIZE_BYTES} bytes.")
    return expanded


def import_dataset(name: str, files: list[tuple[str, bytes]]) -> DatasetRecord:
    """Stages A+B+C: parse, detect schema, suggest (not confirm) category
    and field mappings for every file. Nothing is validated or normalized
    into canonical records yet — that happens explicitly in validate_dataset.
    """
    record = DatasetRecord(id=registry.new_dataset_id(), name=name, provenance="uploaded", lifecycle_state="imported")

    for filename, content in files:
        file_type = detect_file_type(filename)  # raises UnsupportedFormatError for an unrecognized extension
        adapter = ADAPTERS[file_type]
        info = build_file_info(filename, content, adapter)
        record.files[filename] = info
        record.raw_file_bytes[filename] = content

        best_guess_category, is_confident = classify_file(info.detected_fields)
        # A low-confidence guess is never silently applied as the category —
        # that's exactly the "guessing silently" the milestone prohibits.
        # It stays "unknown" (forcing an explicit user selection) and the
        # weak guess is surfaced only as a warning, not as the mapping.
        category = best_guess_category if is_confident else "unknown"
        suggestions = suggest_field_mappings(category, info.detected_fields) if category != "unknown" else []
        record.file_mappings[filename] = FileMapping(
            file_name=filename,
            category=category,
            category_confidence="suggested",
            field_mappings=suggestions,
        )

    # Persist before registering in memory: if the DB write fails, the
    # dataset is never added to the in-memory registry either, so the two
    # never disagree about whether this import "happened".
    repository.save_new_dataset(record)
    registry.register(record)
    return record


def confirm_mapping(
    dataset_id: str, file_name: str, category: str, field_mappings: list[FieldMappingEntry]
) -> DatasetRecord:
    record = registry.get(dataset_id)
    if file_name not in record.files:
        raise ImportError_(f"Dataset {dataset_id} has no file named {file_name!r}.")
    confirmed_mappings = [
        FieldMappingEntry(source_field=m.source_field, canonical_field=m.canonical_field, confidence="confirmed")
        for m in field_mappings
    ]
    new_mapping = FileMapping(
        file_name=file_name,
        category=category,
        category_confidence="confirmed",
        field_mappings=confirmed_mappings,
    )
    new_lifecycle_state = "mapped"
    # Persist BEFORE mutating the in-memory record: if this raises, the
    # record is left completely untouched rather than looking "mapped" in
    # memory while the database still has the old mapping.
    repository.save_mapping(dataset_id, file_name, new_mapping, new_lifecycle_state)
    record.file_mappings[file_name] = new_mapping
    record.lifecycle_state = new_lifecycle_state
    return record


def validate_dataset(dataset_id: str) -> DatasetRecord:
    """Stage E. Re-parses each file from its preserved raw bytes (never
    discarded) using its current mapping (confirmed if the user did so,
    the automatic suggestion otherwise) and normalizes into canonical
    records. Fully re-runnable: validating again replaces the previous
    report rather than accumulating duplicates.
    """
    record = registry.get(dataset_id)
    if not record.file_mappings:
        raise ImportError_(f"Dataset {dataset_id} has no files to validate.")

    by_category: dict[str, list] = {}
    reports = []

    for file_name, mapping in record.file_mappings.items():
        content = record.raw_file_bytes[file_name]
        adapter = ADAPTERS[detect_file_type(file_name)]
        records_iter = adapter.parse(file_name, content)
        canonical_list, report = validate_and_normalize_file(
            records_iter,
            mapping.category,
            mapping.field_mappings,
            dataset_id,
            file_name,
            record.files[file_name].detected_fields,
        )
        reports.append(report)
        by_category.setdefault(mapping.category, []).extend(canonical_list)

    new_validation_report = merge_reports(reports)
    new_lifecycle_state = "validated"
    # Persist BEFORE mutating the in-memory record (same discipline as
    # confirm_mapping above) — a failed write here must never leave the
    # dataset looking validated in memory when the DB still has stale or
    # no canonical records for it.
    repository.save_validation(dataset_id, by_category, new_validation_report, new_lifecycle_state)

    record.canonical_alerts = by_category.get("alerts", [])
    record.canonical_dependencies = by_category.get("dependencies", [])
    record.canonical_logs = by_category.get("logs", [])
    record.canonical_metrics = by_category.get("metrics", [])
    record.canonical_historical_incidents = by_category.get("historical_incidents", [])
    record.ground_truth_labels = by_category.get("ground_truth", [])
    record.validation_report = new_validation_report
    record.lifecycle_state = new_lifecycle_state
    return record


def load_dataset(dataset_id: str) -> DatasetRecord:
    """Stage F (load, not "process"). Prepares a fresh, isolated pipeline
    Store/Orchestrator for this dataset from its validated canonical
    records. Does NOT run Triage/Investigation/Verification on anything —
    that's the separate, explicit `/replay` operation.
    """
    record = registry.get(dataset_id)
    # "processing"/"processed" are also valid preconditions: reprocessing an
    # already-processed dataset must rebuild a fresh runtime from the SAME
    # persisted canonical records, not be blocked just because it was
    # loaded/processed before. Only "imported"/"mapped" (not yet validated)
    # and "failed" are rejected.
    if record.lifecycle_state not in ("validated", "loaded", "processing", "processed"):
        raise ImportError_(f"Dataset {dataset_id} must be validated before it can be loaded (current state: {record.lifecycle_state}).")

    new_lifecycle_state = "loaded"
    # Only the lifecycle marker is persisted here — the Store/Orchestrator
    # runtime pipeline state itself is intentionally not (see
    # dataset_registry.DatasetRegistry.hydrate_from_db). After a restart,
    # a dataset restored as "loaded"/"processed" can be re-loaded from its
    # persisted canonical records via this same function, called explicitly
    # again. Persist BEFORE mutating memory, same discipline as above.
    repository.save_lifecycle_state(dataset_id, new_lifecycle_state)

    store = Store(seed_demo_fixtures=False)
    store.dependencies = [
        ServiceDependency(service=d.source_service, depends_on=d.target_service) for d in record.canonical_dependencies
    ]
    record.store = store
    record.orchestrator = Orchestrator(store)
    record.lifecycle_state = new_lifecycle_state
    return record


def canonical_alert_to_raw(alert) -> dict:
    """The orchestrator's public ingestion contract is a raw dict (same
    shape REST/WebSocket callers already use) — this is the one place a
    CanonicalAlert gets translated into it, so the agents never depend on
    the canonical schema directly.

    `fingerprint` is included only when the source dataset actually mapped
    one (see ingestion/field_mapping.py) — normalize_alert() falls back to
    its own computed fingerprint when it's absent, so demo/simulated alerts
    are unaffected. When present, it lets the dataset's own stable alert
    identity drive duplicate detection instead of a service+message hash
    that two real re-occurrences of the same alert can easily miss (e.g.
    recurring-alert message text with a varying suffix).
    """
    raw: dict = {
        "id": alert.event_id,
        "timestamp": alert.timestamp.isoformat(),
        "service": alert.service_name,
        "signal": alert.message,
        "severity": alert.severity.value if hasattr(alert.severity, "value") else str(alert.severity),
    }
    if alert.fingerprint:
        raw["fingerprint"] = alert.fingerprint
    return raw


def _prepare_new_run(dataset_id: str) -> tuple[str, datetime, list]:
    """Validates preconditions, persists a new run row (status='running')
    BEFORE any processing happens, and returns (run_id, started_at,
    ordered_alerts). Shared by the synchronous run_replay() and the async
    start_processing_run() — the concurrency guard and run-identity
    creation only need to exist in one place.

    Deterministic order: timestamp, then event_id as a stable tie-breaker
    (event_id is a hash of dataset_id/file_name/row_index/raw-row — see
    ingestion/normalization._stable_event_id — so it is unique per row
    even when two source rows share the same mapped identifier, and the
    ordering is reproducible across runs on the same validated data).
    """
    record = registry.get(dataset_id)
    if record.lifecycle_state not in ("validated", "loaded", "processing", "processed"):
        raise ImportError_(
            f"Dataset {dataset_id} must be validated before it can be processed (current state: {record.lifecycle_state})."
        )
    if not record.canonical_alerts:
        raise ImportError_(f"Dataset {dataset_id} has no usable canonical alert records to process.")

    # Concurrency guard (section 4C): two active runs for the same dataset
    # would race over the same dataset_id's in-memory Store/Orchestrator
    # reference (load_dataset() replaces it). Checked again here even
    # though routers/datasets.py's async endpoint also checks first, as a
    # backstop against the narrow race between that check and this one.
    existing_run_id = repository.has_active_run(dataset_id)
    if existing_run_id is not None:
        raise ImportError_(
            f"Dataset {dataset_id} already has an active processing run ({existing_run_id}); "
            "wait for it to finish before starting another."
        )

    ordered_alerts = sorted(record.canonical_alerts, key=lambda a: (a.timestamp, a.event_id))
    run_id = registry.new_dataset_id()
    started_at = datetime.now(timezone.utc)
    repository.start_run(run_id, dataset_id, started_at, eligible_alert_count=len(ordered_alerts))
    return run_id, started_at, ordered_alerts


def _execute_processing_run(dataset_id: str, run_id: str, started_at: datetime, ordered_alerts: list) -> ReplayRun:
    """The actual processing engine — identical whether called
    synchronously (run_replay) or from a background thread
    (processing_jobs.submit_run), so there is exactly one place this logic
    exists (section 4A). Persists each alert's result AS SOON as it's
    processed (not batched until the run finishes) so a polling client
    sees `processed_count` genuinely advance during a live run — never a
    manufactured/estimated progress number.
    """
    record = registry.get(dataset_id)
    prior_lifecycle_state = record.lifecycle_state
    repository.save_lifecycle_state(dataset_id, "processing")
    record.lifecycle_state = "processing"

    seq_by_event_id = {a.event_id: i for i, a in enumerate(ordered_alerts)}
    persisted_ids: set[str] = set()

    try:
        # Always reconstruct a FRESH, isolated runtime from the dataset's
        # current persisted canonical records — this is what makes
        # processing restart-safe and makes each run fully independent.
        record = load_dataset(dataset_id)
        assert record.store is not None and record.orchestrator is not None

        for alert in ordered_alerts:
            result = record.orchestrator.process_alert(canonical_alert_to_raw(alert), is_synthetic=False)
            to_persist = [(seq_by_event_id[result.alert_id], result.alert_id, result)]
            if result.incident_id is not None:
                # Other members of this group may have just been
                # retroactively corrected (see Orchestrator._run_pipeline /
                # Store.correct_processing_result) — persist their current
                # state too, incrementally, not just at the very end.
                for member in record.store.candidate_groups.get(result.incident_id, []):
                    if member.id != result.alert_id and member.id in seq_by_event_id:
                        corrected = record.store.processing_results.get(member.id)
                        if corrected is not None:
                            to_persist.append((seq_by_event_id[member.id], member.id, corrected))
            # A result is never counted as processed before this persists —
            # if save_run_results raises, the exception propagates to the
            # except block below and the run is marked failed, not partially
            # "succeeded" with an uncounted result.
            repository.save_run_results(run_id, dataset_id, to_persist)
            for _, aid, _ in to_persist:
                persisted_ids.add(aid)
            repository.update_run_progress(run_id, len(persisted_ids), datetime.now(timezone.utc))
    except Exception as exc:  # noqa: BLE001 — a processing failure must become a 'failed' run, not an unhandled 500
        repository.complete_run(
            run_id,
            status="failed",
            completed_at=datetime.now(timezone.utc),
            processed_count=len(persisted_ids),
            workflow_status_counts={},
            duplicate_count=0,
            group_count=0,
            incident_count=0,
            error_summary=f"{type(exc).__name__}: processing did not complete.",
        )
        record.lifecycle_state = prior_lifecycle_state
        repository.save_lifecycle_state(dataset_id, prior_lifecycle_state)
        raise ImportError_(f"Processing run {run_id} for dataset {dataset_id} failed: {type(exc).__name__}") from exc

    # Re-fetch each alert's FINAL ProcessingResult from the store only
    # AFTER the whole loop completes (catches any correction triggered by
    # the very last alert processed, which the incremental persistence
    # above couldn't have seen yet) rather than trusting only the in-loop
    # incremental writes.
    results: list = [
        record.store.processing_results[alert.event_id]
        for alert in ordered_alerts
        if alert.event_id in record.store.processing_results
    ]

    # Only a group with >= 2 distinct members represents a meaningful
    # incident correlation (section 4D). The orchestrator's seed-alert
    # guard should already prevent a single-member group from ever being
    # created, but this filter is a second, independent safeguard against
    # ever persisting or counting a meaningless one-alert "group".
    valid_groups = {gid: members for gid, members in record.store.candidate_groups.items() if len(members) >= 2}
    groups_to_persist = [
        (group_id, [a.id for a in members], record.store.incidents.get(group_id))
        for group_id, members in valid_groups.items()
    ]

    try:
        indexed_results = [(seq_by_event_id[r.alert_id], r.alert_id, r) for r in results]
        repository.save_run_results(run_id, dataset_id, indexed_results)  # final re-sync — idempotent, INSERT OR REPLACE
        repository.save_run_groups(run_id, dataset_id, groups_to_persist)
        # Single shared aggregation function (section 5) — the exact same
        # computation the live summary endpoint uses, so the final
        # persisted counts and a mid-run snapshot can never disagree about
        # how a count is defined.
        stats = repository.compute_run_statistics(run_id, dataset_id)
        repository.complete_run(
            run_id,
            status="completed",
            completed_at=datetime.now(timezone.utc),
            processed_count=stats["processed_count"],
            workflow_status_counts=stats["workflow_status_counts"],
            duplicate_count=stats["duplicate_count"],
            group_count=stats["group_count"],
            incident_count=len(record.store.incidents),
            unique_count=stats["unique_count"],
            uncertain_count=stats["uncertain_count"],
            grouped_alert_count=stats["grouped_alert_count"],
            standalone_count=stats["standalone_count"],
        )
    except Exception as exc:  # noqa: BLE001 — persistence failing after a real run must not look like success
        repository.complete_run(
            run_id,
            status="failed",
            completed_at=datetime.now(timezone.utc),
            processed_count=len(results),
            workflow_status_counts={},
            duplicate_count=0,
            group_count=0,
            incident_count=0,
            error_summary=f"{type(exc).__name__}: results were computed but could not be fully persisted.",
        )
        record.lifecycle_state = prior_lifecycle_state
        repository.save_lifecycle_state(dataset_id, prior_lifecycle_state)
        raise ImportError_(f"Processing run {run_id} for dataset {dataset_id} failed to persist: {type(exc).__name__}") from exc

    record.lifecycle_state = "processed"
    repository.save_lifecycle_state(dataset_id, "processed")

    run = ReplayRun(id=run_id, dataset_id=dataset_id, started_at=started_at, results=results)
    record.runs[run_id] = run
    return run


def run_replay(dataset_id: str) -> ReplayRun:
    """Processes THIS dataset's own persisted, validated canonical alerts —
    never REPLAY_ALERTS (the unrelated global demo fixture used only by
    `/api/pipeline/replay`) — through the real Triage -> Investigation ->
    Verification pipeline. Synchronous, backward-compatible entry point
    (unchanged response contract from Phase 2/3): blocks until the run
    completes and returns its final ReplayRun. Uses the identical
    processing engine as the new async start_processing_run() — see
    _execute_processing_run.
    """
    run_id, started_at, ordered_alerts = _prepare_new_run(dataset_id)
    return _execute_processing_run(dataset_id, run_id, started_at, ordered_alerts)


def start_processing_run(dataset_id: str) -> dict:
    """Phase 4: the new ASYNC entry point. Creates and persists the run
    row SYNCHRONOUSLY (so it's immediately visible to a polling client,
    and the concurrency guard runs before this returns), then hands the
    actual processing off to the background worker and returns at once.
    """
    run_id, started_at, ordered_alerts = _prepare_new_run(dataset_id)
    from . import processing_jobs

    processing_jobs.submit_run(dataset_id, run_id, started_at, ordered_alerts)
    return {
        "run_id": run_id,
        "dataset_id": dataset_id,
        "status": "running",
        "eligible_alert_count": len(ordered_alerts),
        "started_at": started_at.isoformat(),
    }
