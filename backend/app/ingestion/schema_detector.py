"""Inspects a parsed file's fields/sample without ever loading an entire
large file into the browser or requiring a full pass just to preview it.
Record counting IS a full pass (bounded by MAX_COUNT_SCAN) since it's
cheap for this product's dataset scale — re-parsing from the in-memory
upload bytes, not re-reading from disk/network.
"""
from __future__ import annotations

from itertools import islice

from ..schemas.dataset import DatasetFileInfo
from .base import SourceAdapter

PREVIEW_SAMPLE_SIZE = 10
MAX_COUNT_SCAN = 100_000  # safety cap; beyond this, record_count is reported as None (unknown)

_TIMESTAMP_HINTS = ("timestamp", "time", "ts", "event_time", "created_at", "occurred_at", "first_seen")
_IDENTIFIER_HINTS = ("id", "alert_id", "event_id", "incident_id", "uid")


def _looks_like_timestamp(value: object) -> bool:
    if not isinstance(value, str) or len(value) < 8:
        return False
    return any(c.isdigit() for c in value) and ("-" in value or "/" in value or "T" in value)


def detect_timestamp_field(field_names: list[str], sample: list[dict]) -> str | None:
    candidates = [f for f in field_names if any(h in f.lower() for h in _TIMESTAMP_HINTS)]
    for field in candidates:
        if any(_looks_like_timestamp(r.get(field)) for r in sample if isinstance(r, dict)):
            return field
    return candidates[0] if candidates else None


def detect_identifier_field(field_names: list[str], sample: list[dict]) -> str | None:
    for hint in _IDENTIFIER_HINTS:
        for field in field_names:
            if field.lower() == hint or field.lower().endswith(f"_{hint}"):
                return field
    return None


def build_file_info(file_name: str, content: bytes, adapter: SourceAdapter) -> DatasetFileInfo:
    sample_iter = adapter.parse(file_name, content)
    sample = list(islice((r for r in sample_iter if isinstance(r, dict) and "__malformed__" not in r), PREVIEW_SAMPLE_SIZE))

    field_names: list[str] = []
    seen = set()
    for record in sample:
        for key in record.keys():
            if key not in seen:
                seen.add(key)
                field_names.append(key)

    count_iter = adapter.parse(file_name, content)
    record_count = 0
    for _ in islice(count_iter, MAX_COUNT_SCAN + 1):
        record_count += 1
    record_count_result = record_count if record_count <= MAX_COUNT_SCAN else None

    return DatasetFileInfo(
        file_name=file_name,
        file_type=adapter.file_type,  # type: ignore[arg-type]
        size_bytes=len(content),
        record_count=record_count_result,
        detected_fields=field_names,
        detected_timestamp_field=detect_timestamp_field(field_names, sample),
        detected_identifier_field=detect_identifier_field(field_names, sample),
        sample_records=sample,
    )
