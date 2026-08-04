"""Official AgentModule seam for deterministic Pinned Task State bootstrap."""

from __future__ import annotations

from dataclasses import dataclass, field

from ...context.task_state_bootstrap import (
    PinnedTaskStateBootstrapBindingResolver,
    PinnedTaskStateBootstrapHarness,
)
from ...context.task_state_runtime import TaskStateContextRuntime
from .base import BaseAgentModule


class PinnedTaskStateBootstrapModuleError(RuntimeError):
    """The bootstrap module is not attached to its exact context runtime."""


@dataclass(frozen=True)
class PinnedTaskStateBootstrapModule(BaseAgentModule):
    """Attach task-state bootstrap after the official task-state context owner."""

    runtime: TaskStateContextRuntime = field(kw_only=True, repr=False)
    binding_resolver: PinnedTaskStateBootstrapBindingResolver = field(
        kw_only=True,
        repr=False,
    )
    name: str = field(default="pinned_task_state_bootstrap", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, TaskStateContextRuntime):
            raise TypeError("runtime must be a TaskStateContextRuntime")
        if not callable(self.binding_resolver):
            raise TypeError("binding_resolver must be callable")

    def configure(self, builder) -> None:
        attached_runtime = getattr(builder, "context_runtime", None)
        if attached_runtime is not self.runtime:
            if not isinstance(attached_runtime, TaskStateContextRuntime):
                raise PinnedTaskStateBootstrapModuleError(
                    "Pinned Task State bootstrap requires its "
                    "TaskStateContextRuntime to be attached first"
                )
            raise PinnedTaskStateBootstrapModuleError(
                "Pinned Task State bootstrap runtime does not match the "
                "attached TaskStateContextRuntime"
            )
        if any(
            isinstance(harness, PinnedTaskStateBootstrapHarness)
            for harness in builder.harnesses
        ):
            raise PinnedTaskStateBootstrapModuleError(
                "Pinned Task State bootstrap harness is already attached"
            )
        harness = PinnedTaskStateBootstrapHarness(
            binding_resolver=self.binding_resolver,
        )
        if harness.order != -990:  # pragma: no cover - load-bearing invariant
            raise PinnedTaskStateBootstrapModuleError(
                "Pinned Task State bootstrap harness order changed"
            )
        builder.add_harness(harness)


__all__ = [
    "PinnedTaskStateBootstrapModule",
    "PinnedTaskStateBootstrapModuleError",
]
