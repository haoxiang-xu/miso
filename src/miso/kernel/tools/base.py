from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ...tools.toolkit import Toolkit
from ..delta import HarnessDelta
from ..harness import BaseRuntimeHarness, HarnessContext, RuntimeHarness, RuntimePhase
from ..state import RunState


@dataclass(frozen=True)
class ToolContext:
    harness_context: HarnessContext
    tool_component_name: str

    @property
    def state(self) -> RunState:
        return self.harness_context.state

    @property
    def phase(self) -> RuntimePhase:
        return self.harness_context.phase

    @property
    def event(self) -> dict[str, Any]:
        return self.harness_context.event_payload()

    @property
    def latest_version_id(self) -> str | None:
        return self.harness_context.latest_version_id

    @property
    def provider(self) -> str:
        return str(self.state.provider_state.provider or "")

    @property
    def model(self) -> str:
        return str(self.state.provider_state.model or "")

    @property
    def session_id(self) -> str:
        return str(self.state.session_state.session_id or "")

    @property
    def memory_namespace(self) -> str:
        return str(self.state.session_state.memory_namespace or "")

    @property
    def toolkit(self) -> Toolkit | None:
        toolkit = self.event.get("toolkit")
        return toolkit if isinstance(toolkit, Toolkit) else None

    @property
    def loop(self) -> Any:
        return self.event.get("loop")

    @property
    def callback(self) -> Any:
        return self.event.get("callback")

    @property
    def run_id(self) -> str:
        return str(self.event.get("run_id") or "kernel")

    @property
    def iteration(self) -> int:
        return int(self.state.iteration)

    def latest_messages(self) -> list[dict[str, Any]]:
        return self.harness_context.latest_messages()

    def view_messages(self, version_id: str | None = None) -> list[dict[str, Any]]:
        return self.harness_context.view_messages(version_id)


@runtime_checkable
class ToolHarness(RuntimeHarness, Protocol):
    ...


@dataclass
class BaseToolHarness(BaseRuntimeHarness):
    @property
    def created_by(self) -> str:
        return f"harness.{self.name}"

    def build_delta(self, context: HarnessContext) -> HarnessDelta | None:
        tool_context = ToolContext(harness_context=context, tool_component_name=self.name)
        return self.build_tool_delta(tool_context)

    def build_tool_delta(self, context: ToolContext) -> HarnessDelta | None:
        raise NotImplementedError
