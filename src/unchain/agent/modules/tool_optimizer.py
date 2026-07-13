from __future__ import annotations

from dataclasses import dataclass, field

from ...tools.exposure import ToolOptimizerConfig
from .base import BaseAgentModule


@dataclass(frozen=True)
class ToolOptimizerModule(BaseAgentModule):
    config: ToolOptimizerConfig = field(default_factory=ToolOptimizerConfig)
    name: str = field(default="tool_optimizer", init=False)

    def __post_init__(self) -> None:
        normalized = ToolOptimizerConfig.coerce(self.config)
        if normalized is None:
            normalized = ToolOptimizerConfig()
        object.__setattr__(self, "config", normalized)

    def configure(self, builder) -> None:
        builder.set_tool_optimizer_config(self.config)


__all__ = ["ToolOptimizerModule"]
