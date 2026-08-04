from __future__ import annotations

from dataclasses import dataclass, field

from ...context.harness import (
    ContextExecutionBindingHarness,
    ContextShadowCompilerHarness,
)
from ...context.runtime import ContextRuntime
from ...context.projector import SemanticEventProjectionMode
from .base import BaseAgentModule


@dataclass(frozen=True)
class ContextModule(BaseAgentModule):
    runtime: ContextRuntime = field(kw_only=True)
    name: str = field(default="context", init=False)

    def configure(self, builder) -> None:
        factory = self.runtime.execution_factory
        if (
            factory is not None
            and factory.projection_mode
            is SemanticEventProjectionMode.SHADOW_OBSERVED
        ):
            raise ValueError(
                "observation-only context runtime cannot become the active owner"
            )
        builder.attach_context_runtime(self.runtime)


@dataclass(frozen=True)
class ContextShadowModule(BaseAgentModule):
    """Explicitly opt in to a durable, observation-only Context V2 build."""

    runtime: ContextRuntime = field(kw_only=True)
    enabled: bool = field(default=False, kw_only=True)
    name: str = field(default="context_shadow", init=False)

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("context shadow enabled must be an exact boolean")

    def configure(self, builder) -> None:
        if not self.enabled:
            return
        if type(self.runtime) is not ContextRuntime:
            raise TypeError("context shadow runtime must be a ContextRuntime")
        if self.runtime.execution_factory is None:
            raise TypeError(
                "context shadow requires a factory ContextRuntime for durability"
            )
        if self.runtime.provider_turns_enabled:
            raise ValueError(
                "context shadow cannot claim durable provider-turn authority"
            )
        if (
            builder.semantic_context_owner is not None
            or builder.context_runtime is not None
        ):
            raise ValueError(
                "context shadow cannot be combined with an active semantic context owner"
            )
        if any(
            isinstance(harness, ContextShadowCompilerHarness)
            for harness in builder.harnesses
        ):
            raise ValueError("context shadow is already configured")
        builder.add_harness(ContextExecutionBindingHarness(runtime=self.runtime))
        builder.add_harness(ContextShadowCompilerHarness(runtime=self.runtime))


__all__ = ["ContextModule", "ContextShadowModule"]
