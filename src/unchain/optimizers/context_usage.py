from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from .base import BaseContextOptimizer, OptimizerContext
from .common import estimate_tokens, split_system_and_non_system


@dataclass(frozen=True)
class ContextUsageOptimizerConfig:
    enabled: bool = True


class ContextUsageOptimizer(BaseContextOptimizer):
    """Inject a [Context Status] system note with token usage stats.

    Runs at order 55 (after all other optimizers) so the usage stats reflect
    the post-optimization message state. Helps the agent make informed
    decisions about delegation vs direct tool use.
    """

    def __init__(
        self,
        config: ContextUsageOptimizerConfig | None = None,
        *,
        phases=("before_model",),
        order: int = 55,
    ) -> None:
        super().__init__(name="context_usage", phases=phases, order=order)
        self.config = config or ContextUsageOptimizerConfig()

    def build_optimizer_delta(self, context: OptimizerContext):
        if not self.config.enabled:
            return None

        max_tokens = context.max_context_window_tokens
        if max_tokens <= 0:
            return None

        messages = context.latest_messages()
        current_tokens = estimate_tokens(messages)
        usage_pct = current_tokens / max_tokens if max_tokens > 0 else 0.0
        remaining_tokens = max(0, max_tokens - current_tokens)

        # Build the status note
        pct_str = f"{usage_pct:.0%}"
        note = (
            f"[Context Status] {pct_str} used "
            f"({current_tokens:,}/{max_tokens:,} tokens). "
            f"~{remaining_tokens:,} remaining."
        )

        # Inject as a system message right after existing system messages
        systems, non_system = split_system_and_non_system(messages)
        status_message = {"role": "system", "content": note}

        # Remove any previous context status note (from prior iterations)
        cleaned_systems = [
            msg for msg in systems
            if not (
                isinstance(msg.get("content"), str)
                and msg["content"].startswith("[Context Status]")
            )
        ]

        updated_messages = cleaned_systems + [status_message] + non_system

        bucket = context.optimizer_state()
        bucket["usage_pct"] = round(usage_pct, 4)
        bucket["current_tokens"] = current_tokens
        bucket["max_tokens"] = max_tokens
        bucket["remaining_tokens"] = remaining_tokens

        return self.replace_messages_delta(
            context,
            updated_messages,
            bucket=bucket,
            trace={
                "usage_pct": round(usage_pct, 4),
                "current_tokens": current_tokens,
                "remaining_tokens": remaining_tokens,
            },
        )
