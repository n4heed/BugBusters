"""Deprecated — superseded by the `app.ai` package (ai/config.py,
ai/provider.py, ai/budget.py, ai/usage_tracker.py, ai/selection_policy.py,
ai/evidence.py) introduced in the selective-AI-integration milestone.

Kept only as a re-export so an old import path doesn't hard-fail; new code
should import from `app.ai.provider` / `app.ai.config` directly.
"""
from __future__ import annotations

from .ai.config import load_ai_config_safe
from .ai.provider import ModelProvider, get_configured_provider as _get_configured_provider


def get_configured_provider() -> ModelProvider | None:
    return _get_configured_provider(load_ai_config_safe())
