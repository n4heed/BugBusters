"""JSON Lines / newline-delimited JSON adapter (.jsonl, .ndjson).

Genuinely streaming: reads line by line rather than loading the whole file
into memory at once, which matters for large inputs.
"""
from __future__ import annotations

import json
from typing import Iterator


class JsonlAdapter:
    file_type = "jsonl"

    def can_handle(self, filename: str) -> bool:
        lower = filename.lower()
        return lower.endswith(".jsonl") or lower.endswith(".ndjson")

    def parse(self, filename: str, content: bytes) -> Iterator[dict]:
        text = content.decode("utf-8-sig", errors="replace")
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                yield {"__malformed__": True, "__raw_value__": stripped[:200], "__line__": line_no}
                continue
            if isinstance(parsed, dict):
                yield parsed
            else:
                yield {"__malformed__": True, "__raw_value__": repr(parsed)[:200], "__line__": line_no}
