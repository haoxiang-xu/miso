from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..kernel.types import KernelRunResult


@dataclass(frozen=True)
class CompletionEvaluation:
    complete: bool
    feedback: str = ""
    reason: str = ""


CompletionValidator = Callable[[KernelRunResult], CompletionEvaluation | bool | dict[str, Any]]


@dataclass(frozen=True)
class CompletionPolicy:
    validator: CompletionValidator
    max_repair_turns: int = 1
    repair_max_iterations: int | None = None
    max_total_tokens: int | None = None
    max_elapsed_seconds: float | None = None
    stop_on_no_progress: bool = True

    def evaluate(self, result: KernelRunResult) -> CompletionEvaluation:
        raw = self.validator(result)
        if isinstance(raw, CompletionEvaluation):
            return raw
        if isinstance(raw, dict):
            complete = bool(raw.get("complete", raw.get("completed", False)))
            return CompletionEvaluation(
                complete=complete,
                feedback=str(raw.get("feedback") or raw.get("repair_prompt") or ""),
                reason=str(raw.get("reason") or ""),
            )
        return CompletionEvaluation(complete=bool(raw))


__all__ = [
    "CompletionEvaluation",
    "CompletionPolicy",
    "CompletionValidator",
]
