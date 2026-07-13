from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from ...memory import KernelMemoryRuntime, MemoryConfig, MemoryManager, SessionStore
from .base import BaseAgentModule


@dataclass(frozen=True)
class MemoryModule(BaseAgentModule):
    memory: KernelMemoryRuntime | MemoryManager | MemoryConfig | dict[str, Any] | None = None
    store: SessionStore | None = None
    name: str = "memory"

    def configure(self, builder) -> None:
        runtime = self.memory
        if runtime is None:
            return
        cache_key = f"memory_runtime:{id(self)}"
        cached_runtime = builder.state.module_state.get(cache_key)
        if isinstance(cached_runtime, KernelMemoryRuntime):
            builder.attach_memory_runtime(cached_runtime)
            return
        if isinstance(runtime, KernelMemoryRuntime):
            resolved_runtime = runtime
        elif isinstance(runtime, MemoryManager):
            resolved_runtime = KernelMemoryRuntime.from_memory_manager(runtime)
        elif isinstance(runtime, MemoryConfig):
            resolved_runtime = KernelMemoryRuntime.from_config(
                copy.deepcopy(runtime),
                store=self.store,
            )
        elif isinstance(runtime, dict):
            resolved_runtime = KernelMemoryRuntime.from_config(
                MemoryConfig(**copy.deepcopy(runtime)),
                store=self.store,
            )
        else:
            raise TypeError(f"unsupported memory module value: {type(runtime).__name__}")
        builder.state.module_state[cache_key] = resolved_runtime
        builder.attach_memory_runtime(resolved_runtime)
