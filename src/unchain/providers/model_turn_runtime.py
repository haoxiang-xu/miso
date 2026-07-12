from __future__ import annotations

import copy
from typing import Any

from ..kernel.delta import HarnessDelta
from ..kernel.provider_replay import (
    current_provider_replay_frame,
    ensure_replay_tool_schema_compatible,
    merge_model_turn_replay_frame,
)
from ..kernel.state import RunState
from ..kernel.types import ModelTurnResult
from ..retry import RetryConfig, RetryContext, fetch_turn_with_retry
from ..tools.toolkit import Toolkit
from .base import ModelIO, ModelTurnRequest


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
    replay_frame = current_provider_replay_frame(state)
    replay_formats = {
        "openai": "openai.responses.v1",
        "anthropic": "anthropic.messages.v1",
        "hyperspace": "anthropic.messages.v1",
        "ollama": "ollama.chat.v1",
    }
    provider = str(state.provider_state.provider or "")
    if (
        provider in replay_formats
        and not state.provider_state.use_previous_response_chain
        and isinstance(replay_frame, dict)
        and replay_frame.get("complete") is True
        and replay_frame.get("format") == replay_formats[provider]
    ):
        ensure_replay_tool_schema_compatible(
            replay_frame,
            toolkit=resolved_toolkit,
            provider=provider,
        )
        request_messages = copy.deepcopy(replay_frame.get("items") or [])
    else:
        request_messages = (
            copy.deepcopy(state.next_model_input)
            if isinstance(state.next_model_input, list)
            else state.latest_messages()
        )
    return ModelTurnRequest(
        messages=request_messages,
        payload=dict(payload or {}),
        response_format=response_format,
        callback=callback,
        verbose=verbose,
        run_id=run_id,
        iteration=state.iteration,
        toolkit=resolved_toolkit,
        emit_stream=emit_stream,
        previous_response_id=(
            state.provider_state.previous_response_id
            if state.provider_state.use_previous_response_chain
            else None
        ),
        openai_text_format=openai_text_format,
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
) -> ModelTurnResult:
    if model_io is None:
        raise RuntimeError("KernelLoop.model_io is not configured")
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
    context = RetryContext(
        run_id=run_id,
        iteration=state.iteration,
        is_background=(run_id == "observe"),
    )
    return fetch_turn_with_retry(
        model_io=model_io,
        request=request,
        config=retry_config,
        context=context,
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
