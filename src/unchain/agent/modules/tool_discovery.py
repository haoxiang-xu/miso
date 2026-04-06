from __future__ import annotations

from dataclasses import dataclass, field

from ...tools.discovery import ToolDiscoveryConfig, ToolDiscoveryRuntime
from .base import BaseAgentModule


@dataclass(frozen=True)
class ToolDiscoveryModule(BaseAgentModule):
    config: ToolDiscoveryConfig
    name: str = field(default="tool_discovery", init=False)

    def __post_init__(self) -> None:
        normalized = ToolDiscoveryConfig.coerce(self.config)
        if normalized is None:
            raise ValueError("ToolDiscoveryModule requires a config")
        object.__setattr__(self, "config", normalized)

    def configure(self, builder) -> None:
        runtime = ToolDiscoveryRuntime(
            config=self.config,
            runtime_toolkit=builder.toolkit,
        )
        for tool_obj in runtime.build_tools():
            builder.add_tool(tool_obj)

        def _shutdown_runtime(result):
            runtime.shutdown()
            return result

        builder.add_run_hook(_shutdown_runtime)


__all__ = ["ToolDiscoveryModule"]
