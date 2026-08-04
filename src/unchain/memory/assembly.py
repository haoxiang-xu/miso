from __future__ import annotations

import copy
from enum import StrEnum
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
from .checkpoint import ExecutionCheckpointHarness
from .commit import MemoryCommitHarness
from .events import (
    MemoryCommitEventHarness,
    MemoryCommitInfoResetHarness,
    MemoryPrepareEventHarness,
    MemoryPrepareInfoResetHarness,
)
from .durability import DurabilityBootstrapHarness, DurabilityCheckpointHarness
from .recall_long_term import LongTermRecallMemoryHarness
from .runtime import KernelMemoryRuntime
from .short_term import ShortTermRecallMemoryHarness


class MemoryRuntimeComponentMode(StrEnum):
    FULL = "full"
    DURABILITY_ONLY = "durability_only"


def build_durability_memory_components(
    memory_runtime: KernelMemoryRuntime,
) -> list[Any]:
    if not isinstance(memory_runtime, KernelMemoryRuntime):
        raise TypeError("memory_runtime must be a KernelMemoryRuntime")
    return [
        DurabilityBootstrapHarness(runtime=memory_runtime),
        DurabilityCheckpointHarness(runtime=memory_runtime),
    ]


def build_default_memory_components(
    memory_runtime: KernelMemoryRuntime,
    *,
    semantic_context_owner: str | None = None,
) -> list[Any]:
    config = memory_runtime.config
    destructive_components = [
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
    ]
    durable_components = [
        MemoryPrepareInfoResetHarness(runtime=memory_runtime),
        MemoryPrepareEventHarness(runtime=memory_runtime),
        MemoryBootstrapHarness(runtime=memory_runtime),
        MemoryCommitInfoResetHarness(runtime=memory_runtime),
        MemoryCommitHarness(runtime=memory_runtime),
        MemoryCommitEventHarness(runtime=memory_runtime),
        ExecutionCheckpointHarness(runtime=memory_runtime),
    ]
    if semantic_context_owner is None:
        return [
            *destructive_components,
            durable_components[0],
            ShortTermRecallMemoryHarness(runtime=memory_runtime),
            LongTermRecallMemoryHarness(runtime=memory_runtime),
            *durable_components[1:],
        ]
    if not isinstance(semantic_context_owner, str) or not semantic_context_owner.strip():
        raise ValueError("semantic_context_owner must be a non-empty identifier")
    return durable_components


__all__ = [
    "MemoryRuntimeComponentMode",
    "build_default_memory_components",
    "build_durability_memory_components",
]
