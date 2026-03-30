from __future__ import annotations

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
    if normalized == "gemini":
        return GeminiMessageBuilder()
    if normalized == "ollama":
        return OllamaMessageBuilder()
    raise NotImplementedError(f"provider message builder is not implemented for provider={provider!r}")
