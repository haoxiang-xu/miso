"""Official AgentModule seam for graph step checkpoint admission."""

from __future__ import annotations

from dataclasses import dataclass, field

from ...context.graph_harness import (
    GraphStepBootstrapBindingResolver,
    GraphStepBootstrapHarness,
    GraphStepResumeBindingResolver,
    GraphStepResumeHarness,
)
from ...context.harness import (
    ContextExecutionBindingHarness,
    ContextShadowCompilerHarness,
)
from ...context.runtime import ContextRuntime
from .base import BaseAgentModule


class GraphStepBootstrapModuleError(RuntimeError):
    """The graph bootstrap module is not attached to its context owner."""


def _require_context_binding(
    builder,
    runtime: ContextRuntime,
    *,
    error_type: type[RuntimeError],
    label: str,
) -> None:
    attached_runtime = getattr(builder, "context_runtime", None)
    bindings = tuple(
        harness
        for harness in builder.harnesses
        if isinstance(harness, ContextExecutionBindingHarness)
    )
    shadow_compilers = tuple(
        harness
        for harness in builder.harnesses
        if isinstance(harness, ContextShadowCompilerHarness)
    )
    if attached_runtime is runtime:
        if shadow_compilers:
            raise error_type(
                f"{label} cannot mix active and shadow context ownership"
            )
    elif attached_runtime is None:
        if getattr(builder, "semantic_context_owner", None) is not None:
            raise error_type(
                f"{label} cannot attach to a foreign semantic context owner"
            )
        if (
            len(shadow_compilers) != 1
            or shadow_compilers[0].runtime is not runtime
        ):
            raise error_type(
                f"{label} requires its official ContextRuntime to be attached "
                "first or enabled by one official ContextShadowModule"
            )
    elif isinstance(attached_runtime, ContextRuntime):
        raise error_type(
            f"{label} runtime does not match the attached ContextRuntime"
        )
    else:
        raise error_type(
            f"{label} requires its official ContextRuntime to be attached first"
        )
    if len(bindings) != 1 or bindings[0].runtime is not runtime:
        raise error_type(
            f"{label} requires one official context binding harness"
        )


@dataclass(frozen=True)
class GraphStepBootstrapModule(BaseAgentModule):
    """Attach graph admission after context and pinned-state bootstrap."""

    runtime: ContextRuntime = field(kw_only=True, repr=False)
    binding_resolver: GraphStepBootstrapBindingResolver = field(
        kw_only=True,
        repr=False,
    )
    name: str = field(default="graph_step_bootstrap", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, ContextRuntime):
            raise TypeError("runtime must be an official ContextRuntime")
        if not callable(self.binding_resolver):
            raise TypeError("binding_resolver must be callable")

    def configure(self, builder) -> None:
        _require_context_binding(
            builder,
            self.runtime,
            error_type=GraphStepBootstrapModuleError,
            label="Graph step bootstrap",
        )
        if any(
            isinstance(
                harness,
                (GraphStepBootstrapHarness, GraphStepResumeHarness),
            )
            for harness in builder.harnesses
        ):
            raise GraphStepBootstrapModuleError(
                "Graph checkpoint admission harness is already attached"
            )
        harness = GraphStepBootstrapHarness(
            runtime=self.runtime,
            binding_resolver=self.binding_resolver,
        )
        if harness.order != -980:  # pragma: no cover - load-bearing invariant
            raise GraphStepBootstrapModuleError(
                "Graph step bootstrap harness order changed"
            )
        builder.add_harness(harness)


class GraphStepResumeModuleError(RuntimeError):
    """The graph resume module is not attached to its context owner."""


@dataclass(frozen=True)
class GraphStepResumeModule(BaseAgentModule):
    """Attach exact resume admission after official context input persistence."""

    runtime: ContextRuntime = field(kw_only=True, repr=False)
    binding_resolver: GraphStepResumeBindingResolver = field(
        kw_only=True,
        repr=False,
    )
    name: str = field(default="graph_step_resume", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, ContextRuntime):
            raise TypeError("runtime must be an official ContextRuntime")
        if not callable(self.binding_resolver):
            raise TypeError("binding_resolver must be callable")

    def configure(self, builder) -> None:
        _require_context_binding(
            builder,
            self.runtime,
            error_type=GraphStepResumeModuleError,
            label="Graph step resume",
        )
        if any(
            isinstance(
                harness,
                (GraphStepBootstrapHarness, GraphStepResumeHarness),
            )
            for harness in builder.harnesses
        ):
            raise GraphStepResumeModuleError(
                "Graph checkpoint admission harness is already attached"
            )
        harness = GraphStepResumeHarness(
            runtime=self.runtime,
            binding_resolver=self.binding_resolver,
        )
        if harness.order != -980:  # pragma: no cover - load-bearing invariant
            raise GraphStepResumeModuleError(
                "Graph step resume harness order changed"
            )
        builder.add_harness(harness)


__all__ = [
    "GraphStepBootstrapModule",
    "GraphStepBootstrapModuleError",
    "GraphStepResumeModule",
    "GraphStepResumeModuleError",
]
