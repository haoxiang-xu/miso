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
        if isinstance(runtime, KernelMemoryRuntime):
            builder.attach_memory_runtime(runtime)
            return
        if isinstance(runtime, MemoryManager):
            builder.attach_memory_runtime(KernelMemoryRuntime.from_memory_manager(runtime))
            return
        if isinstance(runtime, MemoryConfig):
            builder.attach_memory_runtime(
                KernelMemoryRuntime.from_config(copy.deepcopy(runtime), store=self.store)
            )
            return
        if isinstance(runtime, dict):
            builder.attach_memory_runtime(
                KernelMemoryRuntime.from_config(
                    MemoryConfig(**copy.deepcopy(runtime)),
                    store=self.store,
                )
            )
            return
        raise TypeError(f"unsupported memory module value: {type(runtime).__name__}")
