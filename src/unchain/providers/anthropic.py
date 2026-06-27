from __future__ import annotations

import copy
import json
import uuid
from typing import Any, Callable

import httpx

from .base import ModelTurnRequest
from .native import _NativeModelIOBase, _translate_content_blocks_for_anthropic
from ..kernel.types import ModelTurnResult, ToolCall


class AnthropicModelIO(_NativeModelIOBase):
    """Native Anthropic Messages API adapter for the new kernel."""

    provider = "anthropic"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        client_factory: Callable[..., Any] | None = None,
        default_payloads: dict[str, dict[str, Any]] | None = None,
        model_capabilities: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("AnthropicModelIO requires a non-empty api_key")
        super().__init__(
            model=model,
            default_payloads=default_payloads,
            model_capabilities=model_capabilities,
        )
        self.api_key = api_key
        if client_factory is None:
            try:
                import anthropic
                client_factory = anthropic.Anthropic
            except ImportError:
                raise ImportError("anthropic package is required for anthropic provider — pip install anthropic")
        self._client_factory = client_factory

    _ANTHROPIC_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)

    def fetch_turn(self, request: ModelTurnRequest) -> ModelTurnResult:
        client = self._client_factory(api_key=self.api_key, timeout=self._ANTHROPIC_TIMEOUT)
        request_payload = self._merged_payload(request.payload)

        system_parts: list[str] = []
        chat_messages: list[dict[str, Any]] = []
        for message in request.messages:
            if isinstance(message, dict) and message.get("role") == "system":
                content = message.get("content", "")
                if isinstance(content, str) and content.strip():
                    system_parts.append(content.strip())
                elif content not in (None, ""):
                    system_parts.append(str(content))
                continue
            chat_messages.append(copy.deepcopy(message))

        _translate_content_blocks_for_anthropic(chat_messages)

        if request.response_format is not None:
            system_parts.append(request.response_format.to_anthropic())
        system_prompt = "\n\n".join(part for part in system_parts if isinstance(part, str) and part.strip())

        tools_json = request.toolkit.to_provider_json(self.provider)
        anthropic_tools: list[dict[str, Any]] = []
        if tools_json and self._model_capability("supports_tools", True):
            anthropic_tools = copy.deepcopy(tools_json)

        _default_max = self._model_capability("max_output_tokens", 4096)
        max_tokens = request_payload.pop("max_tokens", _default_max)
        request_kwargs: dict[str, Any] = {
            "model": self._provider_request_model(),
            "messages": chat_messages,
            "max_tokens": max_tokens,
            **request_payload,
        }
        if system_prompt:
            request_kwargs["system"] = [
                {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
            ]
        if anthropic_tools:
            anthropic_tools[-1]["cache_control"] = {"type": "ephemeral"}
            request_kwargs["tools"] = anthropic_tools
        # Annotate last message for prompt caching.
        if chat_messages:
            _last = chat_messages[-1]
            _content = _last.get("content")
            if isinstance(_content, str):
                _last["content"] = [{"type": "text", "text": _content, "cache_control": {"type": "ephemeral"}}]
            elif isinstance(_content, list) and _content:
                _block = _content[-1]
                if isinstance(_block, dict):
                    _block["cache_control"] = {"type": "ephemeral"}

        self._emit_request_messages(
            callback=request.callback,
            run_id=request.run_id,
            iteration=request.iteration,
            messages=chat_messages,
            tool_names=self._tool_names_for_trace(tools_json),
            system=system_prompt if system_prompt else None,
        )

        if not chat_messages:
            raise ValueError(
                "Anthropic request has no chat messages after preprocessing. "
                "This usually means context optimization or memory/history "
                "selection dropped the active turn before provider call."
            )

        collected_chunks: list[str] = []
        assistant_messages: list[dict[str, Any]] = []
        tool_calls: list[ToolCall] = []
        final_text_parts: list[str] = []
        reasoning_items: list[dict[str, Any]] = []
        input_tokens = 0
        output_tokens = 0
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 0
        current_tool_name = ""
        current_tool_id = ""
        current_tool_json_parts: list[str] = []
        content_blocks: list[dict[str, Any]] = []
        in_thinking_block = False
        current_thinking_parts: list[str] = []

        with client.messages.stream(**request_kwargs) as stream:
            for event in stream:
                event_type = getattr(event, "type", None)

                if event_type == "content_block_start":
                    block_dict = self._as_dict(getattr(event, "content_block", None))
                    if block_dict.get("type") == "tool_use":
                        current_tool_name = str(block_dict.get("name", "") or "")
                        current_tool_id = str(block_dict.get("id") or str(uuid.uuid4()))
                        current_tool_json_parts = []
                    elif block_dict.get("type") == "thinking":
                        in_thinking_block = True
                        current_thinking_parts = []
                    continue

                if event_type == "content_block_delta":
                    delta_dict = self._as_dict(getattr(event, "delta", None))
                    delta_type = delta_dict.get("type", "")
                    if delta_type == "thinking_delta":
                        thinking_text = delta_dict.get("thinking", "") or ""
                        if thinking_text:
                            current_thinking_parts.append(thinking_text)
                            if request.emit_stream:
                                self._emit(
                                    request.callback,
                                    "reasoning",
                                    request.run_id,
                                    iteration=request.iteration,
                                    provider=self.provider,
                                    delta=thinking_text,
                                )
                        continue
                    if delta_type == "text_delta":
                        text = delta_dict.get("text", "") or ""
                        if text:
                            collected_chunks.append(text)
                            if request.emit_stream:
                                self._emit(
                                    request.callback,
                                    "token_delta",
                                    request.run_id,
                                    iteration=request.iteration,
                                    provider=self.provider,
                                    delta=text,
                                    accumulated_text="".join(collected_chunks),
                                )
                        continue
                    if delta_type == "input_json_delta":
                        partial = delta_dict.get("partial_json", "") or ""
                        if partial:
                            current_tool_json_parts.append(partial)
                    continue

                if event_type == "content_block_stop":
                    if in_thinking_block and current_thinking_parts:
                        reasoning_items.append({
                            "type": "thinking",
                            "text": "".join(current_thinking_parts),
                        })
                        in_thinking_block = False
                        current_thinking_parts = []
                    elif current_tool_name:
                        raw_json = "".join(current_tool_json_parts)
                        try:
                            arguments = json.loads(raw_json) if raw_json.strip() else {}
                        except json.JSONDecodeError:
                            arguments = raw_json
                        tool_calls.append(
                            ToolCall(
                                call_id=current_tool_id,
                                name=current_tool_name,
                                arguments=copy.deepcopy(arguments),
                            )
                        )
                        content_blocks.append({
                            "type": "tool_use",
                            "id": current_tool_id,
                            "name": current_tool_name,
                            "input": arguments if isinstance(arguments, dict) else {},
                        })
                        current_tool_name = ""
                        current_tool_id = ""
                        current_tool_json_parts = []
                    else:
                        in_thinking_block = False
                    continue

                if event_type == "message_delta":
                    usage_dict = self._as_dict(getattr(event, "usage", None))
                    if usage_dict:
                        input_tokens = max(input_tokens, self._coerce_token_count(usage_dict.get("input_tokens")))
                        output_tokens = max(output_tokens, self._coerce_token_count(usage_dict.get("output_tokens")))
                        cache_read_input_tokens = max(cache_read_input_tokens, self._coerce_token_count(usage_dict.get("cache_read_input_tokens")))
                        cache_creation_input_tokens = max(cache_creation_input_tokens, self._coerce_token_count(usage_dict.get("cache_creation_input_tokens")))
                    continue

                if event_type == "message_start":
                    msg_dict = self._as_dict(getattr(event, "message", None))
                    usage_dict = msg_dict.get("usage", {}) if isinstance(msg_dict, dict) else {}
                    if isinstance(usage_dict, dict):
                        input_tokens = max(input_tokens, self._coerce_token_count(usage_dict.get("input_tokens")))
                        output_tokens = max(output_tokens, self._coerce_token_count(usage_dict.get("output_tokens")))
                        cache_read_input_tokens = max(cache_read_input_tokens, self._coerce_token_count(usage_dict.get("cache_read_input_tokens")))
                        cache_creation_input_tokens = max(cache_creation_input_tokens, self._coerce_token_count(usage_dict.get("cache_creation_input_tokens")))
                    continue

        full_text = "".join(collected_chunks).strip()
        if full_text:
            final_text_parts.append(full_text)

        if tool_calls:
            assistant_content: list[dict[str, Any]] = []
            if full_text:
                assistant_content.append({"type": "text", "text": full_text})
            assistant_content.extend(content_blocks)
            assistant_messages.append({
                "role": "assistant",
                "content": assistant_content,
            })
        elif full_text:
            assistant_messages.append({"role": "assistant", "content": full_text})

        # Coerce input/output to ints via _normalize_token_usage (consumed_tokens
        # from it is intentionally ignored -- see total_consumed below).
        token_usage = self._normalize_token_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        # Anthropic's input_tokens excludes cached tokens, so we add
        # cache_read + cache_creation to get the true total processed.
        total_consumed = (
            input_tokens
            + cache_read_input_tokens
            + cache_creation_input_tokens
            + output_tokens
        )
        return ModelTurnResult(
            assistant_messages=assistant_messages,
            tool_calls=tool_calls,
            final_text="".join(final_text_parts).strip(),
            response_id=None,
            reasoning_items=reasoning_items or None,
            consumed_tokens=total_consumed,
            input_tokens=token_usage.input_tokens,
            output_tokens=token_usage.output_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
        )


__all__ = ["AnthropicModelIO"]
