from __future__ import annotations

import copy
import json
from typing import Protocol, runtime_checkable

from ..kernel.types import ToolCall


@runtime_checkable
class ProviderMessageBuilder(Protocol):
    provider: str

    def build_tool_result_message(
        self,
        *,
        tool_call: ToolCall,
        tool_result: dict,
    ) -> dict:
        ...


class OpenAIMessageBuilder:
    provider = "openai"

    def build_tool_result_message(
        self,
        *,
        tool_call: ToolCall,
        tool_result: dict,
    ) -> dict:
        return {
            "type": "function_call_output",
            "call_id": tool_call.call_id,
            "output": json.dumps(tool_result, default=str, ensure_ascii=False),
        }


class AnthropicMessageBuilder:
    provider = "anthropic"

    def build_tool_result_message(
        self,
        *,
        tool_call: ToolCall,
        tool_result: dict,
    ) -> dict:
        return {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_call.call_id,
                "content": json.dumps(tool_result, default=str, ensure_ascii=False),
            }],
        }


class HyperspaceMessageBuilder(AnthropicMessageBuilder):
    """SAP Hyperspace uses the Anthropic Messages API wire format."""

    provider = "hyperspace"


class GeminiMessageBuilder:
    provider = "gemini"

    def build_tool_result_message(
        self,
        *,
        tool_call: ToolCall,
        tool_result: dict,
    ) -> dict:
        return {
            "role": "user",
            "parts": [{
                "function_response": {
                    "name": tool_call.name,
                    "response": dict(tool_result),
                },
            }],
        }


class OllamaMessageBuilder:
    provider = "ollama"

    def build_tool_result_message(
        self,
        *,
        tool_call: ToolCall,
        tool_result: dict,
    ) -> dict:
        return {
            "role": "tool",
            "tool_call_id": tool_call.call_id,
            "content": json.dumps(tool_result, default=str, ensure_ascii=False),
        }


def get_provider_message_builder(provider: str) -> ProviderMessageBuilder:
    normalized = str(provider or "").strip().lower()
    if normalized == "openai":
        return OpenAIMessageBuilder()
    if normalized == "anthropic":
        return AnthropicMessageBuilder()
    if normalized == "hyperspace":
        return HyperspaceMessageBuilder()
    if normalized == "gemini":
        return GeminiMessageBuilder()
    if normalized == "ollama":
        return OllamaMessageBuilder()
    raise NotImplementedError(f"provider message builder is not implemented for provider={provider!r}")


def coalesce_provider_tool_result_messages(
    provider: str,
    messages: list[dict],
) -> list[dict]:
    if str(provider or "").strip().lower() not in {"anthropic", "hyperspace"}:
        return copy.deepcopy(messages)

    coalesced: list[dict] = []
    pending_blocks: list[dict] = []

    def flush_pending() -> None:
        if pending_blocks:
            coalesced.append(
                {
                    "role": "user",
                    "content": copy.deepcopy(pending_blocks),
                }
            )
            pending_blocks.clear()

    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        is_tool_result_message = (
            isinstance(message, dict)
            and message.get("role") == "user"
            and isinstance(content, list)
            and bool(content)
            and all(
                isinstance(block, dict) and block.get("type") == "tool_result"
                for block in content
            )
        )
        if is_tool_result_message:
            pending_blocks.extend(copy.deepcopy(content))
            continue
        flush_pending()
        if isinstance(message, dict):
            coalesced.append(copy.deepcopy(message))
    flush_pending()
    return coalesced


__all__ = [
    "AnthropicMessageBuilder",
    "GeminiMessageBuilder",
    "HyperspaceMessageBuilder",
    "OllamaMessageBuilder",
    "OpenAIMessageBuilder",
    "ProviderMessageBuilder",
    "coalesce_provider_tool_result_messages",
    "get_provider_message_builder",
]
