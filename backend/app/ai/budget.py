"""A shared, server-side model-call budget for a run/session.

Every model invocation — including a verification repair attempt — must be
counted here before it happens. The deterministic pipeline must keep
working when the budget is exhausted; callers check `can_spend()` and fall
back rather than blocking.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class BudgetExhaustedError(RuntimeError):
    pass


@dataclass
class ModelCallBudget:
    max_calls: int
    used: int = 0
    log: list[str] = field(default_factory=list)

    def remaining(self) -> int:
        return max(0, self.max_calls - self.used)

    def can_spend(self, count: int = 1) -> bool:
        return self.used + count <= self.max_calls

    def spend(self, purpose: str, count: int = 1) -> None:
        if not self.can_spend(count):
            raise BudgetExhaustedError(
                f"AI_BUDGET_EXHAUSTED: {self.used}/{self.max_calls} calls already used; "
                f"refused additional request for {purpose!r}."
            )
        self.used += count
        self.log.append(purpose)

    def reset(self) -> None:
        self.used = 0
        self.log = []
