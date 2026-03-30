from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..kernel.delta import SuspendSignal
from ..kernel.types import ToolCall


@dataclass(frozen=True)
class ToolRuntimeOutcome:
    handled: bool = True
    tool_result: dict[str, Any] | None = None
    result_messages: list[dict[str, Any]] = field(default_factory=list)
    state_updates: dict[str, Any] = field(default_factory=dict)
    should_observe: bool = False
    suspend_override: SuspendSignal | None = None


@runtime_checkable
class ToolRuntimePlugin(Protocol):
    def can_handle(self, *, tool_call: ToolCall, context: Any) -> bool:
        ...

    def execute(self, *, tool_call: ToolCall, context: Any) -> ToolRuntimeOutcome:
        ...


def run_tool_runtime_plugins(
    plugins: list[ToolRuntimePlugin],
    *,
    tool_call: ToolCall,
    context: Any,
) -> ToolRuntimeOutcome | None:
    for plugin in plugins:
        if not plugin.can_handle(tool_call=tool_call, context=context):
            continue
        outcome = plugin.execute(tool_call=tool_call, context=context)
        if isinstance(outcome, ToolRuntimeOutcome) and outcome.handled:
            return ToolRuntimeOutcome(
                handled=True,
                tool_result=copy.deepcopy(outcome.tool_result) if isinstance(outcome.tool_result, dict) else outcome.tool_result,
                result_messages=copy.deepcopy(outcome.result_messages),
                state_updates=copy.deepcopy(outcome.state_updates),
                should_observe=bool(outcome.should_observe),
                suspend_override=outcome.suspend_override,
            )
    return None
