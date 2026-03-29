from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from .delta import HarnessDelta
from .state import RunState


RuntimePhase = Literal[
    "bootstrap",
    "before_model",
    "after_model",
    "on_tool_call",
    "after_tool_batch",
    "before_commit",
    "on_suspend",
    "on_resume",
]


@dataclass(frozen=True)
class HarnessContext:
    state: RunState
    phase: RuntimePhase
    event: dict[str, Any] = field(default_factory=dict)

    @property
    def latest_version_id(self) -> str | None:
        return self.state.latest_version_id

    def latest_messages(self) -> list[dict[str, Any]]:
        return self.state.latest_messages()

    def view_messages(self, version_id: str | None = None) -> list[dict[str, Any]]:
        return self.state.view_messages(version_id)

    def event_payload(self) -> dict[str, Any]:
        return copy.deepcopy(self.event)


@runtime_checkable
class RuntimeHarness(Protocol):
    name: str
    phases: tuple[RuntimePhase, ...]
    order: int

    def applies(self, context: HarnessContext) -> bool:
        ...

    def build_delta(self, context: HarnessContext) -> HarnessDelta | None:
        ...


@dataclass
class BaseRuntimeHarness:
    name: str
    phases: tuple[RuntimePhase, ...]
    order: int = 100

    def applies(self, context: HarnessContext) -> bool:
        return context.phase in self.phases

    def build_delta(self, context: HarnessContext) -> HarnessDelta | None:
        raise NotImplementedError
