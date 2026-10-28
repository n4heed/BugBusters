"""Applies a CONFIRMED field mapping to raw records, producing canonical
records. Never invents a root cause, an incident ID, or a ground-truth
label to make a record pass — a record that's missing a required canonical
field is reported as invalid (see validation.py) instead.

Deterministic event IDs: `event_id` is a hash of
(dataset_id, file_name, row_index, source_alert_id-or-raw-row) — so
re-importing the exact same file into the exact same dataset produces the
exact same event_ids, which is the idempotency strategy for "repeated
imports must not duplicate records accidentally".
"""
from __future__ import annotations

import hashlib
import json

from ..schemas.canonical import (
    CanonicalAlert,
    CanonicalDependency,
    CanonicalHistoricalIncident,
    CanonicalLog,
    CanonicalMetric,
    GroundTruthLabel,
    SourceRef,
    coerce_severity,
)
from ..schemas.dataset import FieldMappingEntry
from ..services.normalization import parse_timestamp


class RecordNormalizationError(ValueError):
    def __init__(self, reason: str, field: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.field = field


def _apply_mapping(raw: dict, mappings: list[FieldMappingEntry]) -> dict:
    mapped: dict = {}
    mapped_source_fields = set()
    for m in mappings:
        if m.source_field in raw:
            mapped[m.canonical_field] = raw[m.source_field]
            mapped_source_fields.add(m.source_field)
    extra = {k: v for k, v in raw.items() if k not in mapped_source_fields}
    return mapped, extra  # type: ignore[return-value]


def _stable_event_id(dataset_id: str, file_name: str, row_index: int, raw: dict) -> str:
    basis = json.dumps({"d": dataset_id, "f": file_name, "r": row_index}, sort_keys=True) + json.dumps(
        raw, sort_keys=True, default=str
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def normalize_alert_record(
    raw: dict, mappings: list[FieldMappingEntry], dataset_id: str, file_name: str, row_index: int
) -> CanonicalAlert:
    mapped, extra = _apply_mapping(raw, mappings)

    if "timestamp" not in mapped:
        raise RecordNormalizationError("Missing required field: timestamp (no confirmed mapping)", "timestamp")
    if "service_name" not in mapped:
        raise RecordNormalizationError("Missing required field: service_name (no confirmed mapping)", "service_name")

    try:
        timestamp = parse_timestamp(mapped.get("timestamp"))
    except Exception as exc:  # noqa: BLE001
        raise RecordNormalizationError(f"Invalid timestamp value: {mapped.get('timestamp')!r}", "timestamp") from exc

    metric_value = mapped.get("metric_value")
    metric_threshold = mapped.get("metric_threshold")

    return CanonicalAlert(
        event_id=_stable_event_id(dataset_id, file_name, row_index, raw),
        source_alert_id=str(mapped["source_alert_id"]) if mapped.get("source_alert_id") is not None else None,
        timestamp=timestamp,
        service_name=str(mapped["service_name"]),
        rule_name=str(mapped["rule_name"]) if mapped.get("rule_name") is not None else None,
        severity=coerce_severity(mapped.get("severity")),
        signal_type=str(mapped["signal_type"]) if mapped.get("signal_type") is not None else None,
        message=str(mapped.get("message") or mapped.get("rule_name") or "No message provided."),
        metric_name=str(mapped["metric_name"]) if mapped.get("metric_name") is not None else None,
        metric_value=float(metric_value) if metric_value not in (None, "") else None,
        metric_threshold=float(metric_threshold) if metric_threshold not in (None, "") else None,
        fingerprint=str(mapped["fingerprint"]) if mapped.get("fingerprint") not in (None, "") else None,
        dataset_id=dataset_id,
        source_ref=SourceRef(dataset_id=dataset_id, file_name=file_name, row_index=row_index),
        extra_fields={k: v for k, v in extra.items() if not k.startswith("__")},
    )


def normalize_dependency_record(
    raw: dict, mappings: list[FieldMappingEntry], dataset_id: str, file_name: str, row_index: int
) -> CanonicalDependency:
    mapped, _ = _apply_mapping(raw, mappings)
    if "source_service" not in mapped or "target_service" not in mapped:
        raise RecordNormalizationError("Missing required field: source_service and/or target_service", "source_service")
    return CanonicalDependency(
        source_service=str(mapped["source_service"]),
        target_service=str(mapped["target_service"]),
        edge_type=str(mapped["edge_type"]) if mapped.get("edge_type") is not None else None,
        dataset_id=dataset_id,
        source_ref=SourceRef(dataset_id=dataset_id, file_name=file_name, row_index=row_index),
    )


def normalize_metric_record(
    raw: dict, mappings: list[FieldMappingEntry], dataset_id: str, file_name: str, row_index: int
) -> CanonicalMetric:
    mapped, _ = _apply_mapping(raw, mappings)
    for required in ("timestamp", "service_name", "metric_name", "value"):
        if required not in mapped:
            raise RecordNormalizationError(f"Missing required field: {required}", required)
    try:
        timestamp = parse_timestamp(mapped.get("timestamp"))
        value = float(mapped["value"])
    except Exception as exc:  # noqa: BLE001
        raise RecordNormalizationError(f"Invalid timestamp or numeric value: {exc}", "value") from exc
    return CanonicalMetric(
        timestamp=timestamp,
        service_name=str(mapped["service_name"]),
        metric_name=str(mapped["metric_name"]),
        value=value,
        unit=str(mapped["unit"]) if mapped.get("unit") is not None else None,
        threshold=float(mapped["threshold"]) if mapped.get("threshold") not in (None, "") else None,
        dataset_id=dataset_id,
        source_ref=SourceRef(dataset_id=dataset_id, file_name=file_name, row_index=row_index),
    )


def normalize_log_record(
    raw: dict, mappings: list[FieldMappingEntry], dataset_id: str, file_name: str, row_index: int
) -> CanonicalLog:
    mapped, _ = _apply_mapping(raw, mappings)
    for required in ("timestamp", "service_name", "message"):
        if required not in mapped:
            raise RecordNormalizationError(f"Missing required field: {required}", required)
    try:
        timestamp = parse_timestamp(mapped.get("timestamp"))
    except Exception as exc:  # noqa: BLE001
        raise RecordNormalizationError(f"Invalid timestamp value: {mapped.get('timestamp')!r}", "timestamp") from exc
    return CanonicalLog(
        timestamp=timestamp,
        service_name=str(mapped["service_name"]),
        severity=coerce_severity(mapped.get("severity")),
        message=str(mapped["message"]),
        correlation_id=str(mapped["correlation_id"]) if mapped.get("correlation_id") is not None else None,
        dataset_id=dataset_id,
        source_ref=SourceRef(dataset_id=dataset_id, file_name=file_name, row_index=row_index),
    )


def normalize_historical_incident_record(
    raw: dict, mappings: list[FieldMappingEntry], dataset_id: str, file_name: str, row_index: int
) -> CanonicalHistoricalIncident:
    mapped, _ = _apply_mapping(raw, mappings)
    if "incident_id" not in mapped:
        raise RecordNormalizationError("Missing required field: incident_id", "incident_id")
    affected = mapped.get("affected_services")
    if isinstance(affected, str):
        affected = [s.strip() for s in affected.split(",") if s.strip()]
    elif not isinstance(affected, list):
        affected = []
    confirmation = mapped.get("confirmation_status")
    confirmation = confirmation if confirmation in ("confirmed", "unconfirmed", "false_positive") else None
    return CanonicalHistoricalIncident(
        incident_id=str(mapped["incident_id"]),
        first_seen=parse_timestamp(mapped["first_seen"]) if mapped.get("first_seen") else None,
        last_seen=parse_timestamp(mapped["last_seen"]) if mapped.get("last_seen") else None,
        affected_services=affected,
        description=str(mapped["description"]) if mapped.get("description") is not None else None,
        confirmation_status=confirmation,  # never defaulted to "confirmed" — only set if the source said so
        dataset_id=dataset_id,
        source_ref=SourceRef(dataset_id=dataset_id, file_name=file_name, row_index=row_index),
    )


def normalize_ground_truth_record(
    raw: dict, mappings: list[FieldMappingEntry], dataset_id: str, file_name: str, row_index: int
) -> GroundTruthLabel:
    mapped, _ = _apply_mapping(raw, mappings)
    if "alert_event_id" not in mapped or "true_incident_id" not in mapped:
        raise RecordNormalizationError("Missing required field: alert_event_id and/or true_incident_id")
    return GroundTruthLabel(
        alert_event_id=str(mapped["alert_event_id"]),
        true_incident_id=str(mapped["true_incident_id"]),
        dataset_id=dataset_id,
        source_ref=SourceRef(dataset_id=dataset_id, file_name=file_name, row_index=row_index),
    )


NORMALIZERS = {
    "alerts": normalize_alert_record,
    "metrics": normalize_metric_record,
    "logs": normalize_log_record,
    "dependencies": normalize_dependency_record,
    "historical_incidents": normalize_historical_incident_record,
    "ground_truth": normalize_ground_truth_record,
}
