from __future__ import annotations

import copy
from typing import Any

from ..optimizers import (
    LastNOptimizer,
    LastNOptimizerConfig,
    LlmSummaryOptimizer,
    LlmSummaryOptimizerConfig,
    SlidingWindowOptimizer,
    SlidingWindowOptimizerConfig,
    ToolHistoryCompactionOptimizer,
    ToolHistoryCompactionOptimizerConfig,
)
from .bootstrap import MemoryBootstrapHarness
from .commit import MemoryCommitHarness
from .events import (
    MemoryCommitEventHarness,
    MemoryCommitInfoResetHarness,
    MemoryPrepareEventHarness,
    MemoryPrepareInfoResetHarness,
)
from .recall_long_term import LongTermRecallMemoryHarness
from .runtime import KernelMemoryRuntime
from .short_term import ShortTermRecallMemoryHarness


def build_default_memory_components(memory_runtime: KernelMemoryRuntime) -> list[Any]:
    config = memory_runtime.config
    return [
        ToolHistoryCompactionOptimizer(
            ToolHistoryCompactionOptimizerConfig(
                enabled=bool(config.deferred_tool_compaction_enabled),
                keep_completed_turns=int(config.deferred_tool_compaction_keep_completed_turns),
                max_chars=int(config.deferred_tool_compaction_max_chars),
                preview_chars=int(config.deferred_tool_compaction_preview_chars),
                include_tools=copy.deepcopy(config.deferred_tool_compaction_include_tools),
                hash_payloads=bool(config.deferred_tool_compaction_hash_payloads),
            )
        ),
        LlmSummaryOptimizer(
            LlmSummaryOptimizerConfig(
                summary_trigger_pct=float(config.summary_trigger_pct),
                summary_target_pct=float(config.summary_target_pct),
                max_summary_chars=int(config.max_summary_chars),
                summary_generator=memory_runtime.summary_generator,
            )
        ),
        LastNOptimizer(
            LastNOptimizerConfig(last_n_turns=int(config.last_n_turns))
        ),
        SlidingWindowOptimizer(
            SlidingWindowOptimizerConfig(
                max_window_pct=float(config.sliding_window_pct),
                max_window_tokens=config.sliding_window_max_tokens,
            )
        ),
        MemoryPrepareInfoResetHarness(runtime=memory_runtime),
        ShortTermRecallMemoryHarness(runtime=memory_runtime),
        LongTermRecallMemoryHarness(runtime=memory_runtime),
        MemoryPrepareEventHarness(runtime=memory_runtime),
        MemoryBootstrapHarness(runtime=memory_runtime),
        MemoryCommitInfoResetHarness(runtime=memory_runtime),
        MemoryCommitHarness(runtime=memory_runtime),
        MemoryCommitEventHarness(runtime=memory_runtime),
    ]


__all__ = ["build_default_memory_components"]
