"""The Dataset entity — an explicit, isolated unit of imported data. See
dataset_registry.py for the runtime object that holds a dataset's actual
records/pipeline state; this module is the API-facing shape of it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .canonical import FileCategory

DatasetProvenance = Literal["synthetic_demo", "uploaded", "evaluation_reference"]
DatasetLifecycleState = Literal[
    "imported", "mapped", "validated", "loaded", "processing", "processed", "failed"
]


class FieldMappingEntry(BaseModel):
    source_field: str
    canonical_field: str
    confidence: Literal["suggested", "confirmed"] = "suggested"


class FileMapping(BaseModel):
    file_name: str
    category: FileCategory
    category_confidence: Literal["suggested", "confirmed"] = "suggested"
    field_mappings: list[FieldMappingEntry] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    file_name: str
    row_index: int
    reason: str
    field: Optional[str] = None


class ValidationReport(BaseModel):
    total_records_examined: int = 0
    valid_records: int = 0
    invalid_records: int = 0
    missing_required_fields: int = 0
    invalid_timestamps: int = 0
    invalid_severity_values: int = 0
    duplicate_identifiers: int = 0
    unresolved_service_references: int = 0
    unrecognized_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)  # bounded sample, see validation.py
    issues_truncated: bool = False


class DatasetFileInfo(BaseModel):
    file_name: str
    file_type: Literal["csv", "json", "jsonl"]
    size_bytes: int
    record_count: Optional[int] = None
    detected_fields: list[str] = Field(default_factory=list)
    detected_timestamp_field: Optional[str] = None
    detected_identifier_field: Optional[str] = None
    sample_records: list[dict] = Field(default_factory=list)  # bounded, see schema_detector.py


class DatasetSummary(BaseModel):
    id: str
    name: str
    provenance: DatasetProvenance
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    lifecycle_state: DatasetLifecycleState
    files: list[DatasetFileInfo] = Field(default_factory=list)
    record_counts: dict[str, int] = Field(default_factory=dict)  # category -> count
    validation_report: Optional[ValidationReport] = None
    is_active: bool = False
