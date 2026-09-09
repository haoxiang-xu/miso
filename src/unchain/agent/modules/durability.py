from __future__ import annotations

from dataclasses import dataclass, field

from ...memory import KernelMemoryRuntime, MemoryRuntimeComponentMode
from .base import BaseAgentModule


@dataclass(frozen=True)
class DurabilityModule(BaseAgentModule):
    """Attach checkpoint/interaction durability without semantic memory."""

    runtime: KernelMemoryRuntime = field(kw_only=True)
    name: str = field(default="durability", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, KernelMemoryRuntime):
            raise TypeError("runtime must be a KernelMemoryRuntime")

    def configure(self, builder) -> None:
        builder.attach_memory_runtime(
            self.runtime,
            component_mode=MemoryRuntimeComponentMode.DURABILITY_ONLY,
        )


__all__ = ["DurabilityModule"]
