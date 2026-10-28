"""JSON adapter. Does NOT assume the top level is always an array — a JSON
file may be a single object, a bare array, or a structured wrapper like
{"alerts": [...]}, {"data": [...]}, {"records": [...]}, {"items": [...]}.
"""
from __future__ import annotations

import json
from typing import Iterator

_WRAPPER_KEYS = ("alerts", "records", "data", "items", "events", "results")


class JsonAdapter:
    file_type = "json"

    def can_handle(self, filename: str) -> bool:
        return filename.lower().endswith(".json")

    def parse(self, filename: str, content: bytes) -> Iterator[dict]:
        text = content.decode("utf-8-sig", errors="replace")
        parsed = json.loads(text)  # a genuinely invalid JSON file raises here, by design — see base.py

        if isinstance(parsed, list):
            records = parsed
        elif isinstance(parsed, dict):
            wrapper_key = next((k for k in _WRAPPER_KEYS if isinstance(parsed.get(k), list)), None)
            if wrapper_key is not None:
                records = parsed[wrapper_key]
            else:
                # A single JSON object with no list wrapper: treat it as one record.
                records = [parsed]
        else:
            raise ValueError(
                f"{filename}: top-level JSON value must be an object or an array, got {type(parsed).__name__}."
            )

        for item in records:
            if isinstance(item, dict):
                yield item
            else:
                # Never silently drop a record: a non-dict array entry still
                # counts as one examined record, flagged malformed so
                # validation.py reports it rather than it vanishing from
                # the count entirely.
                yield {"__malformed__": True, "__raw_value__": repr(item)[:200]}
