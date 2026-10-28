"""Lightweight, in-memory model-usage tracker.

Records only operational metadata — never prompts, never raw evidence,
never credentials. Bounded to the most recent N records so it can't grow
without limit across a long-running process.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

MAX_RECORDS = 200


class ModelUsageRecord(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provider: str
    model: str
    purpose: Literal["investigation", "verification_repair"]
    investigation_id: Optional[str] = None
    alert_count: int
    historical_case_count: int
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    tokens_are_estimated: bool = False
    latency_ms: float
    retry_count: int = 0
    status: Literal["success", "error", "timeout", "invalid_response"]
    error_summary: Optional[str] = None  # never the raw exception with secrets


class UsageTracker:
    def __init__(self) -> None:
        self.records: list[ModelUsageRecord] = []

    def record(self, entry: ModelUsageRecord) -> None:
        self.records.append(entry)
        if len(self.records) > MAX_RECORDS:
            self.records = self.records[-MAX_RECORDS:]

    def summary(self) -> dict:
        total = len(self.records)
        successes = sum(1 for r in self.records if r.status == "success")
        errors = total - successes
        total_input = sum(r.input_tokens or 0 for r in self.records)
        total_output = sum(r.output_tokens or 0 for r in self.records)
        any_estimated = any(r.tokens_are_estimated for r in self.records)
        avg_latency = round(sum(r.latency_ms for r in self.records) / total, 1) if total else None
        return {
            "total_calls": total,
            "successes": successes,
            "errors": errors,
            "total_input_tokens": total_input or None,
            "total_output_tokens": total_output or None,
            "tokens_are_estimated": any_estimated,
            "avg_latency_ms": avg_latency,
            # Deliberately no cost figure: see ai/provider.py — we only
            # compute one when the configured provider+model's pricing is
            # actually known, and none is wired in for this milestone.
            "estimated_cost_usd": None,
        }

    def reset(self) -> None:
        self.records = []
