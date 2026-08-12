from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class ProviderMessageContractError(ValueError):
    """A provider message contains fields outside its exact wire contract."""

    code = "provider_message_schema_invalid"

    def __init__(self, provider: str, path: str, detail: str) -> None:
        self.provider = str(provider or "").strip().casefold()
        self.path = str(path or "messages").strip()
        self.detail = str(detail or "provider message is invalid").strip()
        super().__init__(f"{self.code}: {self.provider} {self.path}: {self.detail}")


_OPENAI_ITEM_KEYS: dict[str, frozenset[str]] = {
    "message": frozenset({"content", "id", "phase", "role", "status", "type"}),
    "function_call": frozenset(
        {"arguments", "call_id", "id", "name", "namespace", "status", "type"}
    ),
    "function_call_output": frozenset({"call_id", "id", "output", "status", "type"}),
    "computer_call": frozenset(
        {"action", "actions", "call_id", "id", "pending_safety_checks", "status", "type"}
    ),
    "computer_call_output": frozenset(
        {"acknowledged_safety_checks", "call_id", "id", "output", "status", "type"}
    ),
    "reasoning": frozenset(
        {"content", "encrypted_content", "id", "status", "summary", "type"}
    ),
    "compaction": frozenset({"encrypted_content", "id", "type"}),
    "file_search_call": frozenset({"id", "queries", "results", "status", "type"}),
    "web_search_call": frozenset({"action", "id", "status", "type"}),
    "code_interpreter_call": frozenset(
        {"code", "container_id", "id", "outputs", "status", "type"}
    ),
    "image_generation_call": frozenset({"id", "result", "status", "type"}),
    "local_shell_call": frozenset({"action", "call_id", "id", "status", "type"}),
    "local_shell_call_output": frozenset({"id", "output", "status", "type"}),
    "shell_call": frozenset(
        {"action", "call_id", "environment", "id", "status", "type"}
    ),
    "shell_call_output": frozenset(
        {"call_id", "id", "max_output_length", "output", "status", "type"}
    ),
    "apply_patch_call": frozenset({"call_id", "id", "operation", "status", "type"}),
    "apply_patch_call_output": frozenset(
        {"call_id", "id", "output", "status", "type"}
    ),
    "mcp_list_tools": frozenset({"error", "id", "server_label", "tools", "type"}),
    "mcp_approval_request": frozenset(
        {"arguments", "id", "name", "server_label", "type"}
    ),
    "mcp_approval_response": frozenset(
        {"approval_request_id", "approve", "id", "reason", "type"}
    ),
    "mcp_call": frozenset(
        {
            "approval_request_id",
            "arguments",
            "error",
            "id",
            "name",
            "output",
            "server_label",
            "status",
            "type",
        }
    ),
    "custom_tool_call": frozenset({"call_id", "id", "input", "name", "namespace", "type"}),
    "custom_tool_call_output": frozenset({"call_id", "id", "output", "type"}),
    "tool_search_call": frozenset(
        {"arguments", "call_id", "execution", "id", "status", "type"}
    ),
    "tool_search_output": frozenset(
        {"call_id", "execution", "id", "status", "tools", "type"}
    ),
    "item_reference": frozenset({"id", "type"}),
}
_OPENAI_INPUT_CONTENT_KEYS: dict[str, frozenset[str]] = {
    "input_text": frozenset({"text", "type"}),
    "input_image": frozenset({"detail", "file_id", "image_url", "type"}),
    "input_file": frozenset({"file_data", "file_id", "file_url", "filename", "type"}),
}
_OPENAI_ASSISTANT_CONTENT_KEYS: dict[str, frozenset[str]] = {
    **_OPENAI_INPUT_CONTENT_KEYS,
    "output_text": frozenset({"annotations", "logprobs", "text", "type"}),
    "refusal": frozenset({"refusal", "type"}),
}
_ANTHROPIC_MESSAGE_KEYS = frozenset({"content", "role"})
_ANTHROPIC_CONTENT_KEYS: dict[str, frozenset[str]] = {
    "text": frozenset({"cache_control", "citations", "text", "type"}),
    "image": frozenset({"cache_control", "source", "type"}),
    "document": frozenset(
        {"cache_control", "citations", "context", "source", "title", "type"}
    ),
    "search_result": frozenset(
        {"cache_control", "citations", "content", "source", "title", "type"}
    ),
    "thinking": frozenset({"signature", "thinking", "type"}),
    "redacted_thinking": frozenset({"data", "type"}),
    "tool_use": frozenset({"cache_control", "caller", "id", "input", "name", "type"}),
    "tool_result": frozenset(
        {"cache_control", "content", "is_error", "tool_use_id", "type"}
    ),
    "server_tool_use": frozenset(
        {"cache_control", "caller", "id", "input", "name", "type"}
    ),
    "web_search_tool_result": frozenset(
        {"cache_control", "caller", "content", "tool_use_id", "type"}
    ),
    "web_fetch_tool_result": frozenset(
        {"cache_control", "caller", "content", "tool_use_id", "type"}
    ),
    "code_execution_tool_result": frozenset(
        {"cache_control", "content", "tool_use_id", "type"}
    ),
    "bash_code_execution_tool_result": frozenset(
        {"cache_control", "content", "tool_use_id", "type"}
    ),
    "text_editor_code_execution_tool_result": frozenset(
        {"cache_control", "content", "tool_use_id", "type"}
    ),
    "tool_search_tool_result": frozenset(
        {"cache_control", "content", "tool_use_id", "type"}
    ),
    "container_upload": frozenset({"cache_control", "file_id", "type"}),
}
_OLLAMA_MESSAGE_KEYS = frozenset({"content", "images", "role", "thinking", "tool_call_id", "tool_calls"})


def _unknown_keys(value: Mapping[str, Any], allowed: frozenset[str]) -> tuple[str, ...]:
    return tuple(sorted(str(key) for key in value if key not in allowed))


def _validate_openai_content(
    content: Any,
    *,
    path: str,
    assistant: bool = False,
) -> None:
    if isinstance(content, str):
        return
    if not isinstance(content, Sequence) or isinstance(content, (str, bytes, bytearray)):
        raise ProviderMessageContractError("openai", path, "content must be text or an array")
    for index, block in enumerate(content):
        block_path = f"{path}[{index}]"
        if not isinstance(block, Mapping):
            raise ProviderMessageContractError("openai", block_path, "content block must be an object")
        block_type = block.get("type")
        allowed = (
            _OPENAI_ASSISTANT_CONTENT_KEYS
            if assistant
            else _OPENAI_INPUT_CONTENT_KEYS
        ).get(str(block_type or ""))
        if allowed is None:
            raise ProviderMessageContractError(
                "openai",
                block_path,
                f"unsupported input content type: {block_type or '<missing>'}",
            )
        unknown = _unknown_keys(block, allowed)
        if unknown:
            raise ProviderMessageContractError(
                "openai", block_path, f"unknown fields: {', '.join(unknown)}"
            )


def validate_openai_input_items(items: Any, *, field_name: str = "input") -> None:
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        raise ProviderMessageContractError("openai", field_name, "must be an array")
    for index, item in enumerate(items):
        path = f"{field_name}[{index}]"
        if not isinstance(item, Mapping):
            raise ProviderMessageContractError("openai", path, "item must be an object")
        item_type = str(item.get("type") or "")
        role = item.get("role")
        if not item_type and isinstance(role, str):
            item_type = "message"
        allowed = _OPENAI_ITEM_KEYS.get(item_type)
        if allowed is None:
            raise ProviderMessageContractError(
                "openai", path, f"unsupported item type: {item_type or '<missing>'}"
            )
        unknown = _unknown_keys(item, allowed)
        if unknown:
            raise ProviderMessageContractError(
                "openai", path, f"unknown fields: {', '.join(unknown)}"
            )
        if item_type == "message":
            if not isinstance(role, str) or role not in {"user", "assistant", "system", "developer"}:
                raise ProviderMessageContractError("openai", path, "message role is invalid")
            if "content" not in item:
                raise ProviderMessageContractError("openai", path, "message content is required")
            _validate_openai_content(
                item["content"],
                path=f"{path}.content",
                assistant=role == "assistant",
            )


def validate_anthropic_messages(messages: Any, *, field_name: str = "messages") -> None:
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes, bytearray)):
        raise ProviderMessageContractError("anthropic", field_name, "must be an array")
    for index, message in enumerate(messages):
        path = f"{field_name}[{index}]"
        if not isinstance(message, Mapping):
            raise ProviderMessageContractError("anthropic", path, "message must be an object")
        unknown = _unknown_keys(message, _ANTHROPIC_MESSAGE_KEYS)
        if unknown:
            raise ProviderMessageContractError(
                "anthropic", path, f"unknown fields: {', '.join(unknown)}"
            )
        if message.get("role") not in {"user", "assistant"}:
            raise ProviderMessageContractError("anthropic", path, "message role is invalid")
        if "content" not in message:
            raise ProviderMessageContractError("anthropic", path, "message content is required")
        content = message["content"]
        if isinstance(content, str):
            continue
        if not isinstance(content, Sequence) or isinstance(
            content, (str, bytes, bytearray)
        ):
            raise ProviderMessageContractError(
                "anthropic", f"{path}.content", "must be text or an array"
            )
        for block_index, block in enumerate(content):
            block_path = f"{path}.content[{block_index}]"
            if not isinstance(block, Mapping):
                raise ProviderMessageContractError(
                    "anthropic", block_path, "content block must be an object"
                )
            block_type = str(block.get("type") or "")
            allowed = _ANTHROPIC_CONTENT_KEYS.get(block_type)
            if allowed is None:
                raise ProviderMessageContractError(
                    "anthropic",
                    block_path,
                    f"unsupported content block type: {block_type or '<missing>'}",
                )
            unknown = _unknown_keys(block, allowed)
            if unknown:
                raise ProviderMessageContractError(
                    "anthropic", block_path, f"unknown fields: {', '.join(unknown)}"
                )


def validate_ollama_messages(messages: Any, *, field_name: str = "messages") -> None:
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes, bytearray)):
        raise ProviderMessageContractError("ollama", field_name, "must be an array")
    for index, message in enumerate(messages):
        path = f"{field_name}[{index}]"
        if not isinstance(message, Mapping):
            raise ProviderMessageContractError("ollama", path, "message must be an object")
        unknown = _unknown_keys(message, _OLLAMA_MESSAGE_KEYS)
        if unknown:
            raise ProviderMessageContractError(
                "ollama", path, f"unknown fields: {', '.join(unknown)}"
            )
        if message.get("role") not in {"system", "user", "assistant", "tool"}:
            raise ProviderMessageContractError("ollama", path, "message role is invalid")
        if "content" not in message:
            raise ProviderMessageContractError("ollama", path, "message content is required")
        if not isinstance(message["content"], str):
            raise ProviderMessageContractError(
                "ollama", f"{path}.content", "must be text after media projection"
            )


__all__ = [
    "ProviderMessageContractError",
    "validate_anthropic_messages",
    "validate_ollama_messages",
    "validate_openai_input_items",
]
