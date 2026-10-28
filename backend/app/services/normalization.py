"""Alert normalization: schema validation, UTC timestamp normalization,
stable identity and fingerprinting for duplicate detection.

This is deliberately deterministic and has no dependency on any model
provider — it is the safety-critical first stage every alert passes
through before any agent sees it.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from ..models import Alert, AlertSeverity
from ..redaction import redact_customer, redact_host, redact_ip

REQUIRED_FIELDS = ("service", "signal")


class NormalizationError(ValueError):
    """Raised when a raw alert payload fails required-field validation.

    Callers (the orchestrator) must not treat this as license to drop the
    payload — the raw record must still be preserved, just flagged as
    malformed rather than processed.
    """


def _parse_timestamp(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return datetime.now(timezone.utc)
    return datetime.now(timezone.utc)


def parse_timestamp(value: Any) -> datetime:
    """Public alias of the internal timestamp parser, reused by the generic
    ingestion layer (ingestion/normalization.py) so there is one timestamp-
    parsing implementation, not two.
    """
    return _parse_timestamp(value)


def _parse_severity(value: Any) -> AlertSeverity:
    if isinstance(value, AlertSeverity):
        return value
    if isinstance(value, str) and value.lower() in {s.value for s in AlertSeverity}:
        return AlertSeverity(value.lower())
    return AlertSeverity.unknown


def compute_fingerprint(service: str, signal: str) -> str:
    """Identity used for duplicate detection: same service + same signal text."""
    raw = f"{service.strip().lower()}::{signal.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def validate_required_fields(raw: dict) -> list[str]:
    return [f for f in REQUIRED_FIELDS if not raw.get(f)]


def normalize_alert(raw: dict, is_synthetic: bool = False) -> Alert:
    """Convert a raw, untrusted alert payload into the internal Alert model.

    Raises NormalizationError if required fields are missing. Does not
    raise on unknown/missing severity or host/ip/customer — those degrade
    to "unknown"/redacted-empty rather than failing the whole alert.

    `is_synthetic` threads the orchestrator's own `process_alert(...,
    is_synthetic=...)` provenance flag onto the Alert model itself (Phase
    3, section 2A) — previously only ProcessingResult carried it, so an
    Alert embedded in an Incident or returned from a later API call had no
    way to say where it actually came from.
    """
    missing = validate_required_fields(raw)
    if missing:
        raise NormalizationError(f"Missing required field(s): {', '.join(missing)}")

    service = str(raw["service"]).strip()
    signal = str(raw["signal"]).strip()
    severity = _parse_severity(raw.get("severity"))
    timestamp = _parse_timestamp(raw.get("timestamp"))
    alert_id = str(raw.get("id") or uuid.uuid4().hex[:10])

    host_raw = raw.get("host")
    ip_raw = raw.get("ip")
    customer_raw = raw.get("customer")

    # A caller that already knows a stable identity for this alert (e.g. a
    # dataset's own fingerprint/dedup-key column — see
    # ingestion/import_service.canonical_alert_to_raw) should have its
    # fingerprint used as-is for duplicate detection, rather than one
    # recomputed from service+signal text, which two genuine re-occurrences
    # of the same alert can easily miss if their message text varies even
    # slightly (e.g. a "(recurring alert occurrence)" suffix).
    source_fingerprint = raw.get("fingerprint")
    fingerprint = str(source_fingerprint).strip() if source_fingerprint else compute_fingerprint(service, signal)

    return Alert(
        id=alert_id,
        timestamp=timestamp,
        service=service,
        signal=signal,
        severity=severity,
        host_raw=host_raw,
        ip_raw=ip_raw,
        customer_raw=customer_raw,
        host_redacted=redact_host(host_raw),
        ip_redacted=redact_ip(ip_raw),
        customer_redacted=redact_customer(customer_raw),
        incident_id=None,
        is_safety_queue=False,
        safety_reason=None,
        workflow_status="RECEIVED",
        duplicate_of=None,
        fingerprint=fingerprint,
        is_synthetic=is_synthetic,
    )
