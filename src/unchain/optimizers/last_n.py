from __future__ import annotations

from dataclasses import dataclass

from .base import BaseContextOptimizer, OptimizerContext
from .common import replace_non_system_span, split_system_and_non_system, split_turns


def _flatten_turns(turns: list[list[dict]]) -> list[dict]:
    flattened: list[dict] = []
    for turn in turns:
        flattened.extend(turn)
    return flattened


@dataclass(frozen=True)
class LastNOptimizerConfig:
    last_n_turns: int = 8


class LastNOptimizer(BaseContextOptimizer):
    def __init__(
        self,
        config: LastNOptimizerConfig | None = None,
        *,
        phases=("before_model",),
        order: int = 30,
    ) -> None:
        super().__init__(name="last_n", phases=phases, order=order)
        self.config = config or LastNOptimizerConfig()

    def build_optimizer_delta(self, context: OptimizerContext):
        messages = context.latest_messages()
        _, non_system = split_system_and_non_system(messages)
        turns = split_turns(non_system)
        last_n_turns = max(0, int(self.config.last_n_turns))
        kept_turns = turns[-last_n_turns:] if last_n_turns > 0 else []
        bucket = context.optimizer_state()
        bucket["last_n_turns"] = last_n_turns
        bucket["kept_turn_count"] = len(kept_turns)
        bucket["total_turn_count"] = len(turns)
        updated_messages = replace_non_system_span(messages, _flatten_turns(kept_turns))
        return self.replace_messages_delta(
            context,
            updated_messages,
            bucket=bucket,
            trace={
                "kept_turn_count": len(kept_turns),
                "total_turn_count": len(turns),
            },
        )
