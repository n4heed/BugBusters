"""Deterministic (never pickle) persistence for the dataset-import
lifecycle. Every Pydantic/canonical model is serialized via
`.model_dump(mode="json")` + `json.dumps` and restored via
`Model.model_validate(json.loads(...))` — Pydantic reconstructs datetimes,
enums and nested models from that JSON-safe shape on the way back in.

Scope: dataset metadata, uploaded file bytes, detected schema, field
mappings, validation reports, and canonical records (alerts/metrics/logs/
dependencies/historical_incidents/ground_truth). Pipeline runtime state
(Store/Orchestrator) is intentionally NOT persisted here — see
dataset_registry.DatasetRegistry.hydrate_from_db for why.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from ..schemas.canonical import (
    CanonicalAlert,
    CanonicalDependency,
    CanonicalHistoricalIncident,
    CanonicalLog,
    CanonicalMetric,
    GroundTruthLabel,
)
from ..schemas.dataset import DatasetFileInfo, FieldMappingEntry, FileMapping, ValidationReport
from .db import get_connection

_CANONICAL_MODELS = {
    "alerts": CanonicalAlert,
    "metrics": CanonicalMetric,
    "logs": CanonicalLog,
    "dependencies": CanonicalDependency,
    "historical_incidents": CanonicalHistoricalIncident,
    "ground_truth": GroundTruthLabel,
}

_CANONICAL_ATTR = {
    "alerts": "canonical_alerts",
    "metrics": "canonical_metrics",
    "logs": "canonical_logs",
    "dependencies": "canonical_dependencies",
    "historical_incidents": "canonical_historical_incidents",
    "ground_truth": "ground_truth_labels",
}


def _mapping_row(mapping: FileMapping) -> str:
    return json.dumps([m.model_dump(mode="json") for m in mapping.field_mappings])


def _save_mapping(conn: sqlite3.Connection, dataset_id: str, file_name: str, mapping: FileMapping) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO dataset_file_mappings "
        "(dataset_id, file_name, category, category_confidence, field_mappings) VALUES (?, ?, ?, ?, ?)",
        (dataset_id, file_name, mapping.category, mapping.category_confidence, _mapping_row(mapping)),
    )


def save_new_dataset(record) -> None:
    """Persists a freshly-imported dataset's metadata, per-file schema
    info, raw uploaded bytes, and suggested mappings — called at the end
    of import_dataset(), before it reports success. One transaction:
    nothing is left half-written if any part fails.
    """
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dataset_meta (id, name, provenance, created_at, lifecycle_state) "
            "VALUES (?, ?, ?, ?, ?)",
            (record.id, record.name, record.provenance, record.created_at.isoformat(), record.lifecycle_state),
        )
        for file_name, info in record.files.items():
            conn.execute(
                "INSERT OR REPLACE INTO dataset_files "
                "(dataset_id, file_name, file_type, size_bytes, record_count, detected_fields, "
                " detected_timestamp_field, detected_identifier_field, sample_records, raw_bytes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    file_name,
                    info.file_type,
                    info.size_bytes,
                    info.record_count,
                    json.dumps(info.detected_fields),
                    info.detected_timestamp_field,
                    info.detected_identifier_field,
                    json.dumps(info.sample_records),
                    record.raw_file_bytes[file_name],
                ),
            )
        for file_name, mapping in record.file_mappings.items():
            _save_mapping(conn, record.id, file_name, mapping)


def save_mapping(dataset_id: str, file_name: str, mapping: FileMapping, lifecycle_state: str) -> None:
    """Called from confirm_mapping() — persists the updated mapping and the
    dataset's new lifecycle_state together.
    """
    with get_connection() as conn:
        _save_mapping(conn, dataset_id, file_name, mapping)
        conn.execute("UPDATE dataset_meta SET lifecycle_state = ? WHERE id = ?", (lifecycle_state, dataset_id))


def save_validation(
    dataset_id: str,
    canonical_by_category: dict[str, list],
    validation_report,
    lifecycle_state: str,
) -> None:
    """Called from validate_dataset() — atomically REPLACES all of this
    dataset's canonical records (so re-validating never accumulates
    duplicate rows) and persists the validation report + new
    lifecycle_state in the same transaction. On any failure partway
    through, the `with conn:` block in get_connection() rolls back the
    DELETE and any partial INSERTs — the dataset's previously-persisted
    records are left exactly as they were, not half-replaced.

    Takes explicit arguments rather than a DatasetRecord so the caller can
    persist FIRST and only mutate its in-memory record after this succeeds
    (see import_service.validate_dataset) — a failed write here must never
    leave the in-memory dataset looking validated when it isn't.
    """
    with get_connection() as conn:
        conn.execute("DELETE FROM canonical_records WHERE dataset_id = ?", (dataset_id,))
        rows = [
            (dataset_id, category, json.dumps(item.model_dump(mode="json")))
            for category in _CANONICAL_ATTR
            for item in canonical_by_category.get(category, [])
        ]
        if rows:
            conn.executemany(
                "INSERT INTO canonical_records (dataset_id, category, record_json) VALUES (?, ?, ?)", rows
            )
        conn.execute(
            "INSERT OR REPLACE INTO dataset_validation_report (dataset_id, report_json) VALUES (?, ?)",
            (
                dataset_id,
                json.dumps(validation_report.model_dump(mode="json")) if validation_report else "null",
            ),
        )
        conn.execute("UPDATE dataset_meta SET lifecycle_state = ? WHERE id = ?", (lifecycle_state, dataset_id))


def save_lifecycle_state(dataset_id: str, lifecycle_state: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE dataset_meta SET lifecycle_state = ? WHERE id = ?", (lifecycle_state, dataset_id))


def save_active_dataset(dataset_id: str) -> None:
    with get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES ('active_dataset_id', ?)", (dataset_id,))


def load_active_dataset_id() -> str | None:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key = 'active_dataset_id'").fetchone()
        return row[0] if row else None


def load_all_datasets() -> list:
    """Reconstructs every persisted dataset (never the in-memory-only
    "default" synthetic-demo dataset, which is never written here) as
    DatasetRecord objects — used by DatasetRegistry.hydrate_from_db() on
    startup. `.store` / `.orchestrator` are left None: reconstructing the
    pipeline's runtime state automatically is out of scope for this phase
    (see the orchestrator's own docstring) — a restored dataset is ready
    for an explicit /validate and /load call, using its restored raw
    bytes and mappings, exactly as the original milestone spec requires.
    """
    from ..dataset_registry import DatasetRecord  # deferred: avoids a circular import at module load time

    results = []
    with get_connection() as conn:
        for dataset_id, name, provenance, created_at, lifecycle_state in conn.execute(
            "SELECT id, name, provenance, created_at, lifecycle_state FROM dataset_meta"
        ):
            record = DatasetRecord(
                id=dataset_id,
                name=name,
                provenance=provenance,
                created_at=datetime.fromisoformat(created_at),
                lifecycle_state=lifecycle_state,
            )

            for (
                file_name,
                file_type,
                size_bytes,
                record_count,
                detected_fields,
                detected_timestamp_field,
                detected_identifier_field,
                sample_records,
                raw_bytes,
            ) in conn.execute(
                "SELECT file_name, file_type, size_bytes, record_count, detected_fields, "
                "detected_timestamp_field, detected_identifier_field, sample_records, raw_bytes "
                "FROM dataset_files WHERE dataset_id = ?",
                (dataset_id,),
            ):
                record.files[file_name] = DatasetFileInfo(
                    file_name=file_name,
                    file_type=file_type,
                    size_bytes=size_bytes,
                    record_count=record_count,
                    detected_fields=json.loads(detected_fields),
                    detected_timestamp_field=detected_timestamp_field,
                    detected_identifier_field=detected_identifier_field,
                    sample_records=json.loads(sample_records),
                )
                record.raw_file_bytes[file_name] = bytes(raw_bytes)

            for file_name, category, category_confidence, field_mappings in conn.execute(
                "SELECT file_name, category, category_confidence, field_mappings "
                "FROM dataset_file_mappings WHERE dataset_id = ?",
                (dataset_id,),
            ):
                record.file_mappings[file_name] = FileMapping(
                    file_name=file_name,
                    category=category,
                    category_confidence=category_confidence,
                    field_mappings=[FieldMappingEntry.model_validate(m) for m in json.loads(field_mappings)],
                )

            vrow = conn.execute(
                "SELECT report_json FROM dataset_validation_report WHERE dataset_id = ?", (dataset_id,)
            ).fetchone()
            if vrow and vrow[0] and vrow[0] != "null":
                record.validation_report = ValidationReport.model_validate(json.loads(vrow[0]))

            by_category: dict[str, list] = {c: [] for c in _CANONICAL_MODELS}
            for category, record_json in conn.execute(
                "SELECT category, record_json FROM canonical_records WHERE dataset_id = ? ORDER BY id",
                (dataset_id,),
            ):
                model_cls = _CANONICAL_MODELS.get(category)
                if model_cls is not None:
                    by_category[category].append(model_cls.model_validate(json.loads(record_json)))

            for category, attr in _CANONICAL_ATTR.items():
                setattr(record, attr, by_category[category])

            results.append(record)
    return results


# ---------------------------------------------------------------------------
# Phase 2: processing-run persistence
# ---------------------------------------------------------------------------

# Chunk size for writing per-alert results — avoids one enormous transaction
# for large datasets while each chunk still commits atomically on its own.
RESULTS_BATCH_SIZE = 200


def start_run(run_id: str, dataset_id: str, started_at: datetime, eligible_alert_count: int) -> None:
    """Persists the run's existence BEFORE any processing happens, with
    status='running' — so a run interrupted mid-processing is visible as
    exactly that (a 'running' row that never reached complete_run()),
    never as if it had simply never been attempted.
    """
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO processing_runs "
            "(run_id, dataset_id, started_at, status, eligible_alert_count, last_progress_at) "
            "VALUES (?, ?, ?, 'running', ?, ?)",
            (run_id, dataset_id, started_at.isoformat(), eligible_alert_count, started_at.isoformat()),
        )


def has_active_run(dataset_id: str) -> str | None:
    """Returns the run_id of a currently-'running' run for this dataset, or
    None. Used as a concurrency guard (Phase 4, section 4C) — rapidly
    clicking "start processing" must not spawn a second worker racing the
    first one over the same dataset's in-memory Store/Orchestrator.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT run_id FROM processing_runs WHERE dataset_id = ? AND status = 'running' LIMIT 1",
            (dataset_id,),
        ).fetchone()
    return row[0] if row else None


def update_run_progress(run_id: str, processed_count: int, last_progress_at: datetime) -> None:
    """Called after each incremental batch of results is persisted during a
    LIVE run — this is what lets a polling client see processed_count
    advance before the run finishes, and is the source of the "latest
    update" timestamp. Never touches `status` — only complete_run() sets a
    terminal state.
    """
    with get_connection() as conn:
        conn.execute(
            "UPDATE processing_runs SET processed_count = ?, last_progress_at = ? WHERE run_id = ?",
            (processed_count, last_progress_at.isoformat(), run_id),
        )


def mark_stale_running_runs_interrupted() -> list[str]:
    """Called once at startup (DatasetRegistry.hydrate_from_db) — any run
    still 'running' at this point belonged to a PREVIOUS process that is
    now gone (this one has no worker for it), so it must never be shown as
    healthy/active. Returns the run_ids that were marked, for logging.
    Never touches 'completed'/'failed' rows — historical runs are immutable.
    """
    with get_connection() as conn:
        rows = conn.execute("SELECT run_id FROM processing_runs WHERE status = 'running'").fetchall()
        run_ids = [r[0] for r in rows]
        if run_ids:
            conn.executemany(
                "UPDATE processing_runs SET status = 'interrupted', "
                "error_summary = 'Backend restarted while this run was still in progress.' WHERE run_id = ?",
                [(rid,) for rid in run_ids],
            )
    return run_ids


def compute_run_statistics(run_id: str, dataset_id: str) -> dict | None:
    """The SINGLE shared aggregation function — both the live summary API
    and complete_run()'s final persisted counts are derived from this same
    computation (see import_service.py), so they can never disagree.
    Reads directly from `processing_results` / `processing_groups` — works
    correctly whether the run is still in progress, completed, or being
    read after a restart. Returns None if the run doesn't exist for this
    dataset.
    """
    results = load_run_results(run_id, dataset_id)
    if results is None:
        return None

    workflow_status_counts: dict[str, int] = {}
    unique_count = duplicate_count = uncertain_count = 0
    by_alert_id = {}
    for r in results:
        key = r.workflow_status.value if hasattr(r.workflow_status, "value") else str(r.workflow_status)
        workflow_status_counts[key] = workflow_status_counts.get(key, 0) + 1
        if r.disposition == "redundant":
            duplicate_count += 1
        elif r.disposition == "uncertain":
            uncertain_count += 1
        else:
            unique_count += 1
        by_alert_id[r.alert_id] = r

    groups = load_run_groups(run_id)
    group_count = len(groups)
    grouped_alert_ids = {aid for g in groups for aid in g["member_alert_ids"]}
    grouped_alert_count = len(grouped_alert_ids)
    # In this architecture a duplicate is never added to a candidate group
    # (duplicate detection short-circuits before grouping ever runs — see
    # agents/triage_agent.py), so every group member's own disposition is
    # always "unique" and unique_grouped_alert_count == grouped_alert_count
    # by construction. Computed independently anyway (not assumed equal)
    # so this stays correct if that invariant ever changes.
    unique_grouped_alert_count = len(
        {aid for aid in grouped_alert_ids if by_alert_id.get(aid) is not None and by_alert_id[aid].disposition != "redundant"}
    )
    standalone_count = max(0, unique_count - unique_grouped_alert_count)

    return {
        "processed_count": len(results),
        "workflow_status_counts": workflow_status_counts,
        "unique_count": unique_count,
        "duplicate_count": duplicate_count,
        "uncertain_count": uncertain_count,
        "group_count": group_count,
        "grouped_alert_count": grouped_alert_count,
        "unique_grouped_alert_count": unique_grouped_alert_count,
        "standalone_count": standalone_count,
    }


def save_run_results(run_id: str, dataset_id: str, results: list[tuple[int, str, object]]) -> None:
    """`results` is a list of (seq, alert_id, ProcessingResult) tuples, in
    processing order. Written in bounded-size batches, never as one
    transaction for the whole run. UNIQUE(run_id, alert_id) + INSERT OR
    REPLACE means a retried write never duplicates a row.
    """
    for start in range(0, len(results), RESULTS_BATCH_SIZE):
        chunk = results[start : start + RESULTS_BATCH_SIZE]
        rows = [
            (run_id, dataset_id, alert_id, seq, json.dumps(result.model_dump(mode="json")))
            for seq, alert_id, result in chunk
        ]
        with get_connection() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO processing_results "
                "(run_id, dataset_id, alert_id, seq, result_json) VALUES (?, ?, ?, ?, ?)",
                rows,
            )


def save_run_groups(run_id: str, dataset_id: str, groups: list[tuple[str, list[str], object | None]]) -> None:
    """`groups` is a list of (group_id, member_alert_ids, incident_or_None)
    tuples, taken directly from the pipeline's own Store.candidate_groups /
    Store.incidents at the end of a run — never invented or backfilled.
    """
    if not groups:
        return
    rows = [
        (
            run_id,
            dataset_id,
            group_id,
            json.dumps(member_ids),
            len(member_ids),
            json.dumps(incident.model_dump(mode="json")) if incident is not None else None,
        )
        for group_id, member_ids, incident in groups
    ]
    with get_connection() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO processing_groups "
            "(run_id, dataset_id, group_id, member_alert_ids, size, incident_json) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )


def complete_run(
    run_id: str,
    status: str,
    completed_at: datetime,
    processed_count: int,
    workflow_status_counts: dict,
    duplicate_count: int,
    group_count: int,
    incident_count: int,
    error_summary: str | None = None,
    unique_count: int = 0,
    uncertain_count: int = 0,
    grouped_alert_count: int = 0,
    standalone_count: int = 0,
) -> None:
    """Marks a run 'completed' or 'failed' with its final counts — the ONLY
    call that sets a terminal status. A run this is never called for stays
    'running' forever, which is the intended, honest signal for a process
    that was interrupted mid-run (see import_service.run_replay).

    Counting semantics (Phase 3): `duplicate_count` already excludes
    canonical representatives (a representative's own triage decision is
    never "duplicate"). `unique_count` = processed - duplicate - uncertain.
    `grouped_alert_count` = distinct alerts in a valid (>=2 member) group;
    `standalone_count` = unique alerts with no group. `group_count` counts
    distinct incident groups and is NOT assumed equal to `incident_count`
    (an incident is this project's richer, investigated/verified record
    for a group — see models.Incident — so the two can diverge, e.g. if a
    group's incident upsert is still pending).
    """
    with get_connection() as conn:
        conn.execute(
            "UPDATE processing_runs SET completed_at = ?, status = ?, processed_count = ?, "
            "workflow_status_counts = ?, duplicate_count = ?, group_count = ?, incident_count = ?, "
            "error_summary = ?, unique_count = ?, uncertain_count = ?, grouped_alert_count = ?, "
            "standalone_count = ?, last_progress_at = ? WHERE run_id = ?",
            (
                completed_at.isoformat(),
                status,
                processed_count,
                json.dumps(workflow_status_counts),
                duplicate_count,
                group_count,
                incident_count,
                error_summary,
                unique_count,
                uncertain_count,
                grouped_alert_count,
                standalone_count,
                completed_at.isoformat(),
                run_id,
            ),
        )


def _run_row_to_summary(row) -> dict:
    (
        run_id, dataset_id, started_at, completed_at, status, eligible_alert_count, processed_count,
        workflow_status_counts, duplicate_count, group_count, incident_count, error_summary,
        unique_count, uncertain_count, grouped_alert_count, standalone_count, last_progress_at,
    ) = row
    return {
        "run_id": run_id,
        "dataset_id": dataset_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": status,
        "eligible_alert_count": eligible_alert_count,
        "processed_count": processed_count,
        "workflow_status_counts": json.loads(workflow_status_counts),
        "duplicate_count": duplicate_count,
        "group_count": group_count,
        "incident_count": incident_count,
        "error_summary": error_summary,
        "unique_count": unique_count,
        "uncertain_count": uncertain_count,
        "grouped_alert_count": grouped_alert_count,
        "standalone_count": standalone_count,
        "last_progress_at": last_progress_at,
    }


_RUN_SUMMARY_COLUMNS = (
    "run_id, dataset_id, started_at, completed_at, status, eligible_alert_count, "
    "processed_count, workflow_status_counts, duplicate_count, group_count, incident_count, error_summary, "
    "unique_count, uncertain_count, grouped_alert_count, standalone_count, last_progress_at"
)


def load_run_summary(run_id: str, dataset_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT {_RUN_SUMMARY_COLUMNS} FROM processing_runs WHERE run_id = ? AND dataset_id = ?",
            (run_id, dataset_id),
        ).fetchone()
    return _run_row_to_summary(row) if row is not None else None


def list_run_summaries(dataset_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {_RUN_SUMMARY_COLUMNS} FROM processing_runs WHERE dataset_id = ? ORDER BY started_at DESC",
            (dataset_id,),
        ).fetchall()
    return [_run_row_to_summary(row) for row in rows]


def load_run_results(run_id: str, dataset_id: str):
    """Returns the run's ProcessingResult list in original processing
    order, or None if no such run exists for this dataset — distinct from
    an empty list, which would mean a run that genuinely processed zero
    alerts.
    """
    from ..schemas.agent_results import ProcessingResult

    if load_run_summary(run_id, dataset_id) is None:
        return None
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT result_json FROM processing_results WHERE run_id = ? AND dataset_id = ? ORDER BY seq",
            (run_id, dataset_id),
        ).fetchall()
    return [ProcessingResult.model_validate(json.loads(r[0])) for r in rows]


def load_recent_run_results(run_id: str, dataset_id: str, limit: int) -> list:
    """Returns up to `limit` most-recently-processed results (highest seq
    first) — a small, bounded payload for live/polling display, so a
    client refreshing every 1-2s during an active run never has to
    transfer the entire result set just to show "what happened recently".
    The full, unbounded GET .../runs/{run_id} endpoint is unchanged and
    still exists for compatibility.
    """
    from ..schemas.agent_results import ProcessingResult

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT result_json FROM processing_results WHERE run_id = ? AND dataset_id = ? "
            "ORDER BY seq DESC LIMIT ?",
            (run_id, dataset_id, limit),
        ).fetchall()
    return [ProcessingResult.model_validate(json.loads(r[0])) for r in rows]


def load_results_by_alert_ids(run_id: str, dataset_id: str, alert_ids: list[str]) -> dict[str, object]:
    """Returns {alert_id: ProcessingResult} for exactly the given alert IDs
    within one run — used by the groups endpoint (routers/datasets.py) to
    join persisted group membership against each member's own persisted
    disposition/duplicate_of at read time, rather than denormalizing that
    information into `processing_groups` itself (see Phase 3's "single
    source of truth" design: a member's classification can never disagree
    with its own ProcessingResult, because it's read from the same row).
    """
    from ..schemas.agent_results import ProcessingResult

    if not alert_ids:
        return {}
    placeholders = ",".join("?" for _ in alert_ids)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT alert_id, result_json FROM processing_results "
            f"WHERE run_id = ? AND dataset_id = ? AND alert_id IN ({placeholders})",
            (run_id, dataset_id, *alert_ids),
        ).fetchall()
    return {alert_id: ProcessingResult.model_validate(json.loads(rj)) for alert_id, rj in rows}


def load_run_groups(run_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT group_id, member_alert_ids, size, incident_json FROM processing_groups WHERE run_id = ?",
            (run_id,),
        ).fetchall()
    return [
        {
            "group_id": group_id,
            "member_alert_ids": json.loads(member_alert_ids),
            "size": size,
            "incident": json.loads(incident_json) if incident_json else None,
        }
        for group_id, member_alert_ids, size, incident_json in rows
    ]
