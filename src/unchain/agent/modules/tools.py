from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import BaseAgentModule


@dataclass(frozen=True)
class ToolsModule(BaseAgentModule):
    tools: tuple[Any, ...] = field(default_factory=tuple)
    name: str = "tools"

    def configure(self, builder) -> None:
        for entry in self.tools:
            builder.add_tool(entry)
