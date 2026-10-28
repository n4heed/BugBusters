"""SQLite connection + schema management.

The database path is resolved FRESH on every connection — never cached at
import time. This is deliberate: an earlier bug in this project (AI
provider config read once at module-import time) meant test isolation and
environment overrides could race against import order. Reading the env var
at connection-open time instead means tests can override DATABASE_PATH at
any point before a DB call happens, with no import-order dependency.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# This file lives at backend/app/persistence/db.py — three parents up is
# backend/, regardless of the process's current working directory, so
# starting uvicorn from a different cwd never creates a second DB file.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DB_PATH = _BACKEND_ROOT / "data" / "bugbusters.sqlite3"

SCHEMA_VERSION = 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_meta (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    provenance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS dataset_files (
    dataset_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    record_count INTEGER,
    detected_fields TEXT NOT NULL,
    detected_timestamp_field TEXT,
    detected_identifier_field TEXT,
    sample_records TEXT NOT NULL,
    raw_bytes BLOB NOT NULL,
    PRIMARY KEY (dataset_id, file_name)
);

CREATE TABLE IF NOT EXISTS dataset_file_mappings (
    dataset_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    category TEXT NOT NULL,
    category_confidence TEXT NOT NULL,
    field_mappings TEXT NOT NULL,
    PRIMARY KEY (dataset_id, file_name)
);

CREATE TABLE IF NOT EXISTS dataset_validation_report (
    dataset_id TEXT PRIMARY KEY,
    report_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS canonical_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT NOT NULL,
    category TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_canonical_records_dataset
    ON canonical_records (dataset_id, category);

-- Schema v2 (Phase 2): processing-run history ------------------------------

CREATE TABLE IF NOT EXISTS processing_runs (
    run_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,                      -- 'running' | 'completed' | 'failed' | 'interrupted' (Phase 4)
    eligible_alert_count INTEGER NOT NULL,     -- == spec's "total_alerts"; name kept for backward compatibility
    processed_count INTEGER NOT NULL DEFAULT 0,
    workflow_status_counts TEXT NOT NULL DEFAULT '{}',   -- JSON {status: count}
    duplicate_count INTEGER NOT NULL DEFAULT 0,          -- redundant alerts, excluding their representatives
    group_count INTEGER NOT NULL DEFAULT 0,
    incident_count INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT,
    -- Schema v3 (Phase 3) additions — see db._migrate_to_v3 for how an
    -- existing database (created before these columns existed) picks them
    -- up via ALTER TABLE rather than losing its run history.
    unique_count INTEGER NOT NULL DEFAULT 0,
    uncertain_count INTEGER NOT NULL DEFAULT 0,
    grouped_alert_count INTEGER NOT NULL DEFAULT 0,
    standalone_count INTEGER NOT NULL DEFAULT 0,
    -- Schema v4 (Phase 4): last time incremental progress was persisted —
    -- the "latest update" timestamp a live-polling client checks, distinct
    -- from started_at/completed_at. See db._migrate_to_v4.
    last_progress_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_processing_runs_dataset ON processing_runs (dataset_id, started_at);
CREATE INDEX IF NOT EXISTS idx_processing_runs_status ON processing_runs (status);

CREATE TABLE IF NOT EXISTS processing_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    alert_id TEXT NOT NULL,
    seq INTEGER NOT NULL,                      -- processing order within the run
    result_json TEXT NOT NULL,
    UNIQUE (run_id, alert_id)
);
CREATE INDEX IF NOT EXISTS idx_processing_results_run ON processing_results (run_id, seq);
CREATE INDEX IF NOT EXISTS idx_processing_results_dataset ON processing_results (dataset_id);
CREATE INDEX IF NOT EXISTS idx_processing_results_alert ON processing_results (alert_id);

CREATE TABLE IF NOT EXISTS processing_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    member_alert_ids TEXT NOT NULL,            -- JSON list[str]
    size INTEGER NOT NULL,
    incident_json TEXT,                        -- serialized Incident, if one formed; else NULL
    UNIQUE (run_id, group_id)
);
CREATE INDEX IF NOT EXISTS idx_processing_groups_run ON processing_groups (run_id);
CREATE INDEX IF NOT EXISTS idx_processing_groups_dataset ON processing_groups (dataset_id);
"""


def get_db_path() -> Path:
    raw = os.environ.get("DATABASE_PATH", "").strip()
    return Path(raw) if raw else _DEFAULT_DB_PATH


def _migrate_to_v3(conn: sqlite3.Connection) -> None:
    """Additive-only: adds Phase 3's new run-level count columns to an
    existing `processing_runs` table that predates them. A brand-new
    database already gets these columns from `_SCHEMA`'s CREATE TABLE
    above, so this is a no-op there — PRAGMA table_info is checked first
    so ALTER TABLE is only issued for genuinely missing columns, never
    dropping or rewriting existing run history.
    """
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(processing_runs)")}
    for col in ("unique_count", "uncertain_count", "grouped_alert_count", "standalone_count"):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE processing_runs ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")


def _migrate_to_v4(conn: sqlite3.Connection) -> None:
    """Additive-only: adds Phase 4's `last_progress_at` column."""
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(processing_runs)")}
    if "last_progress_at" not in existing_cols:
        conn.execute("ALTER TABLE processing_runs ADD COLUMN last_progress_at TEXT")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    # CREATE TABLE/INDEX IF NOT EXISTS: idempotent, and never drops or
    # overwrites an existing database — safe to run on every connection.
    conn.executescript(_SCHEMA)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    current_version = row[0] if row is not None else 0

    if current_version < 3:
        _migrate_to_v3(conn)
    if current_version < 4:
        _migrate_to_v4(conn)

    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif current_version < SCHEMA_VERSION:
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """One short-lived connection per call. `with conn:` commits the whole
    block on success or rolls it back on any exception — this is what
    makes save_validation()'s "delete old rows, insert new ones" replace
    atomic rather than leaving a half-updated dataset on failure.
    """
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        _ensure_schema(conn)
        with conn:
            yield conn
    finally:
        conn.close()
