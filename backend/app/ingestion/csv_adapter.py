"""CSV adapter — standard library only (csv module), no new dependency."""
from __future__ import annotations

import csv
import io
from typing import Iterator


class CsvAdapter:
    file_type = "csv"

    def can_handle(self, filename: str) -> bool:
        return filename.lower().endswith(".csv")

    def parse(self, filename: str, content: bytes) -> Iterator[dict]:
        text = content.decode("utf-8-sig", errors="replace")  # utf-8-sig strips a BOM if present
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            yield {k: v for k, v in row.items() if k is not None}
