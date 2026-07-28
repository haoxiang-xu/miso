"""Builders that bind durable interaction requests to live runtime schemas."""

from __future__ import annotations

import copy
from typing import Any

from ..input.human_input import HumanInputRequest
from ..kernel.state import RunState
from ..kernel.types import ToolCall
from ..tools.models import ToolConfirmationRequest
from ..tools.toolkit import Toolkit
from .durable import (
    INTERACTION_KIND_HUMAN_INPUT,
    INTERACTION_KIND_MAX_BUDGET,
    INTERACTION_KIND_TOOL_APPROVAL,
    InteractionIntegrityError,
    InteractionRequest,
    build_interaction_request,
    strict_json_digest,
)
from .runtime import response_contract_for_kind


def _created_revision(state: RunState) -> int:
    raw = state.memory_state.get("session_revision")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return 0
    return raw


def _prompt_manifest(state: RunState) -> list[dict[str, Any]]:
    return [
        {
            "role": message.get("role"),
            "content": copy.deepcopy(message.get("content")),
        }
        for message in state.transcript
        if isinstance(message, dict)
        and message.get("role") in {"system", "developer"}
    ]


def build_runtime_schema_subject(
    state: RunState,
    *,
    toolkit: Toolkit | None = None,
    tool_name: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    subject: dict[str, Any] = {
        "binding_version": 1,
        "provider": str(state.provider_state.provider or ""),
        "model": str(state.provider_state.model or ""),
        "prompt_digest": strict_json_digest(_prompt_manifest(state)),
    }
    if toolkit is not None and isinstance(tool_name, str) and tool_name:
        tool_obj = toolkit.get(tool_name)
        tool_schema = (
            tool_obj.to_provider_json(state.provider_state.provider)
            if tool_obj is not None
            else {"missing_tool": tool_name}
        )
        subject.update(
            {
                "tool_name": tool_name,
                "tool_schema_digest": strict_json_digest(tool_schema),
            }
        )
    if isinstance(extra, dict):
        subject["extra"] = copy.deepcopy(extra)
    return subject


def build_human_interaction_request(
    *,
    state: RunState,
    request: HumanInputRequest,
    run_id: str,
) -> InteractionRequest:
    return build_interaction_request(
        session_id=str(state.session_state.session_id or ""),
        kind=INTERACTION_KIND_HUMAN_INPUT,
        source_run_id=str(run_id or "kernel"),
        occurrence=f"human:{request.request_id}:{int(state.iteration)}",
        payload=request.to_dict(),
        response_contract=response_contract_for_kind(
            INTERACTION_KIND_HUMAN_INPUT
        ),
        created_revision=_created_revision(state),
        subject=build_runtime_schema_subject(state),
    )


def build_tool_approval_interaction_request(
    *,
    state: RunState,
    toolkit: Toolkit,
    tool_call: ToolCall,
    confirmation_request: ToolConfirmationRequest,
    run_id: str,
    source_request: InteractionRequest | dict[str, Any] | None = None,
    extra_subject: dict[str, Any] | None = None,
) -> InteractionRequest:
    stored = (
        InteractionRequest.from_dict(source_request)
        if source_request is not None
        else None
    )
    return build_interaction_request(
        session_id=(
            stored.session_id
            if stored is not None
            else str(state.session_state.session_id or "")
        ),
        kind=INTERACTION_KIND_TOOL_APPROVAL,
        source_run_id=(stored.source_run_id if stored is not None else str(run_id or "kernel")),
        occurrence=(
            stored.occurrence
            if stored is not None
            else f"tool:{tool_call.call_id}:{int(state.iteration)}"
        ),
        payload=confirmation_request.to_dict(),
        response_contract=response_contract_for_kind(
            INTERACTION_KIND_TOOL_APPROVAL
        ),
        created_revision=(
            stored.created_revision if stored is not None else _created_revision(state)
        ),
        subject=build_runtime_schema_subject(
            state,
            toolkit=toolkit,
            tool_name=tool_call.name,
            extra=extra_subject,
        ),
    )


def build_max_budget_interaction_request(
    *,
    state: RunState,
    run_id: str,
    effective_max: int,
    decision_payload: dict[str, Any],
) -> InteractionRequest:
    current_max = max(1, int(effective_max))
    return build_interaction_request(
        session_id=str(state.session_state.session_id or ""),
        kind=INTERACTION_KIND_MAX_BUDGET,
        source_run_id=str(run_id or "kernel"),
        occurrence=f"max:{int(state.iteration)}:{current_max}",
        payload={
            "decision": copy.deepcopy(decision_payload),
            "effective_max": current_max,
            "suggested_extra_iterations": current_max,
        },
        response_contract=response_contract_for_kind(
            INTERACTION_KIND_MAX_BUDGET
        ),
        created_revision=_created_revision(state),
        subject=build_runtime_schema_subject(state),
    )


def ensure_interaction_binding_matches(
    stored: InteractionRequest | dict[str, Any],
    current: InteractionRequest | dict[str, Any],
) -> None:
    stored_request = InteractionRequest.from_dict(stored)
    current_request = InteractionRequest.from_dict(current)
    if (
        stored_request.interaction_id != current_request.interaction_id
        or stored_request.request_digest != current_request.request_digest
    ):
        raise InteractionIntegrityError(
            "pending interaction no longer matches the current prompt/tool schema"
        )


def ensure_interaction_runtime_matches(
    stored: InteractionRequest | dict[str, Any],
    *,
    provider: str | None,
    model: str | None,
    checkpoint: dict[str, Any] | None = None,
    continuation: dict[str, Any] | None = None,
) -> None:
    request = InteractionRequest.from_dict(stored)
    subject = request.subject if isinstance(request.subject, dict) else {}
    expected_provider = str(subject.get("provider") or "")
    expected_model = str(subject.get("model") or "")
    actual_provider = str(provider or "")
    actual_model = str(model or "")
    bindings = [(actual_provider, actual_model)]
    if isinstance(checkpoint, dict):
        bindings.append(
            (
                str(checkpoint.get("provider") or ""),
                str(checkpoint.get("model") or ""),
            )
        )
    if isinstance(continuation, dict):
        bindings.append(
            (
                str(continuation.get("provider") or ""),
                str(continuation.get("model") or ""),
            )
        )
    if any(
        bound_provider != expected_provider or bound_model != expected_model
        for bound_provider, bound_model in bindings
    ):
        raise InteractionIntegrityError(
            "active runtime provider/model does not match the durable interaction"
        )


__all__ = [
    "build_human_interaction_request",
    "build_max_budget_interaction_request",
    "build_runtime_schema_subject",
    "build_tool_approval_interaction_request",
    "ensure_interaction_binding_matches",
    "ensure_interaction_runtime_matches",
]
