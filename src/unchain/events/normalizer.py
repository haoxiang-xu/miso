from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .types import RuntimeEventLinks, Visibility


@dataclass(frozen=True)
class RuntimeEventNormalizerContext:
    session_id: str
    root_run_id: str
    root_agent_id: str = "developer"


@dataclass(frozen=True)
class RuntimeEventDraft:
    type: str
    run_id: str
    agent_id: str
    turn_id: str | None = None
    links: RuntimeEventLinks = field(default_factory=RuntimeEventLinks)
    visibility: Visibility = "user"
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def _str_value(value: Any, fallback: str = "") -> str:
    if isinstance(value, str) and value:
        return value
    return fallback


def _int_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _turn_id(run_id: str, raw: dict[str, Any]) -> str | None:
    iteration = _int_value(raw.get("iteration"))
    if iteration is None:
        return None
    return f"{run_id}:turn-{iteration}"


def _base_run_id(raw: dict[str, Any], context: RuntimeEventNormalizerContext) -> str:
    return _str_value(raw.get("run_id"), context.root_run_id)


def _base_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {"raw_type": _str_value(raw.get("type"), "event")}
    provider = raw.get("provider")
    if isinstance(provider, str) and provider:
        metadata["provider"] = provider
    return metadata


def _status_from_tool_result(result: Any) -> str:
    if isinstance(result, dict):
        if result.get("denied") is True:
            return "denied"
        if result.get("error") is not None:
            return "error"
    return "success"


def _tool_payload(raw: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tool_name": _str_value(raw.get("tool_name"), "tool"),
        "call_id": _str_value(raw.get("call_id")),
    }
    optional_keys = (
        "tool_display_name",
        "toolkit_id",
        "toolkit_name",
        "description",
        "confirmation_id",
        "requires_confirmation",
        "interact_type",
        "interact_config",
        "arguments",
    )
    for key in optional_keys:
        if key in raw:
            payload[key] = copy.deepcopy(raw[key])
    return payload


def _input_request_payload(raw: dict[str, Any]) -> dict[str, Any]:
    selection_mode = _str_value(raw.get("selection_mode"))
    interact_type = _str_value(raw.get("interact_type"), selection_mode)
    if not interact_type:
        kind = _str_value(raw.get("kind"))
        interact_type = "text_input" if kind in {"text", "freeform"} else "confirmation"
    payload: dict[str, Any] = {
        "kind": _str_value(raw.get("kind"), "question"),
        "title": _str_value(raw.get("title")),
        "question": _str_value(raw.get("question")),
        "interact_type": interact_type,
    }
    options = raw.get("options")
    if isinstance(options, list):
        payload["options"] = copy.deepcopy(options)
    for key in (
        "allow_other",
        "other_label",
        "other_placeholder",
        "min_selected",
        "max_selected",
        "interact_config",
    ):
        if key in raw:
            payload[key] = copy.deepcopy(raw[key])
    return payload


def _subagent_payload(raw: dict[str, Any], status: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agent_id": _str_value(raw.get("subagent_id")),
        "parent_id": _str_value(raw.get("parent_id")),
        "mode": _str_value(raw.get("mode")),
        "template": _str_value(raw.get("template")),
        "lineage": copy.deepcopy(raw.get("lineage")) if isinstance(raw.get("lineage"), list) else [],
    }
    batch_id = _str_value(raw.get("batch_id"))
    if batch_id:
        payload["batch_id"] = batch_id
    if status is not None:
        payload["status"] = status
    return payload


def normalize_raw_event(
    raw_event: dict[str, Any],
    *,
    context: RuntimeEventNormalizerContext,
) -> list[RuntimeEventDraft]:
    if not isinstance(raw_event, dict):
        return []
    raw_type = _str_value(raw_event.get("type"))
    if not raw_type:
        return []

    run_id = _base_run_id(raw_event, context)
    agent_id = context.root_agent_id
    turn_id = _turn_id(run_id, raw_event)
    metadata = _base_metadata(raw_event)

    if raw_type == "run_started":
        payload = {
            "status": "running",
            "provider": _str_value(raw_event.get("provider")),
            "model": _str_value(raw_event.get("model")),
        }
        return [
            RuntimeEventDraft(
                type="run.started",
                run_id=run_id,
                agent_id=agent_id,
                turn_id=turn_id,
                payload=payload,
                metadata=metadata,
            )
        ]

    if raw_type == "run_completed":
        payload: dict[str, Any] = {"status": _str_value(raw_event.get("status"), "completed")}
        if isinstance(raw_event.get("bundle"), dict):
            payload["usage"] = copy.deepcopy(raw_event["bundle"])
        return [
            RuntimeEventDraft(
                type="run.completed",
                run_id=run_id,
                agent_id=agent_id,
                turn_id=turn_id,
                payload=payload,
                metadata=metadata,
            )
        ]

    if raw_type == "run_failed":
        payload = {
            "status": "failed",
            "error": {
                "code": _str_value(raw_event.get("code"), "run_failed"),
                "message": _str_value(raw_event.get("message"), "Run failed"),
            },
            "recoverable": bool(raw_event.get("recoverable", False)),
        }
        return [
            RuntimeEventDraft(
                type="run.failed",
                run_id=run_id,
                agent_id=agent_id,
                turn_id=turn_id,
                payload=payload,
                metadata=metadata,
            )
        ]

    if raw_type == "iteration_started":
        return [
            RuntimeEventDraft(
                type="turn.started",
                run_id=run_id,
                agent_id=agent_id,
                turn_id=turn_id,
                visibility="debug",
                payload={"iteration": raw_event.get("iteration")},
                metadata=metadata,
            )
        ]

    if raw_type == "iteration_completed":
        return [
            RuntimeEventDraft(
                type="turn.completed",
                run_id=run_id,
                agent_id=agent_id,
                turn_id=turn_id,
                visibility="debug",
                payload={
                    "iteration": raw_event.get("iteration"),
                    "has_tool_calls": bool(raw_event.get("has_tool_calls", False)),
                },
                metadata=metadata,
            )
        ]

    if raw_type == "request_messages":
        payload = {
            "provider": _str_value(raw_event.get("provider")),
            "model": _str_value(raw_event.get("model")),
            "messages": copy.deepcopy(raw_event.get("messages")) if isinstance(raw_event.get("messages"), list) else [],
            "tool_names": copy.deepcopy(raw_event.get("tool_names")) if isinstance(raw_event.get("tool_names"), list) else [],
        }
        previous_response_id = _str_value(raw_event.get("previous_response_id"))
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
        return [
            RuntimeEventDraft(
                type="model.started",
                run_id=run_id,
                agent_id=agent_id,
                turn_id=turn_id,
                visibility="debug",
                payload=payload,
                metadata=metadata,
            )
        ]

    if raw_type == "token_delta":
        payload = {
            "kind": "text",
            "delta": _str_value(raw_event.get("delta")),
        }
        accumulated_text = raw_event.get("accumulated_text")
        if isinstance(accumulated_text, str):
            payload["accumulated_text"] = accumulated_text
        return [
            RuntimeEventDraft(
                type="model.delta",
                run_id=run_id,
                agent_id=agent_id,
                turn_id=turn_id,
                payload=payload,
                metadata=metadata,
            )
        ]

    if raw_type == "reasoning":
        delta = _str_value(raw_event.get("delta"), _str_value(raw_event.get("content")))
        return [
            RuntimeEventDraft(
                type="model.delta",
                run_id=run_id,
                agent_id=agent_id,
                turn_id=turn_id,
                payload={"kind": "reasoning", "delta": delta},
                metadata=metadata,
            )
        ]

    if raw_type == "response_received":
        payload = {
            "response_id": _str_value(raw_event.get("response_id")),
            "has_tool_calls": bool(raw_event.get("has_tool_calls", False)),
            "status": _str_value(raw_event.get("status")),
        }
        if isinstance(raw_event.get("bundle"), dict):
            payload["usage"] = copy.deepcopy(raw_event["bundle"])
        return [
            RuntimeEventDraft(
                type="model.completed",
                run_id=run_id,
                agent_id=agent_id,
                turn_id=turn_id,
                visibility="debug",
                payload=payload,
                metadata=metadata,
            )
        ]

    if raw_type == "final_message":
        content = _str_value(raw_event.get("content"))
        return [
            RuntimeEventDraft(
                type="model.completed",
                run_id=run_id,
                agent_id=agent_id,
                turn_id=turn_id,
                payload={"status": "completed", "final_text": content},
                metadata=metadata,
            )
        ]

    if raw_type == "tool_call":
        call_id = _str_value(raw_event.get("call_id"))
        return [
            RuntimeEventDraft(
                type="tool.started",
                run_id=run_id,
                agent_id=agent_id,
                turn_id=turn_id,
                links=RuntimeEventLinks(tool_call_id=call_id or None),
                payload=_tool_payload(raw_event),
                metadata=metadata,
            )
        ]

    if raw_type == "tool_result":
        call_id = _str_value(raw_event.get("call_id"))
        result = copy.deepcopy(raw_event.get("result"))
        payload = {
            "tool_name": _str_value(raw_event.get("tool_name"), "tool"),
            "call_id": call_id,
            "status": _status_from_tool_result(result),
            "result": result,
        }
        display_name = _str_value(raw_event.get("tool_display_name"))
        if display_name:
            payload["tool_display_name"] = display_name
        return [
            RuntimeEventDraft(
                type="tool.completed",
                run_id=run_id,
                agent_id=agent_id,
                turn_id=turn_id,
                links=RuntimeEventLinks(tool_call_id=call_id or None),
                payload=payload,
                metadata=metadata,
            )
        ]

    if raw_type == "human_input_requested":
        request_id = _str_value(raw_event.get("request_id"))
        return [
            RuntimeEventDraft(
                type="input.requested",
                run_id=run_id,
                agent_id=agent_id,
                turn_id=turn_id,
                links=RuntimeEventLinks(input_request_id=request_id or None),
                payload=_input_request_payload(raw_event),
                metadata=metadata,
            )
        ]

    if raw_type in {"tool_confirmation_requested", "input_requested", "continuation_request"}:
        request_id = _str_value(
            raw_event.get("confirmation_id"),
            _str_value(raw_event.get("request_id")),
        )
        call_id = _str_value(raw_event.get("call_id"))
        payload = _input_request_payload(raw_event)
        if raw_type == "continuation_request":
            payload["kind"] = "continue"
            payload["interact_type"] = "confirmation"
        return [
            RuntimeEventDraft(
                type="input.requested",
                run_id=run_id,
                agent_id=agent_id,
                turn_id=turn_id,
                links=RuntimeEventLinks(
                    tool_call_id=call_id or None,
                    input_request_id=request_id or None,
                ),
                payload=payload,
                metadata=metadata,
            )
        ]

    if raw_type in {"tool_confirmed", "tool_denied"}:
        call_id = _str_value(raw_event.get("call_id"))
        request_id = _str_value(
            raw_event.get("confirmation_id"),
            _str_value(raw_event.get("request_id"), call_id),
        )
        response = copy.deepcopy(raw_event.get("user_response"))
        if response is None and "response" in raw_event:
            response = copy.deepcopy(raw_event.get("response"))
        payload = {
            "decision": "approved" if raw_type == "tool_confirmed" else "denied",
            "response": response,
        }
        reason = _str_value(raw_event.get("reason"))
        if reason:
            payload["reason"] = reason
        return [
            RuntimeEventDraft(
                type="input.resolved",
                run_id=run_id,
                agent_id=agent_id,
                turn_id=turn_id,
                links=RuntimeEventLinks(
                    tool_call_id=call_id or None,
                    input_request_id=request_id or None,
                ),
                payload=payload,
                metadata=metadata,
            )
        ]

    if raw_type in {"subagent_started", "subagent_completed", "subagent_failed"}:
        child_run_id = _str_value(raw_event.get("child_run_id"))
        subagent_id = _str_value(raw_event.get("subagent_id"), child_run_id or agent_id)
        parent_run_id = _str_value(raw_event.get("root_run_id"), context.root_run_id)
        event_type = {
            "subagent_started": "run.started",
            "subagent_completed": "run.completed",
            "subagent_failed": "run.failed",
        }[raw_type]
        status = None
        if raw_type == "subagent_completed":
            status = _str_value(raw_event.get("status"), "completed")
        elif raw_type == "subagent_failed":
            status = _str_value(raw_event.get("status"), "failed")
        payload = _subagent_payload(raw_event, status=status)
        if raw_type == "subagent_failed":
            payload["error"] = {
                "code": _str_value(raw_event.get("code"), "subagent_failed"),
                "message": _str_value(raw_event.get("message"), "Subagent failed"),
            }
        return [
            RuntimeEventDraft(
                type=event_type,
                run_id=child_run_id,
                agent_id=subagent_id,
                links=RuntimeEventLinks(parent_run_id=parent_run_id or None),
                payload=payload,
                metadata=metadata,
            )
        ]

    return []
