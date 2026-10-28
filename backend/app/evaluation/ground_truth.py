"""Ground truth for the synthetic replay scenario. Scoring-only — never
retrieved by any agent or sent to a model (see `ai/evidence.py`'s separate,
already-tested time-cutoff protection for the live pipeline; this module
is for evaluation code, which never touches evidence packages at all).
"""
from __future__ import annotations

from ..fixtures_pipeline import GROUND_TRUTH_POSITIVE_PAIRS, REPLAY_ALERTS

REPLAY_ALERT_IDS: list[str] = [a["id"] for a in REPLAY_ALERTS]

# The one true incident in the replay scenario (see fixtures_pipeline.py's
# docstring: R-A1/R-A2/R-A3 are the deliberately-related group).
TRUE_INCIDENTS: list[set[str]] = [{"R-A1", "R-A2", "R-A3"}]

TRUE_PAIRS = GROUND_TRUTH_POSITIVE_PAIRS

# Alerts with no true incident membership at all (duplicates excluded by
# design — a duplicate is not a second alert that was wrongly left out of
# the group, it's the same occurrence).
NON_INCIDENT_ALERT_IDS = {"R-DUP1", "R-NOVEL1", "R-AMBIG1"}
