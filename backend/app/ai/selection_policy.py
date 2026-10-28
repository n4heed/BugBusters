"""Decides whether a given candidate group's investigation genuinely
benefits from a model call. The deterministic investigation ALWAYS runs
first (see agents/investigation_agent.py) — this policy only decides
whether to additionally spend AI budget on top of that result.

Never returns True for: schema validation, timestamp conversion, counting,
exact-duplicate detection, routine grouping that already cleared the
correlation threshold cleanly, KPI computation, or display formatting —
none of those ever reach this function in the first place.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..schemas.agent_results import InvestigationResult, TriageResult
from ..services.correlation import GROUPING_THRESHOLD
from .config import AIConfig

# A triage score that cleared the grouping threshold but only barely is
# "ambiguous" — confidently-grouped cases (well above threshold) don't need
# a model opinion.
AMBIGUOUS_SCORE_BAND = 0.15


@dataclass(frozen=True)
class AIDecision:
    use_ai: bool
    reason: str


def should_use_ai(
    ai_config: AIConfig,
    triage: TriageResult,
    deterministic_investigation: InvestigationResult,
    operator_requested: bool,
) -> AIDecision:
    if not ai_config.enabled:
        return AIDecision(False, "AI_DISABLED: no model provider is configured for this run.")

    if operator_requested:
        return AIDecision(True, "An engineer explicitly requested AI-assisted analysis for this incident.")

    if deterministic_investigation.status == "insufficient_evidence":
        # Only worth asking the model if there is at least some real
        # evidence to reason about — not for an empty/near-empty group,
        # which no amount of model reasoning can fix.
        if len(deterministic_investigation.observed_facts) >= 1 and triage.raw_score > 0:
            return AIDecision(
                True,
                "Deterministic correlation found some evidence but could not form a confident hypothesis "
                f"(raw_score={triage.raw_score:.2f}); this is a genuinely ambiguous case.",
            )
        return AIDecision(
            False,
            "Too little evidence exists for AI to meaningfully help — this is a data-availability gap, not an "
            "ambiguity a model can resolve.",
        )

    if GROUPING_THRESHOLD <= triage.raw_score < GROUPING_THRESHOLD + AMBIGUOUS_SCORE_BAND:
        return AIDecision(
            True,
            f"Correlation score ({triage.raw_score:.2f}) only narrowly cleared the grouping threshold "
            f"({GROUPING_THRESHOLD:.2f}); the deterministic hypothesis may benefit from a second opinion.",
        )

    return AIDecision(
        False,
        "Deterministic evidence was sufficient and the correlation score was not borderline — "
        "no AI-assisted analysis needed.",
    )
