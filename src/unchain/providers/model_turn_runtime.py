from __future__ import annotations

import copy
import json
from dataclasses import replace
from typing import Any, Callable

from ..kernel.delta import HarnessDelta
from ..kernel.provider_replay import (
    ProviderReplayFrameError,
    merge_model_turn_replay_frame,
    strict_json_copy,
    tool_schema_digest,
    tool_schema_manifest,
)
from ..kernel.state import RunState
from ..kernel.types import ModelTurnResult
from ..retry import RetryConfig, RetryContext, fetch_turn_with_retry
from ..tools.toolkit import Toolkit
from .base import ModelIO, ModelTurnRequest
from .context_assembler import ProviderContextAssembler


_REPLAY_FORMATS = {
    "openai": "openai.responses.v1",
    "anthropic": "anthropic.messages.v1",
    "hyperspace": "anthropic.messages.v1",
    "ollama": "ollama.chat.v1",
}


def _provider_object_arguments(value: Any, *, provider: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return strict_json_copy(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ProviderReplayFrameError(
                f"{provider} fallback tool arguments must be a JSON object"
            ) from exc
        if isinstance(parsed, dict):
            return strict_json_copy(parsed)
    raise ProviderReplayFrameError(
        f"{provider} fallback tool arguments must be a JSON object"
    )


def _fallback_assistant_messages(
    provider: str,
    turn: ModelTurnResult,
) -> list[dict[str, Any]]:
    messages = strict_json_copy(turn.assistant_messages)
    if not turn.tool_calls:
        return messages

    call_ids = [str(call.call_id or "") for call in turn.tool_calls]
    call_names = [str(call.name or "") for call in turn.tool_calls]
    if (
        any(not call_id for call_id in call_ids)
        or any(not name for name in call_names)
        or len(set(call_ids)) != len(call_ids)
    ):
        raise ProviderReplayFrameError(
            "fallback model adapter returned invalid or duplicate tool calls"
        )

    if provider == "openai":
        semantic_messages: list[dict[str, Any]] = []
        for raw in messages:
            message = copy.deepcopy(raw)
            if message.get("type") == "function_call":
                continue
            if message.get("role") == "assistant" and "tool_calls" in message:
                message.pop("tool_calls", None)
                if message.get("content") in (None, "", []):
                    continue
            semantic_messages.append(message)
        for call in turn.tool_calls:
            arguments = call.arguments
            if isinstance(arguments, dict):
                arguments = json.dumps(
                    arguments,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
            elif arguments is None:
                arguments = "{}"
            elif not isinstance(arguments, str):
                raise ProviderReplayFrameError(
                    "OpenAI fallback tool arguments must be a JSON string or object"
                )
            semantic_messages.append(
                {
                    "type": "function_call",
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": arguments,
                }
            )
        return semantic_messages

    if provider in {"anthropic", "hyperspace"}:
        semantic_messages = []
        assistant_index: int | None = None
        for raw in messages:
            message = copy.deepcopy(raw)
            if message.get("role") != "assistant":
                semantic_messages.append(message)
                continue
            content = message.get("content")
            if isinstance(content, str):
                blocks = (
                    [{"type": "text", "text": content}]
                    if content
                    else []
                )
            elif isinstance(content, list):
                blocks = [
                    copy.deepcopy(block)
                    for block in content
                    if isinstance(block, dict) and block.get("type") != "tool_use"
                ]
            else:
                blocks = []
            if not blocks:
                continue
            message["content"] = blocks
            semantic_messages.append(message)
            assistant_index = len(semantic_messages) - 1
        tool_blocks = [
            {
                "type": "tool_use",
                "id": call.call_id,
                "name": call.name,
                "input": _provider_object_arguments(
                    call.arguments,
                    provider=provider,
                ),
            }
            for call in turn.tool_calls
        ]
        if assistant_index is None:
            semantic_messages.append(
                {"role": "assistant", "content": tool_blocks}
            )
        else:
            semantic_messages[assistant_index]["content"].extend(tool_blocks)
        return semantic_messages

    if provider == "ollama":
        semantic_messages = []
        assistant_index = None
        for raw in messages:
            message = copy.deepcopy(raw)
            if message.get("role") == "assistant":
                message.pop("tool_calls", None)
                if message.get("content") in (None, "", []) and not message.get("thinking"):
                    continue
                assistant_index = len(semantic_messages)
            semantic_messages.append(message)
        tool_calls = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": _provider_object_arguments(
                        call.arguments,
                        provider=provider,
                    ),
                },
            }
            for call in turn.tool_calls
        ]
        if assistant_index is None:
            semantic_messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": tool_calls,
                }
            )
        else:
            semantic_messages[assistant_index]["tool_calls"] = tool_calls
        return semantic_messages

    return messages


def _with_fallback_replay_frame(
    *,
    state: RunState,
    request: ModelTurnRequest,
    turn: ModelTurnResult,
) -> ModelTurnResult:
    if isinstance(turn.provider_replay_frame, dict):
        return turn
    provider = str(state.provider_state.provider or "").strip().lower()
    replay_format = _REPLAY_FORMATS.get(provider)
    if replay_format is None:
        return turn
    assistant_messages = _fallback_assistant_messages(provider, turn)
    remote_without_local_prefix = bool(
        request.previous_response_id
        and not isinstance(request.fallback_messages, list)
    )
    base_messages = (
        request.fallback_messages
        if isinstance(request.fallback_messages, list)
        else request.messages
    )
    complete = not remote_without_local_prefix and not bool(turn.reasoning_items)
    frame = {
        "format": replay_format,
        "complete": complete,
        "items": strict_json_copy(
            [*base_messages, *assistant_messages]
        ),
        "mode": "replace",
        "source": "kernel_semantic_model_adapter_fallback",
        "tool_schema_digest": tool_schema_digest(request.toolkit, provider),
        "tool_schema_manifest": tool_schema_manifest(request.toolkit, provider),
    }
    if not complete:
        frame["incomplete_reason"] = (
            "model adapter did not return a provider-native replay frame for "
            "remote or reasoning output"
        )
    if remote_without_local_prefix:
        frame["mode"] = "append_response"
        frame["response_items"] = strict_json_copy(assistant_messages)
    return replace(
        turn,
        assistant_messages=assistant_messages,
        provider_replay_frame=frame,
    )


def build_model_turn_request(
    state: RunState,
    *,
    payload: dict[str, Any] | None = None,
    toolkit: Toolkit | None = None,
    callback: Any = None,
    verbose: bool = False,
    run_id: str = "kernel",
    emit_stream: bool = False,
    response_format: Any = None,
    openai_text_format: dict[str, Any] | None = None,
) -> ModelTurnRequest:
    resolved_toolkit = toolkit if toolkit is not None else Toolkit()
    assembly = ProviderContextAssembler().assemble(
        state,
        toolkit=resolved_toolkit,
    )
    return ModelTurnRequest(
        messages=assembly.messages,
        payload=dict(payload or {}),
        response_format=response_format,
        callback=callback,
        verbose=verbose,
        run_id=run_id,
        iteration=state.iteration,
        toolkit=resolved_toolkit,
        emit_stream=emit_stream,
        previous_response_id=assembly.previous_response_id,
        fallback_messages=assembly.fallback_messages,
        openai_text_format=openai_text_format,
        context_mode=assembly.mode,
    )


def fetch_built_model_turn(
    *,
    model_io: ModelIO | None,
    retry_config: RetryConfig,
    state: RunState,
    request: ModelTurnRequest,
    before_attempt: Callable[[int], None] | None = None,
    after_attempt: Callable[[int, str, str, str], None] | None = None,
) -> ModelTurnResult:
    if model_io is None:
        raise RuntimeError("KernelLoop.model_io is not configured")
    if type(request) is not ModelTurnRequest:
        raise TypeError("request must be an exact ModelTurnRequest")
    context = RetryContext(
        run_id=request.run_id,
        iteration=request.iteration,
        is_background=(request.run_id == "observe"),
    )
    turn = fetch_turn_with_retry(
        model_io=model_io,
        request=request,
        config=retry_config,
        context=context,
        before_attempt=before_attempt,
        after_attempt=after_attempt,
    )
    return _with_fallback_replay_frame(
        state=state,
        request=request,
        turn=turn,
    )


def fetch_model_turn(
    *,
    model_io: ModelIO | None,
    retry_config: RetryConfig,
    state: RunState,
    payload: dict[str, Any] | None = None,
    toolkit: Toolkit | None = None,
    callback: Any = None,
    verbose: bool = False,
    run_id: str = "kernel",
    emit_stream: bool = False,
    response_format: Any = None,
    openai_text_format: dict[str, Any] | None = None,
    before_attempt: Callable[[int], None] | None = None,
    after_attempt: Callable[[int, str, str, str], None] | None = None,
) -> ModelTurnResult:
    request = build_model_turn_request(
        state,
        payload=payload,
        toolkit=toolkit,
        callback=callback,
        verbose=verbose,
        run_id=run_id,
        emit_stream=emit_stream,
        response_format=response_format,
        openai_text_format=openai_text_format,
    )
    return fetch_built_model_turn(
        model_io=model_io,
        retry_config=retry_config,
        state=state,
        request=request,
        before_attempt=before_attempt,
        after_attempt=after_attempt,
    )


def build_model_turn_delta(
    state: RunState,
    turn: ModelTurnResult,
    *,
    created_by: str = "kernel.model_turn",
) -> HarnessDelta:
    replay_frame = merge_model_turn_replay_frame(
        state,
        turn.provider_replay_frame,
    )
    state_updates = {
        "transcript_append": turn.assistant_messages,
        "pending_tool_calls": list(turn.tool_calls),
        "last_model_turn": turn,
        "provider_state": {
            "previous_response_id": turn.response_id,
        },
        "next_model_input": None,
        "remote_continuation_input": None,
        "run_status": "running",
        "token_state": {
            "consumed_tokens": state.token_state.consumed_tokens + int(turn.consumed_tokens or 0),
            "input_tokens": state.token_state.input_tokens + int(turn.input_tokens or 0),
            "output_tokens": state.token_state.output_tokens + int(turn.output_tokens or 0),
            "cache_read_input_tokens": (
                state.token_state.cache_read_input_tokens + int(turn.cache_read_input_tokens or 0)
            ),
            "cache_creation_input_tokens": (
                state.token_state.cache_creation_input_tokens + int(turn.cache_creation_input_tokens or 0)
            ),
            "last_turn_tokens": int(turn.consumed_tokens or 0),
            "last_turn_input_tokens": int(turn.input_tokens or 0),
            "last_turn_output_tokens": int(turn.output_tokens or 0),
            "last_turn_cache_read_input_tokens": int(turn.cache_read_input_tokens or 0),
            "last_turn_cache_creation_input_tokens": int(turn.cache_creation_input_tokens or 0),
        },
    }
    if isinstance(replay_frame, dict):
        state_updates["provider_replay_frame"] = replay_frame
    return HarnessDelta.append(
        created_by=created_by,
        messages=turn.assistant_messages,
        state_updates=state_updates,
        trace={
            "response_id": turn.response_id,
            "assistant_message_count": len(turn.assistant_messages),
            "tool_call_count": len(turn.tool_calls),
            "provider_replay_complete": bool(
                isinstance(replay_frame, dict) and replay_frame.get("complete") is True
            ),
        },
    )


def apply_model_turn_result(
    state: RunState,
    turn: ModelTurnResult,
    *,
    created_by: str = "kernel.model_turn",
) -> RunState:
    state.apply_delta(build_model_turn_delta(state, turn, created_by=created_by))
    return state


__all__ = [
    "apply_model_turn_result",
    "build_model_turn_delta",
    "build_model_turn_request",
    "fetch_model_turn",
]
