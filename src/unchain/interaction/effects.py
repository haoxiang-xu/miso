from __future__ import annotations

import copy
from typing import Any

from ..capabilities import EmitEventOp, RequestSuspendOp
from ..kernel.provider_replay import current_provider_replay_frame
from ..kernel.replay_handle import store_provider_replay_handle
from .human_input import HumanInputRequest


INTERACTION_EFFECT_CREATED_BY = "interaction.human_input"


def build_human_input_requested_event(request: HumanInputRequest) -> EmitEventOp:
    return EmitEventOp(
        type="human_input_requested",
        payload={
            "request_id": request.request_id,
            "kind": request.kind,
            "title": request.title,
            "question": request.question,
            "selection_mode": request.selection_mode,
            "options": [option.to_dict() for option in request.options],
            "allow_other": request.allow_other,
            "other_label": request.other_label,
            "other_placeholder": request.other_placeholder,
            "min_selected": request.min_selected,
            "max_selected": request.max_selected,
        },
        reason="interaction.requested",
    )


def build_human_input_suspend_request(
    request: HumanInputRequest,
    *,
    continuation: dict[str, Any],
) -> RequestSuspendOp:
    return RequestSuspendOp(
        kind="human_input",
        payload={
            "continuation": copy.deepcopy(continuation),
            "request": request.to_dict(),
        },
        reason="interaction.awaiting_human_input",
    )


def _ensure_json_safe(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {
            key: _ensure_json_safe(value)
            for key, value in obj.items()
            if isinstance(key, str)
            and isinstance(value, (str, int, float, bool, type(None), dict, list, tuple))
        }
    if isinstance(obj, (list, tuple)):
        return [
            _ensure_json_safe(item)
            for item in obj
            if isinstance(item, (str, int, float, bool, type(None), dict, list, tuple))
        ]
    return str(obj)


def _serialize_response_format(response_format: Any) -> dict[str, Any] | None:
    if response_format is None:
        return None
    name = getattr(response_format, "name", None)
    schema = getattr(response_format, "schema", None)
    required = getattr(response_format, "required", None)
    if not isinstance(name, str) or not isinstance(schema, dict):
        return None
    return {
        "name": name,
        "schema": copy.deepcopy(schema),
        "required": list(required) if isinstance(required, list) else [],
    }


def build_human_input_continuation(
    *,
    request: HumanInputRequest,
    payload: dict[str, Any],
    response_format: Any,
    next_iteration: int,
    max_iterations: int,
    state: Any,
    run_id: str | None = None,
) -> dict[str, Any]:
    replay_handle = store_provider_replay_handle(
        current_provider_replay_frame(state)
    )
    return {
        "type": "human_input_continuation",
        "kind": getattr(request, "kind", None),
        "run_id": run_id,
        "provider": state.provider_state.provider,
        "model": state.provider_state.model,
        "request_id": getattr(request, "request_id", None),
        "call_id": getattr(request, "request_id", None),
        "request": request.to_dict(),
        "payload": _ensure_json_safe(copy.deepcopy(payload)),
        "response_format": _ensure_json_safe(_serialize_response_format(response_format)),
        "context_version_id": state.latest_version_id,
        "iteration": int(next_iteration),
        "max_iterations": int(max_iterations),
        "previous_response_id": state.provider_state.previous_response_id,
        "use_openai_previous_response_chain": bool(state.provider_state.use_previous_response_chain),
        "provider_replay_handle": replay_handle,
        "session_id": state.session_state.session_id,
        "memory_namespace": state.session_state.memory_namespace,
        "max_context_window_tokens": int(state.provider_state.max_context_window_tokens or 0),
        "consumed_tokens": int(state.token_state.consumed_tokens or 0),
        "input_tokens": int(state.token_state.input_tokens or 0),
        "output_tokens": int(state.token_state.output_tokens or 0),
        "last_turn_tokens": int(state.token_state.last_turn_tokens or 0),
        "last_turn_input_tokens": int(state.token_state.last_turn_input_tokens or 0),
        "last_turn_output_tokens": int(state.token_state.last_turn_output_tokens or 0),
        "workspace_change_state": copy.deepcopy(state.workspace_change_state),
    }


__all__ = [
    "INTERACTION_EFFECT_CREATED_BY",
    "build_human_input_continuation",
    "build_human_input_requested_event",
    "build_human_input_suspend_request",
]
