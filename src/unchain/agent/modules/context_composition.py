"""Official AgentModule seam for Context Composition bootstrap."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from ...context.composition import (
    ContextCompositionBootstrapHarness,
    validate_private_context_composition_hint,
)
from .base import BaseAgentModule


class ContextCompositionBootstrapModuleError(RuntimeError):
    """The Context Composition bootstrap module is invalid or duplicated."""


@dataclass(frozen=True)
class ContextCompositionBootstrapModule(BaseAgentModule):
    """Attach the official content-free contribution source for a run."""

    private_hint: Mapping[str, Any] = field(kw_only=True, repr=False)
    name: str = field(default="context_composition_bootstrap", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.private_hint, Mapping):
            raise TypeError("private_hint must be an exact context composition object")
        normalized = validate_private_context_composition_hint(dict(self.private_hint))
        object.__setattr__(self, "private_hint", MappingProxyType(normalized))

    @classmethod
    def from_private_hint(
        cls,
        private_hint: Mapping[str, Any],
    ) -> ContextCompositionBootstrapModule:
        """Build the module from the durable private-v1 aggregate tuple."""

        return cls(private_hint=private_hint)

    def configure(self, builder) -> None:
        if any(
            isinstance(harness, ContextCompositionBootstrapHarness)
            for harness in builder.harnesses
        ):
            raise ContextCompositionBootstrapModuleError(
                "Context Composition bootstrap harness is already attached"
            )
        builder.add_harness(
            ContextCompositionBootstrapHarness(private_hint=self.private_hint)
        )


__all__ = [
    "ContextCompositionBootstrapModule",
    "ContextCompositionBootstrapModuleError",
]
