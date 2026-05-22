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


# Keys in `event` that are object references rather than data — deep-copying
# them either drags in un-pickleable internals (rich Console RLocks, httpx
# clients, KernelLoop holding the provider) or is just wasted work because the
# harness only reads them. We shallow-copy these and deep-copy the rest, so
# event_payload() stays safe for arbitrary user-supplied callbacks while still
# isolating data fields like `turn_result` or `tool_call`.
_EVENT_REFERENCE_KEYS: frozenset[str] = frozenset(
    {
        "callback",
        "loop",
        "toolkit",
        "on_tool_confirm",
        "on_human_input",
        "on_max_iterations",
        "tool_runtime_plugins",
        "tool_runtime_config",
    }
)


def _safe_event_deepcopy(event: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy an event dict, but pass reference-typed fields through.

    The kernel sometimes stuffs the loop / callback / toolkit into the
    dispatch-phase event so harnesses can reach them. Those are object
    references, not data — copying them can blow up on un-pickleable
    internals (e.g. rich Console RLocks, httpx clients).
    """

    if not isinstance(event, dict):
        return copy.deepcopy(event)
    out: dict[str, Any] = {}
    for key, value in event.items():
        if key in _EVENT_REFERENCE_KEYS:
            out[key] = value
        else:
            out[key] = copy.deepcopy(value)
    return out


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
        return _safe_event_deepcopy(self.event)


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
