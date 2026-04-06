from __future__ import annotations

import copy
from dataclasses import dataclass

from ..input.human_input import ASK_USER_QUESTION_TOOL_NAME, HumanInputRequest, HumanInputResponse
from ..kernel.delta import HarnessDelta
from ..kernel.types import ToolCall
from .base import BaseToolHarness, ToolContext
from .messages import get_provider_message_builder
from .types import ToolBatchState


def parse_human_input_request(tool_call: ToolCall) -> HumanInputRequest:
    return HumanInputRequest.from_tool_arguments(
        tool_call.arguments,
        request_id=tool_call.call_id,
    )


@dataclass
class HumanInputResumeHarness(BaseToolHarness):
    name: str = "human_input_resume"
    phases: tuple[str, ...] = ("on_resume",)
    order: int = 100

    def build_tool_delta(self, context: ToolContext) -> HarnessDelta | None:
        continuation = context.event.get("continuation")
        response = context.event.get("response")
        if not isinstance(continuation, dict):
            raise TypeError("continuation must be a dict returned by KernelRunResult.continuation")
        if continuation.get("type") != "human_input_continuation":
            raise ValueError("continuation must be a human_input_continuation payload")

        request = HumanInputRequest.from_dict(continuation.get("request"))
        human_response = HumanInputResponse.from_raw(response, request=request)
        tool_call = ToolCall(
            call_id=str(continuation.get("call_id") or request.request_id),
            name=ASK_USER_QUESTION_TOOL_NAME,
            arguments={},
        )
        builder = get_provider_message_builder(context.provider)
        tool_message = builder.build_tool_result_message(
            tool_call=tool_call,
            tool_result=human_response.to_tool_result(),
        )

        use_previous_response_chain = bool(continuation.get("use_openai_previous_response_chain", False))
        next_model_input = (
            [copy.deepcopy(tool_message)]
            if context.provider == "openai"
            and use_previous_response_chain
            and isinstance(continuation.get("previous_response_id"), str)
            and continuation.get("previous_response_id")
            else None
        )

        return HarnessDelta.append(
            created_by=self.created_by,
            messages=[tool_message],
            state_updates={
                "transcript_append": [tool_message],
                "pending_tool_calls": [],
                "tool_batch_state": ToolBatchState(),
                "run_status": "running",
                "last_continuation": None,
                "next_model_input": next_model_input,
                "provider_state": {
                    "previous_response_id": continuation.get("previous_response_id"),
                    "use_previous_response_chain": use_previous_response_chain,
                },
                "suspend_state": {
                    "signal_kind": None,
                    "payload": {},
                },
            },
            trace={
                "request_id": request.request_id,
                "resumed_from_continuation": True,
            },
        )
