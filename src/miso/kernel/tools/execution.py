from __future__ import annotations

import copy
from dataclasses import dataclass

from ...input.human_input import is_human_input_tool_name
from ...tools import Toolkit
from ..delta import HarnessDelta, SuspendSignal
from ..types import ToolBatchState, ToolCall
from .base import BaseToolHarness, ToolContext
from .common import append_executed_call_id, copy_messages, emit_loop_event
from .confirmation import execute_confirmable_tool_call
from .human_input import parse_human_input_request
from .messages import get_provider_message_builder
from .observation import inject_observation, observation_token_state


@dataclass
class ToolExecutionHarness(BaseToolHarness):
    name: str = "tool_execution"
    phases: tuple[str, ...] = ("after_model", "on_tool_call", "after_tool_batch")
    order: int = 100

    def build_tool_delta(self, context: ToolContext) -> HarnessDelta | None:
        if context.phase == "after_model":
            return self._after_model(context)
        if context.phase == "on_tool_call":
            return self._on_tool_call(context)
        if context.phase == "after_tool_batch":
            return self._after_tool_batch(context)
        return None

    def _after_model(self, context: ToolContext) -> HarnessDelta | None:
        tool_calls = list(context.state.pending_tool_calls)
        if not tool_calls:
            return None

        includes_human_input = any(is_human_input_tool_name(tool_call.name) for tool_call in tool_calls)
        if includes_human_input and len(tool_calls) > 1:
            error_text = "ask_user_question must be the only tool call in a turn"
            builder = get_provider_message_builder(context.provider)
            result_messages = [
                builder.build_tool_result_message(
                    tool_call=tool_call,
                    tool_result={"error": error_text, "tool": tool_call.name},
                )
                for tool_call in tool_calls
            ]
            return HarnessDelta(
                created_by=self.created_by,
                state_updates={
                    "tool_batch_state": ToolBatchState(
                        result_messages=result_messages,
                        should_observe=False,
                        awaiting_human_input=False,
                        human_input_request=None,
                        human_input_tool_call_id=None,
                        executed_call_ids=[tool_call.call_id for tool_call in tool_calls],
                    ),
                    "run_status": "running",
                },
                trace={
                    "mixed_human_input_batch": True,
                    "tool_call_count": len(tool_calls),
                },
            )

        return HarnessDelta(
            created_by=self.created_by,
            state_updates={
                "tool_batch_state": ToolBatchState(),
                "run_status": "running",
            },
            trace={"tool_call_count": len(tool_calls)},
        )

    def _on_tool_call(self, context: ToolContext) -> HarnessDelta | None:
        toolkit = context.toolkit if context.toolkit is not None else Toolkit()
        on_tool_confirm = context.event.get("on_tool_confirm")
        tool_call = context.event.get("tool_call")
        if not isinstance(tool_call, ToolCall):
            return None

        batch_state = context.state.tool_batch_state.copy()
        if tool_call.call_id in batch_state.executed_call_ids or batch_state.awaiting_human_input:
            return None

        emit_loop_event(
            context.loop,
            context.callback,
            "tool_call",
            context.run_id,
            iteration=context.iteration,
            tool_name=tool_call.name,
            call_id=tool_call.call_id,
            arguments=copy.deepcopy(tool_call.arguments),
        )

        if is_human_input_tool_name(tool_call.name):
            try:
                request = parse_human_input_request(tool_call)
            except Exception as exc:
                builder = get_provider_message_builder(context.provider)
                tool_result = {
                    "error": str(exc),
                    "tool": tool_call.name,
                }
                result_messages = copy_messages(batch_state.result_messages)
                result_messages.append(
                    builder.build_tool_result_message(tool_call=tool_call, tool_result=tool_result)
                )
                emit_loop_event(
                    context.loop,
                    context.callback,
                    "tool_result",
                    context.run_id,
                    iteration=context.iteration,
                    tool_name=tool_call.name,
                    call_id=tool_call.call_id,
                    result=tool_result,
                )
                return HarnessDelta(
                    created_by=self.created_by,
                    state_updates={
                        "tool_batch_state": ToolBatchState(
                            result_messages=result_messages,
                            should_observe=batch_state.should_observe,
                            awaiting_human_input=False,
                            human_input_request=batch_state.human_input_request,
                            human_input_tool_call_id=batch_state.human_input_tool_call_id,
                            executed_call_ids=append_executed_call_id(batch_state, tool_call.call_id),
                        ),
                    },
                )

            emit_loop_event(
                context.loop,
                context.callback,
                "human_input_requested",
                context.run_id,
                iteration=context.iteration,
                request_id=request.request_id,
                kind=request.kind,
                title=request.title,
                question=request.question,
                selection_mode=request.selection_mode,
                options=[option.to_dict() for option in request.options],
                allow_other=request.allow_other,
                other_label=request.other_label,
                other_placeholder=request.other_placeholder,
                min_selected=request.min_selected,
                max_selected=request.max_selected,
            )
            return HarnessDelta(
                created_by=self.created_by,
                state_updates={
                    "tool_batch_state": ToolBatchState(
                        result_messages=copy_messages(batch_state.result_messages),
                        should_observe=False,
                        awaiting_human_input=True,
                        human_input_request=request,
                        human_input_tool_call_id=tool_call.call_id,
                        executed_call_ids=append_executed_call_id(batch_state, tool_call.call_id),
                    ),
                },
            )

        outcome = execute_confirmable_tool_call(
            toolkit=toolkit,
            tool_call=tool_call,
            on_tool_confirm=on_tool_confirm,
            loop=context.loop,
            callback=context.callback,
            run_id=context.run_id,
            iteration=context.iteration,
        )
        should_observe = batch_state.should_observe or outcome.should_observe
        builder = get_provider_message_builder(context.provider)
        result_messages = copy_messages(batch_state.result_messages)
        result_messages.append(
            builder.build_tool_result_message(tool_call=tool_call, tool_result=outcome.tool_result)
        )
        emit_loop_event(
            context.loop,
            context.callback,
            "tool_result",
            context.run_id,
            iteration=context.iteration,
            tool_name=tool_call.name,
            call_id=tool_call.call_id,
            result=copy.deepcopy(outcome.tool_result),
        )
        return HarnessDelta(
            created_by=self.created_by,
            state_updates={
                "tool_batch_state": ToolBatchState(
                    result_messages=result_messages,
                    should_observe=should_observe,
                    awaiting_human_input=False,
                    human_input_request=batch_state.human_input_request,
                    human_input_tool_call_id=batch_state.human_input_tool_call_id,
                    executed_call_ids=append_executed_call_id(batch_state, tool_call.call_id),
                ),
            },
        )

    def _after_tool_batch(self, context: ToolContext) -> HarnessDelta | None:
        payload = copy.deepcopy(context.event.get("payload") or {})
        response_format = context.event.get("response_format")
        batch_state = context.state.tool_batch_state.copy()

        if batch_state.awaiting_human_input and batch_state.human_input_request is not None:
            continuation = None
            if context.loop is not None and hasattr(context.loop, "build_human_input_continuation"):
                continuation = context.loop.build_human_input_continuation(
                    request=batch_state.human_input_request,
                    payload=payload,
                    response_format=response_format,
                    next_iteration=context.iteration + 1,
                    max_iterations=int(context.event.get("max_iterations") or 0),
                    state=context.state,
                )
            return HarnessDelta(
                created_by=self.created_by,
                state_updates={
                    "run_status": "awaiting_human_input",
                    "last_continuation": continuation,
                    "pending_tool_calls": [],
                    "next_model_input": None,
                    "tool_batch_state": batch_state,
                },
                suspend=(
                    SuspendSignal(
                        kind="human_input",
                        payload={
                            "continuation": copy.deepcopy(continuation) if isinstance(continuation, dict) else {},
                            "request": batch_state.human_input_request.to_dict(),
                        },
                    )
                    if isinstance(continuation, dict)
                    else None
                ),
                trace={"awaiting_human_input": True},
            )

        result_messages = copy_messages(batch_state.result_messages)
        token_state = {}
        if batch_state.should_observe and result_messages:
            observation = ""
            observe_usage = None
            if context.loop is not None and hasattr(context.loop, "observe_tool_batch"):
                observation, observe_usage = context.loop.observe_tool_batch(
                    full_messages=context.state.transcript,
                    tool_messages=result_messages,
                    payload=payload,
                    callback=context.callback,
                    iteration=context.iteration,
                )
            token_state = observation_token_state(
                consumed_tokens=context.state.token_state.consumed_tokens,
                input_tokens=context.state.token_state.input_tokens,
                output_tokens=context.state.token_state.output_tokens,
                observe_usage=observe_usage,
            )
            if observation:
                inject_observation(result_messages[-1], observation)
                emit_loop_event(
                    context.loop,
                    context.callback,
                    "observation",
                    context.run_id,
                    iteration=context.iteration,
                    content=observation,
                )

        if not result_messages:
            return HarnessDelta(
                created_by=self.created_by,
                state_updates={
                    "pending_tool_calls": [],
                    "tool_batch_state": ToolBatchState(),
                    "run_status": "running",
                },
            )

        next_model_input = None
        if context.provider == "openai" and context.state.provider_state.use_previous_response_chain:
            if context.state.provider_state.previous_response_id:
                next_model_input = copy_messages(result_messages)

        return HarnessDelta.append(
            created_by=self.created_by,
            messages=result_messages,
            state_updates={
                "transcript_append": result_messages,
                "pending_tool_calls": [],
                "tool_batch_state": ToolBatchState(),
                "run_status": "running",
                "last_continuation": None,
                "next_model_input": next_model_input,
                "token_state": token_state,
                "suspend_state": {
                    "signal_kind": None,
                    "payload": {},
                },
            },
            trace={
                "result_message_count": len(result_messages),
                "observed": bool(batch_state.should_observe),
            },
        )
