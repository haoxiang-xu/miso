from __future__ import annotations

import copy
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from ..kernel.types import KernelRunResult


@dataclass(frozen=True)
class CompletionEvaluation:
    complete: bool
    feedback: str = ""
    reason: str = ""


CompletionValidator = Callable[[KernelRunResult], CompletionEvaluation | bool | dict[str, Any]]
CompletionRunOnce = Callable[..., KernelRunResult]


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


@dataclass
class CompletionPolicyRunner:
    policy: CompletionPolicy | None
    run_once: CompletionRunOnce
    event_callback: Callable[[dict[str, Any]], None] | None = None
    run_id: str = "agent"

    def _emit_event(self, event_type: str, **payload: Any) -> None:
        if not callable(self.event_callback):
            return
        event = {
            "type": event_type,
            "run_id": str(self.run_id or "agent"),
            **copy.deepcopy(payload),
        }
        self.event_callback(event)

    @staticmethod
    def _final_assistant_text(result: KernelRunResult) -> str:
        for message in reversed(result.messages):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            content = message.get("content")
            if isinstance(content, str):
                return content
        return ""

    def _incomplete_result(self, result: KernelRunResult, *, reason: str) -> KernelRunResult:
        self._emit_event(
            "completion_policy_exhausted",
            reason=reason,
            iteration=int(result.iteration),
        )
        return replace(result, status="completion_incomplete")

    def apply(
        self,
        result: KernelRunResult,
        *,
        payload: dict[str, Any] | None,
        max_iterations: int,
    ) -> KernelRunResult:
        policy = self.policy
        if policy is None or result.status != "completed":
            return result

        started_at = time.monotonic()
        repairs_completed = 0
        current = result
        previous_final_text = ""
        total_tokens = int(current.consumed_tokens or 0)

        while True:
            evaluation = policy.evaluate(current)
            final_text = self._final_assistant_text(current)
            self._emit_event(
                "completion_policy_evaluated",
                complete=bool(evaluation.complete),
                feedback=evaluation.feedback,
                reason=evaluation.reason,
                repair_attempt=repairs_completed,
            )
            if evaluation.complete:
                return current
            if repairs_completed >= max(0, int(policy.max_repair_turns)):
                return self._incomplete_result(
                    current,
                    reason="repair_budget_exhausted",
                )
            if policy.max_total_tokens is not None and total_tokens >= int(policy.max_total_tokens):
                return self._incomplete_result(
                    current,
                    reason="token_budget_exhausted",
                )
            if (
                policy.max_elapsed_seconds is not None
                and time.monotonic() - started_at >= float(policy.max_elapsed_seconds)
            ):
                return self._incomplete_result(
                    current,
                    reason="time_budget_exhausted",
                )
            if (
                repairs_completed > 0
                and policy.stop_on_no_progress
                and final_text == previous_final_text
            ):
                return self._incomplete_result(
                    current,
                    reason="no_progress",
                )

            feedback = str(evaluation.feedback or "").strip()
            if not feedback:
                return self._incomplete_result(
                    current,
                    reason="missing_repair_feedback",
                )

            repairs_completed += 1
            previous_final_text = final_text
            self._emit_event(
                "completion_policy_retry",
                feedback=feedback,
                repair_attempt=repairs_completed,
            )
            repair_messages = copy.deepcopy(current.messages)
            repair_messages.append({"role": "user", "content": feedback})
            current = self.run_once(
                messages=repair_messages,
                payload=payload,
                previous_response_id=current.previous_response_id,
                max_iterations=max(1, int(policy.repair_max_iterations or max_iterations)),
            )
            total_tokens += int(current.consumed_tokens or 0)


__all__ = [
    "CompletionEvaluation",
    "CompletionPolicy",
    "CompletionPolicyRunner",
    "CompletionRunOnce",
    "CompletionValidator",
]
