"""Adapter interface separating input-SPECIFIC parsing from AIOps
processing. Every adapter yields plain dicts (raw source records, field
names untouched) from a `(filename, content_bytes)` pair — nothing past
this point in the pipeline ever looks at a CSV column index or a
source-specific JSON key directly; schema_detector.py and field_mapping.py
take it from here.

Parsing is generator-based so callers can bound it (a 20-record preview
doesn't require reading a 500MB file to the end) — see
import_service.PREVIEW_RECORD_LIMIT / MAX_RECORDS_PER_FILE.
"""
from __future__ import annotations

from typing import Iterator, Protocol

SUPPORTED_EXTENSIONS = (".csv", ".json", ".jsonl", ".ndjson")


class UnsupportedFormatError(ValueError):
    pass


class SourceAdapter(Protocol):
    file_type: str

    def can_handle(self, filename: str) -> bool: ...

    def parse(self, filename: str, content: bytes) -> Iterator[dict]:
        """Yields raw records (dicts) in file order. Must not raise merely
        because an individual record is malformed — validation.py decides
        what's valid; a genuinely unparseable file (e.g. not valid JSON at
        all) may raise, and the caller reports that as a failed import for
        that file rather than crashing the whole request.
        """
        ...


def detect_file_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".csv"):
        return "csv"
    if lower.endswith(".jsonl") or lower.endswith(".ndjson"):
        return "jsonl"
    if lower.endswith(".json"):
        return "json"
    raise UnsupportedFormatError(
        f"Unsupported file type for {filename!r}. Supported: {', '.join(SUPPORTED_EXTENSIONS)} "
        "(or a .zip bundle containing these)."
    )
