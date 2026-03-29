from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from ...tools import ToolConfirmationRequest, ToolConfirmationResponse, Toolkit
from ..types import ToolCall
from .common import emit_loop_event


@dataclass(frozen=True)
class ToolExecutionOutcome:
    tool_result: dict[str, Any]
    should_observe: bool
    denied: bool = False
    deny_reason: str = ""
    effective_arguments: Any = None


def execute_confirmable_tool_call(
    *,
    toolkit: Toolkit,
    tool_call: ToolCall,
    on_tool_confirm: Any,
    loop: Any,
    callback: Any,
    run_id: str,
    iteration: int,
) -> ToolExecutionOutcome:
    tool_obj = toolkit.get(tool_call.name)
    should_observe = bool(tool_obj is not None and tool_obj.observe)
    effective_arguments = copy.deepcopy(tool_call.arguments)
    denied = False
    deny_reason = ""

    if tool_obj is not None and tool_obj.requires_confirmation and callable(on_tool_confirm):
        confirmation_request = ToolConfirmationRequest(
            tool_name=tool_call.name,
            call_id=tool_call.call_id,
            arguments=tool_call.arguments if isinstance(tool_call.arguments, dict) else {},
            description=tool_obj.description,
        )
        response = ToolConfirmationResponse.from_raw(on_tool_confirm(confirmation_request))
        if not response.approved:
            denied = True
            deny_reason = response.reason
            emit_loop_event(
                loop,
                callback,
                "tool_denied",
                run_id,
                iteration=iteration,
                tool_name=tool_call.name,
                call_id=tool_call.call_id,
                reason=deny_reason,
            )
        elif response.modified_arguments is not None:
            effective_arguments = copy.deepcopy(response.modified_arguments)
            emit_loop_event(
                loop,
                callback,
                "tool_confirmed",
                run_id,
                iteration=iteration,
                tool_name=tool_call.name,
                call_id=tool_call.call_id,
            )

    if denied:
        tool_result = {
            "denied": True,
            "tool": tool_call.name,
            "reason": deny_reason or "User denied execution.",
        }
    else:
        tool_result = toolkit.execute(tool_call.name, effective_arguments)

    return ToolExecutionOutcome(
        tool_result=tool_result,
        should_observe=should_observe,
        denied=denied,
        deny_reason=deny_reason,
        effective_arguments=effective_arguments,
    )
