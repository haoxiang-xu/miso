from __future__ import annotations

import copy
from typing import Any

from .provider_replay import current_provider_replay_frame
from .replay_handle import store_provider_replay_handle
from .state import RunState
from .types import KernelRunResult
from ..interaction.durable import InteractionRequest


def _human_input_request_payload(state: RunState) -> dict[str, Any] | None:
    request = state.tool_batch_state.human_input_request
    return request.to_dict() if request is not None else None


def _continuation_payload(state: RunState) -> dict[str, Any] | None:
    return copy.deepcopy(state.last_continuation) if isinstance(state.last_continuation, dict) else None


def _interaction_request_payload(state: RunState) -> dict[str, Any] | None:
    raw = state.suspend_state.payload.get("interaction_request")
    if not isinstance(raw, dict):
        return None
    return InteractionRequest.from_dict(raw).to_dict()


def build_kernel_run_result(state: RunState, *, status: str) -> KernelRunResult:
    return KernelRunResult(
        messages=copy.deepcopy(state.transcript),
        status=status,
        continuation=_continuation_payload(state),
        human_input_request=_human_input_request_payload(state),
        interaction_request=_interaction_request_payload(state),
        consumed_tokens=int(state.token_state.consumed_tokens or 0),
        input_tokens=int(state.token_state.input_tokens or 0),
        output_tokens=int(state.token_state.output_tokens or 0),
        last_turn_tokens=int(state.token_state.last_turn_tokens or 0),
        last_turn_input_tokens=int(state.token_state.last_turn_input_tokens or 0),
        last_turn_output_tokens=int(state.token_state.last_turn_output_tokens or 0),
        cache_read_input_tokens=int(state.token_state.cache_read_input_tokens or 0),
        cache_creation_input_tokens=int(state.token_state.cache_creation_input_tokens or 0),
        previous_response_id=state.provider_state.previous_response_id,
        iteration=int(state.iteration),
        provider_replay_handle=store_provider_replay_handle(
            current_provider_replay_frame(state)
        ),
    )


def build_legacy_run_bundle(state: RunState, *, status: str) -> dict[str, Any]:
    max_context_window_tokens = max(0, int(state.provider_state.max_context_window_tokens or 0))
    last_turn_tokens = int(state.token_state.last_turn_tokens or 0)
    context_window_used_pct = (
        (last_turn_tokens / max_context_window_tokens * 100.0)
        if max_context_window_tokens > 0
        else 0.0
    )
    bundle = {
        "model": state.provider_state.model,
        "consumed_tokens": int(state.token_state.consumed_tokens or 0),
        "input_tokens": int(state.token_state.input_tokens or 0),
        "output_tokens": int(state.token_state.output_tokens or 0),
        "last_turn_tokens": last_turn_tokens,
        "last_turn_input_tokens": int(state.token_state.last_turn_input_tokens or 0),
        "last_turn_output_tokens": int(state.token_state.last_turn_output_tokens or 0),
        "max_context_window_tokens": max_context_window_tokens,
        "context_window_used_pct": round(context_window_used_pct, 2),
        "status": status,
        "human_input_request": _human_input_request_payload(state),
        "continuation": _continuation_payload(state),
        "previous_response_id": state.provider_state.previous_response_id,
        "iteration": int(state.iteration),
    }
    interaction_request = _interaction_request_payload(state)
    if interaction_request is not None:
        bundle["interaction_request"] = interaction_request
    return bundle


__all__ = [
    "build_kernel_run_result",
    "build_legacy_run_bundle",
]
