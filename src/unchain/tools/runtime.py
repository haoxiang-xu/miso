from __future__ import annotations

import copy
import json
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


def _copy_tool_result_snapshot(tool_result: dict[str, Any]) -> dict[str, Any]:
    try:
        return copy.deepcopy(tool_result)
    except Exception:
        try:
            return json.loads(json.dumps(tool_result, default=str, ensure_ascii=False))
        except Exception:
            return {"result": str(tool_result)}


def run_tool_runtime_plugins(
    plugins: list[ToolRuntimePlugin],
    *,
    tool_call: ToolCall,
    context: Any,
    execution_guard: Any = None,
) -> ToolRuntimeOutcome | None:
    for plugin in plugins:
        if execution_guard is not None:
            execution_guard.renew()
        if not plugin.can_handle(tool_call=tool_call, context=context):
            continue
        if execution_guard is not None:
            execution_guard.renew()
        outcome = plugin.execute(tool_call=tool_call, context=context)
        if execution_guard is not None:
            execution_guard.assert_active()
        if isinstance(outcome, ToolRuntimeOutcome) and outcome.handled:
            return ToolRuntimeOutcome(
                handled=True,
                tool_result=_copy_tool_result_snapshot(outcome.tool_result) if isinstance(outcome.tool_result, dict) else outcome.tool_result,
                result_messages=copy.deepcopy(outcome.result_messages),
                state_updates=copy.deepcopy(outcome.state_updates),
                should_observe=bool(outcome.should_observe),
                suspend_override=outcome.suspend_override,
            )
    return None
