from __future__ import annotations

from typing import Any


SESSION_HISTORY_OWNERSHIP_ERROR = (
    "session history ownership conflict: session memory already owns persisted conversation "
    "history; pass only new messages for this turn instead of replaying the stored transcript"
)
PROVIDER_HISTORY_OWNERSHIP_ERROR = (
    "session history ownership conflict: previous_response_id cannot be combined with "
    "session-backed memory on a fresh OpenAI run"
)


class SessionHistoryOwnershipError(ValueError):
    code = "session_history_ownership_conflict"


def _non_system_messages(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        message
        for message in (messages or [])
        if isinstance(message, dict) and message.get("role") != "system"
    ]


def _is_runtime_generated_message(message: dict[str, Any]) -> bool:
    if message.get("role") in {"assistant", "tool"}:
        return True
    if message.get("type") in {
        "function_call",
        "function_call_output",
        "tool_call",
        "tool_result",
    }:
        return True
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") in {"tool_use", "tool_result"}
        for block in content
    )


def _stable_persisted_dialog_prefix(
    history: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    dialog = _non_system_messages(history)
    last_runtime_index = -1
    for index, message in enumerate(dialog):
        if _is_runtime_generated_message(message):
            last_runtime_index = index
    return dialog[: last_runtime_index + 1]


def ensure_session_delta_input(
    *,
    history: list[dict[str, Any]] | None,
    incoming: list[dict[str, Any]] | None,
) -> None:
    """Reject canonical replay of history already owned by session memory."""

    persisted_dialog = _stable_persisted_dialog_prefix(history)
    incoming_dialog = _non_system_messages(incoming)
    if not persisted_dialog or len(incoming_dialog) < len(persisted_dialog):
        return
    if incoming_dialog[: len(persisted_dialog)] == persisted_dialog:
        raise SessionHistoryOwnershipError(SESSION_HISTORY_OWNERSHIP_ERROR)


def ensure_no_external_provider_history(
    *,
    provider: str | None,
    previous_response_id: str | None,
) -> None:
    if str(provider or "").strip().lower() == "openai" and str(
        previous_response_id or ""
    ).strip():
        raise SessionHistoryOwnershipError(PROVIDER_HISTORY_OWNERSHIP_ERROR)


__all__ = [
    "PROVIDER_HISTORY_OWNERSHIP_ERROR",
    "SESSION_HISTORY_OWNERSHIP_ERROR",
    "SessionHistoryOwnershipError",
    "ensure_no_external_provider_history",
    "ensure_session_delta_input",
]
