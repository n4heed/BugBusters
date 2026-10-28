"""Dataset discovery, separated from model processing (reading metadata/
record counts is not the same as running records through an LLM — nothing
in this module ever calls a model).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


class DatasetUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class DatasetInfo:
    name: str
    provenance: str  # "synthetic_fixture" | "challenge_dataset"
    record_count: int
    schema_fields: list[str]
    fingerprint: str


def dataset_fingerprint(records: list[dict]) -> str:
    """A stable identifier for the exact evaluated data version, without
    exposing its contents — a sha256 of the canonical (sorted-key) JSON
    serialization of the record IDs + a count, not the full record bodies.
    """
    ids = sorted(str(r.get("id", "")) for r in records)
    canonical = json.dumps({"count": len(records), "ids": ids}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def load_synthetic_fixture_dataset() -> DatasetInfo:
    """Mode A — the existing synthetic fixtures (fixtures.py + fixtures_pipeline.py)."""
    from .. import fixtures
    from ..fixtures_pipeline import REPLAY_ALERTS

    total = fixtures.total_raw_alert_count()
    return DatasetInfo(
        name="bug_busters_synthetic_fixtures",
        provenance="synthetic_fixture",
        record_count=total,
        schema_fields=["id", "timestamp", "service", "signal", "severity", "host", "ip", "customer"],
        fingerprint=dataset_fingerprint(REPLAY_ALERTS),
    )


def load_challenge_dataset(path: str | Path | None = None) -> DatasetInfo:
    """Mode B — the real challenge dataset with ground-truth incident links.

    No such dataset exists anywhere in this repository or was ever
    supplied for this project (checked: no data/ directory, no dataset
    file referenced anywhere in fixtures.py, no AO-01 challenge file).
    This function documents the expected interface and the exact missing
    inputs rather than fabricating a result — it always raises here.

    Expected schema, if/when a real dataset is supplied: a file path (CSV
    or JSONL) with at minimum columns/fields `id, timestamp, service,
    signal, severity` per alert record, plus a separate ground-truth file
    mapping alert IDs to incident IDs for scoring (never loaded into any
    model prompt — see ai/evidence.py's separate protection for the live
    pipeline).
    """
    if path is None:
        raise DatasetUnavailableError(
            "No challenge dataset path was supplied, and none exists in this repository. "
            "Required inputs to run Mode B: (1) an alert-records file "
            "(CSV/JSONL with id/timestamp/service/signal/severity fields), "
            "(2) a ground-truth file mapping alert IDs to incident IDs. "
            "Neither has been provided for this project."
        )
    resolved = Path(path)
    if not resolved.exists():
        raise DatasetUnavailableError(f"Challenge dataset path does not exist: {resolved}")
    raise DatasetUnavailableError(
        "Challenge dataset loading is not implemented beyond path/existence checks — "
        "no real dataset has been supplied to implement against yet."
    )
