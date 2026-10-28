"""Server-side redaction applied before alert records ever leave the backend.

This runs before data reaches the API response (and before any model would
see it), so the frontend's own masking is a presentation safeguard on top of
this, not the source of truth for redaction.
"""
from __future__ import annotations

import re

_HOST_RE = re.compile(r"^[a-zA-Z0-9.-]+$")
_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def redact_host(host: str | None) -> str:
    if not host:
        return "[REDACTED_HOST]"
    return "[REDACTED_HOST]"


def redact_ip(ip: str | None) -> str:
    if not ip:
        return "[REDACTED_IP]"
    return "[REDACTED_IP]"


def redact_customer(customer: str | None) -> str:
    if not customer:
        return "[REDACTED]"
    return "[REDACTED]"


def looks_like_host(value: str) -> bool:
    return bool(_HOST_RE.match(value))


def looks_like_ip(value: str) -> bool:
    return bool(_IP_RE.match(value))
