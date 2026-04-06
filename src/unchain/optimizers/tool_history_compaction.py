from __future__ import annotations

from dataclasses import dataclass

from ..memory import MemoryConfig
from ..memory.tool_history import _apply_deferred_tool_compaction
from .base import BaseContextOptimizer, OptimizerContext


@dataclass(frozen=True)
class ToolHistoryCompactionOptimizerConfig:
    enabled: bool = True
    keep_completed_turns: int = 1
    max_chars: int = 1200
    preview_chars: int = 160
    include_tools: list[str] | None = None
    hash_payloads: bool = True


class ToolHistoryCompactionOptimizer(BaseContextOptimizer):
    def __init__(
        self,
        config: ToolHistoryCompactionOptimizerConfig | None = None,
        *,
        phases=("before_model",),
        order: int = 10,
    ) -> None:
        super().__init__(name="tool_history_compaction", phases=phases, order=order)
        self.config = config or ToolHistoryCompactionOptimizerConfig()

    def build_optimizer_delta(self, context: OptimizerContext):
        bucket = context.optimizer_state()
        memory_config = MemoryConfig(
            deferred_tool_compaction_enabled=bool(self.config.enabled),
            deferred_tool_compaction_keep_completed_turns=max(0, int(self.config.keep_completed_turns)),
            deferred_tool_compaction_max_chars=max(64, int(self.config.max_chars)),
            deferred_tool_compaction_preview_chars=max(32, int(self.config.preview_chars)),
            deferred_tool_compaction_include_tools=(
                list(self.config.include_tools) if isinstance(self.config.include_tools, list) else None
            ),
            deferred_tool_compaction_hash_payloads=bool(self.config.hash_payloads),
        )
        tool_resolver = context.toolkit.get if context.toolkit is not None else None
        compacted_messages, stats = _apply_deferred_tool_compaction(
            context.latest_messages(),
            provider=context.provider,
            session_id=context.session_id,
            tool_resolver=tool_resolver,
            config=memory_config,
        )
        bucket.update(stats)
        bucket["applied"] = bool(stats.get("deferred_compaction_applied"))
        return self.replace_messages_delta(
            context,
            compacted_messages,
            bucket=bucket,
            trace={
                "deferred_compaction_applied": bucket["applied"],
                "deferred_compaction_record_count": int(bucket.get("deferred_compaction_record_count", 0) or 0),
            },
        )
