from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ...jobs.plugin import DurableShellJobPlugin
from .base import BaseAgentModule

if TYPE_CHECKING:
    from ...jobs.process import ProcessJobSupervisor


@dataclass(frozen=True)
class JobsModule(BaseAgentModule):
    """Attach durable shell-job routing without replacing the shell tool."""

    supervisor: ProcessJobSupervisor
    name: str = field(default="jobs", init=False)

    def configure(self, builder) -> None:
        builder.add_tool_runtime_plugin(
            DurableShellJobPlugin(supervisor=self.supervisor)
        )


__all__ = ["JobsModule"]
