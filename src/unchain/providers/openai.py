from __future__ import annotations

import copy
import uuid
from typing import Any, Callable

from openai import OpenAI

from .base import ModelTurnRequest
from .model_io import _NativeModelIOBase, _translate_content_blocks_for_openai
from ..kernel.types import ModelTurnResult, TokenUsage, ToolCall


class OpenAIModelIO(_NativeModelIOBase):
    """Native OpenAI Responses API adapter for the new kernel."""

    provider = "openai"

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
            raise ValueError("OpenAIModelIO requires a non-empty api_key")
        self.api_key = api_key
        self._client_factory = client_factory or OpenAI
        super().__init__(
            model=model,
            default_payloads=default_payloads,
            model_capabilities=model_capabilities,
        )

    def fetch_turn(self, request: ModelTurnRequest) -> ModelTurnResult:
        openai_client = self._client_factory(api_key=self.api_key)
        normalized_messages = self._normalize_input_messages(request.messages)
        request_payload = self._merged_payload(request.payload)

        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "input": normalized_messages,
            **request_payload,
            "stream": True,
        }
        if request.previous_response_id:
            request_kwargs["previous_response_id"] = request.previous_response_id

        tools_json = request.toolkit.to_provider_json(self.provider)
        if tools_json and self._model_capability("supports_tools", True):
            request_kwargs["tools"] = tools_json

        resolved_text_format = None
        if isinstance(request.openai_text_format, dict):
            resolved_text_format = copy.deepcopy(request.openai_text_format)
        elif request.response_format is not None:
            resolved_text_format = request.response_format.to_openai()
        if isinstance(resolved_text_format, dict):
            text_config = (
                dict(request_kwargs["text"])
                if isinstance(request_kwargs.get("text"), dict)
                else {}
            )
            text_config["format"] = resolved_text_format
            request_kwargs["text"] = text_config

        self._emit_request_messages(
            callback=request.callback,
            run_id=request.run_id,
            iteration=request.iteration,
            messages=normalized_messages,
            previous_response_id=request.previous_response_id,
            tool_names=self._tool_names_for_trace(tools_json),
        )

        try:
            return self._fetch_turn_streaming(openai_client, request, request_kwargs)
        except Exception as exc:
            if request_kwargs.get("previous_response_id") and self._is_previous_response_error(exc):
                request_kwargs.pop("previous_response_id", None)
                request_kwargs["input"] = normalized_messages
                if request.callback:
                    self._emit(
                        request.callback,
                        "previous_response_id_fallback",
                        request.run_id,
                        iteration=request.iteration,
                        provider="openai",
                    )
                return self._fetch_turn_streaming(openai_client, request, request_kwargs)
            raise

    @staticmethod
    def _is_previous_response_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "previous_response" in msg or "not_found" in msg or "no tool call found" in msg

    def _fetch_turn_streaming(
        self,
        openai_client: Any,
        request: ModelTurnRequest,
        request_kwargs: dict[str, Any],
    ) -> ModelTurnResult:
        collected_chunks: list[str] = []
        completed_response = None
        created_response_id: str | None = None
        output_items_from_events: dict[int, dict[str, Any]] = {}

        with openai_client.responses.create(**request_kwargs) as stream_response:
            for chunk in stream_response:
                chunk_type = getattr(chunk, "type", None)
                if chunk_type == "response.output_text.delta":
                    delta = getattr(chunk, "delta", "") or ""
                    if delta:
                        collected_chunks.append(delta)
                        if request.emit_stream:
                            self._emit(
                                request.callback,
                                "token_delta",
                                request.run_id,
                                iteration=request.iteration,
                                provider="openai",
                                delta=delta,
                                accumulated_text="".join(collected_chunks),
                            )
                    continue
                if chunk_type == "response.error":
                    raise ValueError("error: OpenAI text generation failed")
                if chunk_type == "response.created":
                    response_obj = getattr(chunk, "response", None)
                    created = self._as_dict(response_obj)
                    if isinstance(created, dict):
                        cid = created.get("id")
                        if isinstance(cid, str) and cid:
                            created_response_id = cid
                    if created_response_id is None:
                        fallback_id = getattr(response_obj, "id", None)
                        if isinstance(fallback_id, str) and fallback_id:
                            created_response_id = fallback_id
                    continue
                if chunk_type == "response.output_item.done":
                    item = self._as_dict(getattr(chunk, "item", None))
                    output_index = getattr(chunk, "output_index", None)
                    if isinstance(item, dict) and isinstance(output_index, int):
                        output_items_from_events[output_index] = item
                    continue
                if chunk_type == "response.completed":
                    completed_response = getattr(chunk, "response", None)

        cached_input_tokens = 0
        if completed_response is None:
            if output_items_from_events:
                outputs = [
                    output_items_from_events[idx]
                    for idx in sorted(output_items_from_events.keys())
                ]
                response_id = created_response_id
                usage = TokenUsage()
            elif collected_chunks:
                full_text = "".join(collected_chunks).strip()
                return ModelTurnResult(
                    assistant_messages=[{"role": "assistant", "content": full_text}],
                    tool_calls=[],
                    final_text=full_text,
                    response_id=created_response_id,
                )
            else:
                raise ValueError("error: OpenAI stream ended without completion payload")
        else:
            outputs = getattr(completed_response, "output", None) or []
            response_id = getattr(completed_response, "id", None)
            usage, cached_input_tokens = self._extract_openai_token_usage(getattr(completed_response, "usage", None))

        assistant_messages: list[dict[str, Any]] = []
        tool_calls: list[ToolCall] = []
        final_text_parts: list[str] = []
        reasoning_items: list[dict[str, Any]] = []

        for output_item in outputs:
            item = self._as_dict(output_item)
            item_type = item.get("type")
            if item_type == "function_call":
                call_id = item.get("call_id") or item.get("id") or str(uuid.uuid4())
                tool_calls.append(
                    ToolCall(
                        call_id=str(call_id),
                        name=str(item.get("name", "")),
                        arguments=copy.deepcopy(item.get("arguments", "{}")),
                    )
                )
                assistant_messages.append({
                    "type": "function_call",
                    "call_id": str(call_id),
                    "name": item.get("name", ""),
                    "arguments": copy.deepcopy(item.get("arguments", "{}")),
                })
                continue
            if item_type == "message":
                text = self._extract_openai_message_text(item)
                if text:
                    assistant_messages.append({"role": "assistant", "content": text})
                    final_text_parts.append(text)
                continue
            if item_type == "reasoning":
                reasoning_items.append(item)
                continue

        if not tool_calls and not final_text_parts and collected_chunks:
            full_text = "".join(collected_chunks)
            assistant_messages.append({"role": "assistant", "content": full_text})
            final_text_parts.append(full_text)

        return ModelTurnResult(
            assistant_messages=assistant_messages,
            tool_calls=tool_calls,
            final_text="".join(final_text_parts).strip(),
            response_id=response_id,
            reasoning_items=reasoning_items or None,
            consumed_tokens=usage.consumed_tokens,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=cached_input_tokens,
        )

    def _normalize_input_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                normalized.append(copy.deepcopy(message))
                continue
            item_type = message.get("type")
            if item_type == "function_call":
                call_id = message.get("call_id") or message.get("id") or str(uuid.uuid4())
                normalized.append({
                    "type": "function_call",
                    "call_id": call_id,
                    "name": message.get("name", ""),
                    "arguments": message.get("arguments", "{}"),
                })
                continue
            normalized.append(copy.deepcopy(message))
        _translate_content_blocks_for_openai(normalized)
        return normalized

    def _extract_openai_message_text(self, item: dict[str, Any]) -> str:
        content = item.get("content")
        if not isinstance(content, list):
            return ""
        text_parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in ("output_text", "text"):
                text = block.get("text", "")
                if text:
                    text_parts.append(text if isinstance(text, str) else str(text))
        return "".join(text_parts)

    def _extract_openai_token_usage(self, usage: Any) -> tuple[TokenUsage, int]:
        """Return (TokenUsage, cached_input_tokens) from an OpenAI usage object."""
        usage_dict = self._as_dict(usage)
        input_tokens = self._coerce_token_count(usage_dict.get("input_tokens"))
        output_tokens = self._coerce_token_count(usage_dict.get("output_tokens"))
        total_tokens = self._coerce_token_count(usage_dict.get("total_tokens"))
        if total_tokens == 0:
            total_tokens = input_tokens + output_tokens
        details = usage_dict.get("input_tokens_details")
        if not isinstance(details, dict):
            details = self._as_dict(details)
        cached = self._coerce_token_count(details.get("cached_tokens") if isinstance(details, dict) else None)
        return TokenUsage(
            consumed_tokens=total_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ), cached


__all__ = ["OpenAIModelIO"]
