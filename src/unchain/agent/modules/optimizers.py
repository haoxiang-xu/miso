from __future__ import annotations

from dataclasses import dataclass, field

from .base import BaseAgentModule


@dataclass(frozen=True)
class OptimizersModule(BaseAgentModule):
    harnesses: tuple[object, ...] = field(default_factory=tuple)
    name: str = "optimizers"

    def configure(self, builder) -> None:
        for harness in self.harnesses:
            builder.add_harness(harness)
