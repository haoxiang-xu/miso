from __future__ import annotations

import copy
import json
import math
from typing import Any


def _deepcopy_messages(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [copy.deepcopy(message) for message in (messages or []) if isinstance(message, dict)]


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type in ("text", "input_text", "output_text"):
                text = block.get("text", "")
                if text:
                    parts.append(text if isinstance(text, str) else str(text))
                continue
            if block_type in ("image", "input_image"):
                parts.append("[image]")
                continue
            if block_type in ("pdf", "document", "input_file"):
                parts.append("[pdf]")
                continue
            parts.append(json.dumps(block, default=str, ensure_ascii=False))
        return "".join(parts)
    if isinstance(content, dict):
        return json.dumps(content, default=str, ensure_ascii=False)
    if content is None:
        return ""
    return str(content)


def _message_to_text(message: dict[str, Any]) -> str:
    if not isinstance(message, dict):
        return ""
    if "content" in message:
        return _content_to_text(message.get("content"))
    text_parts: list[str] = []
    for key in ("arguments", "output", "result", "text"):
        if key not in message:
            continue
        value = message.get(key)
        if isinstance(value, str):
            text_parts.append(value)
        else:
            text_parts.append(json.dumps(value, default=str, ensure_ascii=False))
    if text_parts:
        return " ".join(text_parts)
    return json.dumps(message, default=str, ensure_ascii=False)


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    total_chars = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if isinstance(role, str):
            total_chars += len(role) + 1
        total_chars += len(_message_to_text(message))
    if total_chars <= 0:
        return 0
    return int(math.ceil(total_chars / 4.0))


def split_system_and_non_system(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    systems: list[dict[str, Any]] = []
    non_system: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "system":
            systems.append(copy.deepcopy(message))
        else:
            non_system.append(copy.deepcopy(message))
    return systems, non_system


def _is_tool_result_like_message(message: dict[str, Any]) -> bool:
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if isinstance(content, list) and content:
        if all(isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
            return True
    parts = message.get("parts")
    if isinstance(parts, list) and parts:
        if all(isinstance(part, dict) and "function_response" in part for part in parts):
            return True
    return False


def split_turns(non_system_messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for message in non_system_messages:
        role = message.get("role") if isinstance(message, dict) else None
        if role == "user" and current and not _is_tool_result_like_message(message):
            turns.append(current)
            current = [copy.deepcopy(message)]
            continue
        current.append(copy.deepcopy(message))
    if current:
        turns.append(current)
    return turns


def latest_user_query(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            text = _message_to_text(message).strip()
            if text:
                return text
    return ""


def replace_non_system_span(
    messages: list[dict[str, Any]],
    replacement_non_system: list[dict[str, Any]],
    *,
    injected_system_messages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    systems, _ = split_system_and_non_system(messages)
    injected = _deepcopy_messages(injected_system_messages)
    replacement = _deepcopy_messages(replacement_non_system)
    return systems + injected + replacement
