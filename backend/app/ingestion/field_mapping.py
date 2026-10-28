"""Synonym tables and mapping-suggestion logic. These are SUGGESTIONS —
never applied as confirmed mappings without the caller (the frontend's
mapping step, or a test that explicitly confirms them) marking them
confirmed. See schemas/dataset.py FieldMappingEntry.confidence.
"""
from __future__ import annotations

from ..schemas.dataset import FieldMappingEntry

# category -> canonical_field -> [acceptable source field name variants]
CANONICAL_FIELD_SYNONYMS: dict[str, dict[str, list[str]]] = {
    "alerts": {
        "timestamp": ["timestamp", "event_time", "created_at", "time", "ts", "occurred_at"],
        "service_name": ["service", "service_name", "application", "app", "component", "svc"],
        "severity": ["severity", "level", "priority", "sev"],
        "rule_name": ["rule_name", "alert_name", "check_title", "rule", "alert_rule"],
        "message": ["message", "description", "summary", "signal", "title", "details"],
        "resource_id": ["host", "hostname", "instance", "resource_id", "node"],
        "source_alert_id": ["id", "alert_id", "event_id", "uid"],
        "fingerprint": ["fingerprint", "dedup_key", "dedupe_key", "group_key", "group_fingerprint"],
        "metric_name": ["metric_name", "metric"],
        "metric_value": ["metric_value", "value"],
        "metric_threshold": ["metric_threshold", "threshold"],
    },
    "metrics": {
        "timestamp": ["timestamp", "time", "ts"],
        "service_name": ["service", "service_name", "application", "component"],
        "metric_name": ["metric_name", "metric", "name"],
        "value": ["value", "metric_value"],
        "unit": ["unit", "units"],
        "threshold": ["threshold"],
    },
    "logs": {
        "timestamp": ["timestamp", "time", "ts"],
        "service_name": ["service", "service_name", "application", "component"],
        "severity": ["severity", "level", "priority"],
        "message": ["message", "description", "log", "text"],
        "correlation_id": ["correlation_id", "trace_id", "request_id"],
    },
    "dependencies": {
        "source_service": ["source_service", "service", "from", "caller"],
        "target_service": ["target_service", "depends_on", "to", "callee"],
        "edge_type": ["edge_type", "relationship_type", "type"],
    },
    "historical_incidents": {
        "incident_id": ["incident_id", "id"],
        "first_seen": ["first_seen", "start_time", "opened_at"],
        "last_seen": ["last_seen", "end_time", "closed_at", "resolved_at"],
        "affected_services": ["affected_services", "services"],
        "description": ["description", "summary", "title"],
        "confirmation_status": ["confirmation_status", "status"],
    },
    "ground_truth": {
        "alert_event_id": ["alert_id", "alert_event_id", "event_id"],
        "true_incident_id": ["incident_id", "true_incident_id", "label"],
    },
}


# The SMALL subset of each category's canonical fields that must ALL be
# present for confident classification. Distinct from the full synonym
# table above (used for mapping suggestions): e.g. a genuine alerts file
# essentially never populates every optional field (metric_name,
# rule_name, etc.), but it always has a timestamp, a service, and at
# least one descriptive signal — so core-field coverage, not overall
# field-count ratio, is what should drive confidence.
_CORE_FIELDS: dict[str, list[str]] = {
    "alerts": ["timestamp", "service_name"],  # plus >=1 of severity/message/rule_name, checked separately
    "metrics": ["timestamp", "service_name", "metric_name", "value"],
    "logs": ["timestamp", "service_name", "message"],
    "dependencies": ["source_service", "target_service"],
    "historical_incidents": ["incident_id"],
    "ground_truth": ["alert_event_id", "true_incident_id"],
}
_ALERTS_SECONDARY_SIGNALS = ["severity", "message", "rule_name"]


def _normalize(name: str) -> str:
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def suggest_field_mappings(category: str, field_names: list[str]) -> list[FieldMappingEntry]:
    synonym_table = CANONICAL_FIELD_SYNONYMS.get(category, {})
    normalized_to_original = {_normalize(f): f for f in field_names}

    suggestions: list[FieldMappingEntry] = []
    for canonical_field, variants in synonym_table.items():
        for variant in variants:
            if variant in normalized_to_original:
                suggestions.append(
                    FieldMappingEntry(
                        source_field=normalized_to_original[variant],
                        canonical_field=canonical_field,
                        confidence="suggested",
                    )
                )
                break  # first/best variant match wins per canonical field
    return suggestions


def classify_file(field_names: list[str]) -> tuple[str, bool]:
    """Returns (best_guess_category, is_confident). This is ALWAYS an
    automatic suggestion — `FileMapping.category_confidence` stays
    "suggested" regardless of `is_confident` until a user/API call
    explicitly confirms it (see ingestion/import_service.py and
    routers/datasets.py's /mapping endpoint). `is_confident=False` means
    the caller should require an explicit user selection rather than
    silently proceeding with the guess — per the milestone's "If the
    system cannot confidently classify a file, request an explicit
    selection instead of guessing silently."
    """
    normalized = {_normalize(f) for f in field_names}

    def category_has_field(category: str, canonical_field: str) -> bool:
        variants = CANONICAL_FIELD_SYNONYMS.get(category, {}).get(canonical_field, [])
        return any(v in normalized for v in variants)

    def core_fields_present(category: str) -> bool:
        core = _CORE_FIELDS.get(category, [])
        if not all(category_has_field(category, f) for f in core):
            return False
        if category == "alerts" and not any(category_has_field("alerts", f) for f in _ALERTS_SECONDARY_SIGNALS):
            return False
        return bool(core)

    # Overall match counts (full synonym table) break ties between
    # categories whose core fields are all present.
    scores: dict[str, int] = {}
    for category, synonym_table in CANONICAL_FIELD_SYNONYMS.items():
        scores[category] = sum(1 for variants in synonym_table.values() if any(v in normalized for v in variants))

    if not scores or max(scores.values()) == 0:
        return "unknown", False

    confidently_identifiable = [c for c in CANONICAL_FIELD_SYNONYMS if core_fields_present(c)]
    best_overall = max(scores, key=lambda c: scores[c])

    if confidently_identifiable:
        best_category = max(confidently_identifiable, key=lambda c: scores[c])
        return best_category, True

    return best_overall, False
