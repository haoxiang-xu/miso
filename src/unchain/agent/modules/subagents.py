from __future__ import annotations

from dataclasses import dataclass, field

from ...subagents.executor import SubagentExecutor
from ...subagents.plugin import SubagentToolPlugin
from ...subagents.runtime_tools import (
    build_delegate_to_subagent_tool,
    build_handoff_to_subagent_tool,
    build_spawn_worker_batch_tool,
)
from ...subagents.types import SubagentPolicy, SubagentTemplate
from .base import BaseAgentModule


@dataclass(frozen=True)
class SubagentModule(BaseAgentModule):
    templates: tuple[SubagentTemplate, ...] = field(default_factory=tuple)
    policy: SubagentPolicy = field(default_factory=SubagentPolicy)
    executor: SubagentExecutor | None = None
    name: str = "subagents"

    def configure(self, builder) -> None:
        plugin = SubagentToolPlugin(
            parent_agent=builder.agent,
            templates=self.templates,
            policy=self.policy,
            executor=self.executor or SubagentExecutor(
                max_parallel_workers=int(self.policy.max_parallel_workers),
                worker_timeout_seconds=float(self.policy.worker_timeout_seconds),
            ),
        )
        builder.add_tool(build_delegate_to_subagent_tool())
        builder.add_tool(build_handoff_to_subagent_tool())
        builder.add_tool(build_spawn_worker_batch_tool())
        builder.add_tool_runtime_plugin(plugin)
