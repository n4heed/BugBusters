"""Gathers the actual evidence available for a candidate group — never
invents metrics, logs, traces or historical matches. Every string returned
here is derived directly from alert records, the supplied dependency
topology, or previously human-approved SEA memory patterns.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Alert, ServiceDependency


@dataclass
class GatheredEvidence:
    observed_facts: list[str] = field(default_factory=list)
    overlapping_time_windows: list[str] = field(default_factory=list)
    supporting_dependencies: list[str] = field(default_factory=list)
    supporting_metrics: list[str] = field(default_factory=list)
    missing_or_contradictory: list[str] = field(default_factory=list)
    historical_similarities: list[str] = field(default_factory=list)
    candidate_origin: str | None = None


def _pick_origin(group_alerts: list[Alert], adjacency: dict[str, set[str]]) -> str | None:
    """A service is a plausible origin if other group members depend on it
    (directly or transitively via the modeled edges), preferring the one
    with the most incoming group-internal edges. Falls back to the
    earliest-seen service's name when no dependency edges apply — this is
    still disclosed as a hypothesis, never a confirmed cause.
    """
    services = {a.service for a in group_alerts}
    if not services:
        return None

    incoming_count: dict[str, int] = {s: 0 for s in services}
    for service in services:
        for neighbor in adjacency.get(service, set()):
            if neighbor in incoming_count:
                incoming_count[neighbor] += 1

    best = max(incoming_count.items(), key=lambda kv: kv[1], default=(None, 0))
    if best[1] > 0:
        return best[0]

    earliest = min(group_alerts, key=lambda a: a.timestamp)
    return earliest.service


def gather_evidence(
    group_alerts: list[Alert],
    dependencies: list[ServiceDependency],
    historical_pattern_titles: list[str],
) -> GatheredEvidence:
    evidence = GatheredEvidence()
    if not group_alerts:
        evidence.missing_or_contradictory.append("No alerts supplied for this candidate group.")
        return evidence

    services = sorted({a.service for a in group_alerts})
    timestamps = [a.timestamp for a in group_alerts]
    start, end = min(timestamps), max(timestamps)

    evidence.observed_facts.append(
        f"{len(group_alerts)} alert(s) observed across {len(services)} service(s): {', '.join(services)}."
    )
    evidence.overlapping_time_windows.append(
        f"{start.isoformat().replace('+00:00', 'Z')} to {end.isoformat().replace('+00:00', 'Z')}"
    )

    adjacency: dict[str, set[str]] = {}
    for dep in dependencies:
        adjacency.setdefault(dep.service, set()).add(dep.depends_on)
        adjacency.setdefault(dep.depends_on, set()).add(dep.service)

    relevant_edges = [
        dep for dep in dependencies if dep.service in services and dep.depends_on in services
    ]
    if relevant_edges:
        evidence.supporting_dependencies.extend(f"{d.service} -> {d.depends_on}" for d in relevant_edges)
    else:
        evidence.missing_or_contradictory.append(
            "No modeled dependency edge connects the services in this group; grouping relies on temporal/fingerprint signals only."
        )

    evidence.supporting_metrics.extend(f"{a.service}: {a.signal}" for a in group_alerts)

    evidence.candidate_origin = _pick_origin(group_alerts, adjacency)

    if historical_pattern_titles:
        origin = evidence.candidate_origin or ""
        matches = [t for t in historical_pattern_titles if origin and origin in t]
        evidence.historical_similarities.extend(matches)

    return evidence
