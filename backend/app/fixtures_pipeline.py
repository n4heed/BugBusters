"""A deterministic, reproducible replay scenario for the Triage ->
Investigation -> Verification pipeline, with explicit expected outcomes.

This is the main correctness demonstration — not the random WebSocket
simulator, which stays available for visual testing but is not a
substitute for this. Six raw alerts, processed in a fixed order against a
fixed clock, covering:

  - a genuine related group that should merge (R-A1, R-A2, R-A3)
  - a duplicate of an alert already in that group (R-DUP1)
  - a genuinely novel/unrelated alert that must stay visible, unclustered
    (R-NOVEL1)
  - an ambiguous alert — one real but weak correlation signal, not enough
    to clear the grouping threshold — that must also stay visible,
    unclustered, rather than being silently merged or silently dropped
    (R-AMBIG1)

Uses the same dependency topology the fixture incidents already define
(see app/store.py Store.reset, which unions app/fixtures.py's incident
dependency edges): status-page -> api-gateway -> core-app-svc-01 ->
primary-postgres-db, async-worker-01 -> core-app-svc-01, etc.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

REPLAY_T0 = datetime(2026, 10, 28, 15, 0, 0, tzinfo=timezone.utc)


def _t(minutes: int) -> str:
    return (REPLAY_T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


REPLAY_ALERTS: list[dict] = [
    {
        "id": "R-A1",
        "timestamp": _t(0),
        "service": "core-app-svc-01",
        "signal": "CPU > 95%",
        "severity": "critical",
    },
    {
        "id": "R-A2",
        "timestamp": _t(2),
        "service": "primary-postgres-db",
        "signal": "Write latency > 3s",
        "severity": "critical",
    },
    {
        "id": "R-A3",
        "timestamp": _t(4),
        "service": "api-gateway",
        "signal": "Upstream timeout spike",
        "severity": "high",
    },
    {
        # Same service + signal as R-A1, 6 minutes later: should be recognized
        # as a duplicate, not merged as a new group member.
        "id": "R-DUP1",
        "timestamp": _t(6),
        "service": "core-app-svc-01",
        "signal": "CPU > 95%",
        "severity": "critical",
    },
    {
        # No temporal, service, or dependency relationship to anything above:
        # must remain visible as a genuinely novel/unclustered alert.
        "id": "R-NOVEL1",
        "timestamp": _t(50),
        "service": "waf-firewall",
        "signal": "Unknown protocol anomaly detected",
        "severity": "high",
    },
    {
        # Has ONE real signal (a modeled dependency edge to core-app-svc-01)
        # but is 45 minutes outside the correlation window and shares no
        # other signal — score should land below the grouping threshold.
        "id": "R-AMBIG1",
        "timestamp": _t(45),
        "service": "async-worker-01",
        "signal": "Queue depth creeping up",
        "severity": "low",
    },
]

# Ground truth for evaluation: alert ID pairs that SHOULD end up in the same
# candidate group. Every other pair among REPLAY_ALERTS is an implicit
# negative. R-DUP1/R-NOVEL1/R-AMBIG1 participate in no positive pair.
GROUND_TRUTH_POSITIVE_PAIRS: set[frozenset[str]] = {
    frozenset({"R-A1", "R-A2"}),
    frozenset({"R-A1", "R-A3"}),
    frozenset({"R-A2", "R-A3"}),
}

# Expected final per-alert workflow_status, asserted by tests after replay.
EXPECTED_ALERT_WORKFLOW_STATUS: dict[str, str] = {
    "R-A1": "VERIFIED",
    "R-A2": "VERIFIED",
    "R-A3": "VERIFIED",
    "R-DUP1": "DUPLICATE",
    "R-NOVEL1": "UNCLUSTERED",
    "R-AMBIG1": "UNCLUSTERED",
}
