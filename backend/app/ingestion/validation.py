"""Validates raw records against a confirmed field mapping and produces a
ValidationReport. Never silently discards an invalid record — every one is
counted, and a bounded sample of specific issues is kept for display.
"""
from __future__ import annotations

from typing import Iterable

from ..schemas.dataset import FieldMappingEntry, ValidationIssue, ValidationReport
from .normalization import NORMALIZERS, RecordNormalizationError

MAX_ISSUES_KEPT = 50


def validate_and_normalize_file(
    records: Iterable[dict],
    category: str,
    field_mappings: list[FieldMappingEntry],
    dataset_id: str,
    file_name: str,
    detected_fields: list[str],
) -> tuple[list, ValidationReport]:
    report = ValidationReport()
    valid_records: list = []

    normalizer = NORMALIZERS.get(category)
    mapped_source_fields = {m.source_field for m in field_mappings}
    report.unrecognized_fields = [f for f in detected_fields if f not in mapped_source_fields]

    if normalizer is None:
        report.warnings.append(
            f"Category {category!r} has no normalizer (ground_truth/unknown categories are tracked separately "
            "or require explicit classification) — no records were normalized from {file_name!r}."
        )
        return valid_records, report

    seen_identifiers: set[str] = set()

    for row_index, raw in enumerate(records):
        report.total_records_examined += 1

        if raw.get("__malformed__"):
            report.invalid_records += 1
            _record_issue(report, file_name, row_index, "Record is not a well-formed object (malformed JSON/row).")
            continue

        try:
            canonical = normalizer(raw, field_mappings, dataset_id, file_name, row_index)
        except RecordNormalizationError as exc:
            report.invalid_records += 1
            if exc.field == "timestamp":
                report.invalid_timestamps += 1
            elif exc.field and "field" in exc.reason.lower() and "missing" in exc.reason.lower():
                report.missing_required_fields += 1
            _record_issue(report, file_name, row_index, exc.reason, exc.field)
            continue
        except Exception as exc:  # noqa: BLE001 — a record-level failure must never crash the whole import
            report.invalid_records += 1
            _record_issue(report, file_name, row_index, f"Unexpected normalization error: {exc}")
            continue

        severity_value = getattr(canonical, "severity", None)
        if severity_value is not None and str(severity_value) == "unknown" and raw.get("severity") not in (None, ""):
            report.invalid_severity_values += 1
            report.warnings.append(
                f"{file_name} row {row_index}: severity value {raw.get('severity')!r} did not match any known "
                "severity synonym; stored as 'unknown' rather than guessed."
            )

        identifier = getattr(canonical, "source_alert_id", None) or getattr(canonical, "incident_id", None)
        if identifier:
            if identifier in seen_identifiers:
                report.duplicate_identifiers += 1
                _record_issue(report, file_name, row_index, f"Duplicate identifier: {identifier!r}")
            seen_identifiers.add(identifier)

        valid_records.append(canonical)
        report.valid_records += 1

    return valid_records, report


def _record_issue(report: ValidationReport, file_name: str, row_index: int, reason: str, field: str | None = None) -> None:
    if len(report.issues) < MAX_ISSUES_KEPT:
        report.issues.append(ValidationIssue(file_name=file_name, row_index=row_index, reason=reason, field=field))
    else:
        report.issues_truncated = True


def merge_reports(reports: list[ValidationReport]) -> ValidationReport:
    merged = ValidationReport()
    for r in reports:
        merged.total_records_examined += r.total_records_examined
        merged.valid_records += r.valid_records
        merged.invalid_records += r.invalid_records
        merged.missing_required_fields += r.missing_required_fields
        merged.invalid_timestamps += r.invalid_timestamps
        merged.invalid_severity_values += r.invalid_severity_values
        merged.duplicate_identifiers += r.duplicate_identifiers
        merged.unresolved_service_references += r.unresolved_service_references
        merged.unrecognized_fields.extend(r.unrecognized_fields)
        merged.warnings.extend(r.warnings)
        remaining = MAX_ISSUES_KEPT - len(merged.issues)
        if remaining > 0:
            merged.issues.extend(r.issues[:remaining])
        if r.issues_truncated or len(r.issues) > remaining:
            merged.issues_truncated = True
    return merged
