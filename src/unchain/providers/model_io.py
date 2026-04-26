from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING, Protocol, runtime_checkable

import httpx

from openai import OpenAI

from ..schemas import ResponseFormat
from ..tools.toolkit import Toolkit
from ..kernel.types import ModelTurnResult, TokenUsage, ToolCall

if TYPE_CHECKING:
    from ..runtime import Broth


def _deepcopy_messages(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [copy.deepcopy(message) for message in (messages or []) if isinstance(message, dict)]


def _parse_base64_data_url(value: Any, *, default_media_type: str) -> tuple[str, str] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw_value = value.strip()
    if not raw_value.startswith("data:"):
        return default_media_type, raw_value

    header, separator, data = raw_value.partition(",")
    if not separator or ";base64" not in header:
        return None
    media_type = header.removeprefix("data:").split(";", 1)[0] or default_media_type
    return media_type, data


def _anthropic_file_source(file_id: Any) -> dict[str, Any] | None:
    if isinstance(file_id, str) and file_id.strip():
        return {"type": "file", "file_id": file_id.strip()}
    return None


def _anthropic_url_source(url: Any) -> dict[str, Any] | None:
    if isinstance(url, str) and url.strip():
        return {"type": "url", "url": url.strip()}
    return None


def _anthropic_base64_source(
    data: Any,
    *,
    media_type: Any,
    default_media_type: str,
) -> dict[str, Any] | None:
    if not isinstance(data, str) or not data:
        return None
    resolved_media_type = (
        media_type if isinstance(media_type, str) and media_type.strip() else default_media_type
    )
    return {"type": "base64", "media_type": resolved_media_type, "data": data}


def _anthropic_source_from_canonical(
    source: Any,
    *,
    default_media_type: str,
) -> dict[str, Any] | None:
    if not isinstance(source, dict):
        return None

    source_type = source.get("type")
    if source_type == "base64":
        return _anthropic_base64_source(
            source.get("data"),
            media_type=source.get("media_type"),
            default_media_type=default_media_type,
        )
    if source_type == "url":
        return _anthropic_url_source(source.get("url"))
    if source_type in ("file", "file_id"):
        return _anthropic_file_source(source.get("file_id"))
    return None


def _anthropic_source_from_input_image(block: dict[str, Any]) -> dict[str, Any] | None:
    image_url = block.get("image_url")
    if isinstance(image_url, dict):
        image_url = image_url.get("url")

    parsed_data_url = _parse_base64_data_url(image_url, default_media_type="image/png")
    if parsed_data_url is not None:
        media_type, data = parsed_data_url
        if isinstance(image_url, str) and image_url.strip().startswith("data:"):
            return _anthropic_base64_source(
                data,
                media_type=media_type,
                default_media_type="image/png",
            )
        return _anthropic_url_source(image_url)

    return _anthropic_file_source(block.get("file_id"))


def _anthropic_source_from_input_file(block: dict[str, Any]) -> dict[str, Any] | None:
    source = _anthropic_file_source(block.get("file_id"))
    if source is not None:
        return source

    source = _anthropic_url_source(block.get("file_url"))
    if source is not None:
        return source

    parsed_file_data = _parse_base64_data_url(
        block.get("file_data"),
        default_media_type="application/pdf",
    )
    if parsed_file_data is not None:
        media_type, data = parsed_file_data
        return _anthropic_base64_source(
            data,
            media_type=media_type,
            default_media_type="application/pdf",
        )

    return _anthropic_source_from_canonical(
        block.get("source"),
        default_media_type="application/pdf",
    )


def _translate_content_blocks_for_anthropic(messages: list[dict[str, Any]]) -> None:
    """Convert unchain canonical content blocks into Anthropic-native format, in place.

    Accept both unchain canonical blocks and OpenAI Responses-style input
    blocks so callers can reuse message payloads across providers.
    """
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue

        new_content: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue

            btype = block.get("type")
            if btype == "input_text":
                new_content.append({
                    "type": "text",
                    "text": block.get("text", "") or "",
                })
                continue

            if btype == "input_image":
                source = _anthropic_source_from_input_image(block)
                if source is None:
                    new_content.append(block)
                else:
                    new_content.append({"type": "image", "source": source})
                continue

            if btype == "image":
                source = _anthropic_source_from_canonical(
                    block.get("source"),
                    default_media_type="image/png",
                )
                if source is None:
                    new_content.append(block)
                else:
                    next_block = copy.deepcopy(block)
                    next_block["type"] = "image"
                    next_block["source"] = source
                    new_content.append(next_block)
                continue

            if btype == "input_file":
                source = _anthropic_source_from_input_file(block)
                if source is None:
                    new_content.append(block)
                else:
                    new_content.append({"type": "document", "source": source})
                continue

            if btype in ("pdf", "document"):
                source = _anthropic_source_from_canonical(
                    block.get("source"),
                    default_media_type="application/pdf",
                )
                if source is None:
                    if btype == "pdf":
                        next_block = copy.deepcopy(block)
                        next_block["type"] = "document"
                        new_content.append(next_block)
                    else:
                        new_content.append(block)
                else:
                    next_block = copy.deepcopy(block)
                    next_block["type"] = "document"
                    next_block["source"] = source
                    new_content.append(next_block)
                continue

            new_content.append(block)

        message["content"] = new_content


def _translate_content_blocks_for_openai(messages: list[dict[str, Any]]) -> None:
    """Convert unchain canonical content blocks into OpenAI Responses API format, in place.

    Only user/system/developer messages are rewritten — assistant messages have
    their own output format managed elsewhere. Attachment blocks (``image`` /
    ``pdf``) are always translated. Plain ``{"type": "text", "text": ...}``
    blocks are also upgraded to ``input_text`` when they appear alongside an
    attachment, because Responses API rejects mixing ``text`` and
    ``input_image`` in the same message. Text-only messages are left alone so
    existing text chats keep working unchanged.
    """
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in ("user", "system", "developer"):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue

        has_attachment = any(
            isinstance(block, dict) and block.get("type") in ("image", "pdf")
            for block in content
        )
        if not has_attachment:
            continue

        new_content: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue
            btype = block.get("type")

            if btype == "text":
                new_content.append({
                    "type": "input_text",
                    "text": block.get("text", "") or "",
                })
                continue

            if btype == "image":
                source = block.get("source") if isinstance(block.get("source"), dict) else {}
                source_type = source.get("type")
                if source_type == "base64":
                    media_type = source.get("media_type") or "image/png"
                    data = source.get("data") or ""
                    new_content.append({
                        "type": "input_image",
                        "image_url": f"data:{media_type};base64,{data}",
                    })
                elif source_type == "url":
                    url = source.get("url") or ""
                    new_content.append({"type": "input_image", "image_url": url})
                else:
                    new_content.append(block)
                continue

            if btype == "pdf":
                source = block.get("source") if isinstance(block.get("source"), dict) else {}
                source_type = source.get("type")
                if source_type == "base64":
                    media_type = source.get("media_type") or "application/pdf"
                    data = source.get("data") or ""
                    filename = source.get("filename") or "document.pdf"
                    new_content.append({
                        "type": "input_file",
                        "filename": filename,
                        "file_data": f"data:{media_type};base64,{data}",
                    })
                elif source_type == "file_id":
                    file_id = source.get("file_id") or ""
                    new_content.append({"type": "input_file", "file_id": file_id})
                elif source_type == "url":
                    # Responses API accepts remote files via file_url
                    url = source.get("url") or ""
                    new_content.append({"type": "input_file", "file_url": url})
                else:
                    new_content.append(block)
                continue

            new_content.append(block)

        message["content"] = new_content


@dataclass(frozen=True)
class ModelTurnRequest:
    messages: list[dict[str, Any]]
    payload: dict[str, Any] = field(default_factory=dict)
    response_format: ResponseFormat | None = None
    callback: Callable[[dict[str, Any]], None] | None = None
    verbose: bool = False
    run_id: str = "kernel"
    iteration: int = 0
    toolkit: Toolkit = field(default_factory=Toolkit)
    emit_stream: bool = False
    previous_response_id: str | None = None
    openai_text_format: dict[str, Any] | None = None

    def copied_messages(self) -> list[dict[str, Any]]:
        return _deepcopy_messages(self.messages)


@runtime_checkable
class ModelIO(Protocol):
    """Provider-facing boundary used by the new kernel loop."""

    provider: str

    def fetch_turn(self, request: ModelTurnRequest) -> ModelTurnResult:
        ...


class LegacyBrothModelIO:
    """Bridge the new kernel loop onto the legacy Broth provider layer."""

    def __init__(self, engine: Broth) -> None:
        self.engine = engine

    @classmethod
    def from_config(
        cls,
        *,
        provider: str = "openai",
        model: str = "gpt-5",
        api_key: str | None = None,
    ) -> "LegacyBrothModelIO":
        from ..runtime import Broth

        return cls(Broth(provider=provider, model=model, api_key=api_key))

    def fetch_turn(self, request: ModelTurnRequest) -> ModelTurnResult:
        result = self.engine._fetch_once(
            messages=request.copied_messages(),
            payload=copy.deepcopy(request.payload),
            response_format=request.response_format,
            callback=request.callback,
            verbose=bool(request.verbose),
            run_id=request.run_id,
            iteration=int(request.iteration),
            toolkit=request.toolkit,
            emit_stream=bool(request.emit_stream),
            previous_response_id=request.previous_response_id,
            openai_text_format=copy.deepcopy(request.openai_text_format),
        )
        return ModelTurnResult(
            assistant_messages=_deepcopy_messages(result.assistant_messages),
            tool_calls=[
                ToolCall(
                    call_id=str(tool_call.call_id),
                    name=str(tool_call.name),
                    arguments=copy.deepcopy(tool_call.arguments),
                )
                for tool_call in result.tool_calls
            ],
            final_text=str(result.final_text or ""),
            response_id=result.response_id,
            reasoning_items=copy.deepcopy(result.reasoning_items)
            if isinstance(result.reasoning_items, list)
            else None,
            consumed_tokens=int(result.consumed_tokens or 0),
            input_tokens=int(result.input_tokens or 0),
            output_tokens=int(result.output_tokens or 0),
        )


class _NativeModelIOBase:
    provider = ""

    def __init__(
        self,
        *,
        model: str,
        default_payloads: dict[str, dict[str, Any]] | None = None,
        model_capabilities: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError(f"{self.__class__.__name__} requires a non-empty model")

        self.model = model.strip()

        if default_payloads is None or model_capabilities is None:
            from ..runtime.payloads import load_default_payloads, load_model_capabilities

            if default_payloads is None:
                default_payloads = load_default_payloads()
            if model_capabilities is None:
                model_capabilities = load_model_capabilities()

        self.default_payloads = copy.deepcopy(default_payloads or {})
        self.model_capabilities = copy.deepcopy(model_capabilities or {})

    def _resolve_model_key(self, registry: dict[str, Any]) -> str | None:
        if self.model in registry:
            return self.model
        normalized_model = self.model.replace(".", "-")
        best: str | None = None
        for key in registry:
            normalized_key = str(key).replace(".", "-")
            if (
                self.model.startswith(key)
                or self.model.startswith(normalized_key)
                or normalized_model.startswith(key)
                or normalized_model.startswith(normalized_key)
                or key.startswith(self.model)
                or key.startswith(normalized_model)
                or normalized_key.startswith(self.model)
                or normalized_key.startswith(normalized_model)
            ) and (best is None or len(str(key)) > len(best)):
                best = str(key)
        return best

    def _model_capability(self, key: str, default: Any = None) -> Any:
        resolved = self._resolve_model_key(self.model_capabilities)
        model_caps = self.model_capabilities.get(resolved, {}) if resolved else {}
        if not isinstance(model_caps, dict):
            return default
        return model_caps.get(key, default)

    def _provider_request_model(self) -> str:
        resolved_model = self._model_capability("provider_model", self.model)
        if isinstance(resolved_model, str) and resolved_model.strip():
            return resolved_model.strip()
        return self.model

    def _merged_payload(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        resolved_key = self._resolve_model_key(self.default_payloads)
        defaults = copy.deepcopy(self.default_payloads.get(resolved_key, {}) if resolved_key else {})
        if not isinstance(defaults, dict):
            return {}

        user_payload = payload or {}
        for key in list(defaults.keys()):
            if key in user_payload:
                defaults[key] = user_payload[key]

        allowed_keys = self._model_capability("allowed_payload_keys", None)
        if isinstance(allowed_keys, list) and allowed_keys:
            allowed_key_set = {key for key in allowed_keys if isinstance(key, str)}
            for key in user_payload:
                if key in allowed_key_set and key not in defaults:
                    defaults[key] = user_payload[key]
            defaults = {key: value for key, value in defaults.items() if key in allowed_key_set}

        defaults = {key: value for key, value in defaults.items() if value is not None or key in user_payload}
        return defaults

    def _coerce_token_count(self, value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _normalize_token_usage(self, *, input_tokens: Any, output_tokens: Any) -> TokenUsage:
        resolved_input = self._coerce_token_count(input_tokens)
        resolved_output = self._coerce_token_count(output_tokens)
        return TokenUsage(
            consumed_tokens=resolved_input + resolved_output,
            input_tokens=resolved_input,
            output_tokens=resolved_output,
        )

    def _tool_names_for_trace(self, tools_json: list[dict[str, Any]] | None) -> list[str]:
        tool_names: list[str] = []
        for tool in tools_json or []:
            name = str(tool.get("name", "")).strip()
            if not name and isinstance(tool.get("function"), dict):
                name = str(tool["function"].get("name", "")).strip()
            if name:
                tool_names.append(name)
        return tool_names

    def _emit_request_messages(
        self,
        *,
        callback: Callable[[dict[str, Any]], None] | None,
        run_id: str,
        iteration: int,
        messages: list[dict[str, Any]],
        previous_response_id: str | None = None,
        tool_names: list[str] | None = None,
        **extra: Any,
    ) -> None:
        payload: dict[str, Any] = {
            "provider": self.provider,
            "messages": copy.deepcopy(messages),
        }
        if previous_response_id is not None:
            payload["previous_response_id"] = previous_response_id
        if tool_names:
            payload["tool_names"] = list(tool_names)
        payload.update(copy.deepcopy(extra))
        self._emit(callback, "request_messages", run_id, iteration=iteration, **payload)

    def _emit(
        self,
        callback: Callable[[dict[str, Any]], None] | None,
        event_type: str,
        run_id: str,
        *,
        iteration: int,
        **extra: Any,
    ) -> None:
        if callback is None:
            return
        event = {
            "type": event_type,
            "run_id": run_id,
            "iteration": iteration,
        }
        event.update(extra)
        callback(event)

    def _as_dict(self, obj: Any) -> dict[str, Any]:
        if obj is None:
            return {}
        if isinstance(obj, dict):
            return copy.deepcopy(obj)
        if hasattr(obj, "model_dump"):
            dumped = obj.model_dump()
            return copy.deepcopy(dumped) if isinstance(dumped, dict) else {}
        if hasattr(obj, "to_dict"):
            dumped = obj.to_dict()
            return copy.deepcopy(dumped) if isinstance(dumped, dict) else {}
        if hasattr(obj, "__dict__"):
            raw = {
                key: value
                for key, value in vars(obj).items()
                if not key.startswith("_")
            }
            return copy.deepcopy(raw)
        return {}


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
        # OpenAI reports cached tokens inside input_tokens_details.
        details = usage_dict.get("input_tokens_details")
        if not isinstance(details, dict):
            details = self._as_dict(details)
        cached = self._coerce_token_count(details.get("cached_tokens") if isinstance(details, dict) else None)
        return TokenUsage(
            consumed_tokens=total_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ), cached

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
        # from it is intentionally ignored — see total_consumed below).
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


class OllamaModelIO(_NativeModelIOBase):
    """Native Ollama chat API adapter for the new kernel."""

    provider = "ollama"

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://localhost:11434",
        stream_factory: Callable[..., Any] | None = None,
        default_payloads: dict[str, dict[str, Any]] | None = None,
        model_capabilities: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            model=model,
            default_payloads=default_payloads,
            model_capabilities=model_capabilities,
        )
        self.base_url = str(base_url or "http://localhost:11434").rstrip("/")
        if stream_factory is None:
            stream_factory = httpx.stream
        self._stream_factory = stream_factory

    def fetch_turn(self, request: ModelTurnRequest) -> ModelTurnResult:
        request_body: dict[str, Any] = {
            "model": self.model,
            "messages": copy.deepcopy(request.messages),
            "stream": True,
        }

        tools_json = request.toolkit.to_provider_json(self.provider)
        tools: list[dict[str, Any]] = []
        if tools_json and self._model_capability("supports_tools", True):
            tools = copy.deepcopy(tools_json)

        if tools:
            request_body["tools"] = tools
            request_body["tool_choice"] = "auto"

        merged_payload = self._merged_payload(request.payload)
        if merged_payload:
            request_body["options"] = merged_payload

        if request.response_format is not None:
            request_body["format"] = request.response_format.to_ollama()

        self._emit_request_messages(
            callback=request.callback,
            run_id=request.run_id,
            iteration=request.iteration,
            messages=request_body.get("messages", []),
            tool_names=self._tool_names_for_trace(tools),
        )

        collected_chunks: list[str] = []
        reasoning_chunks: list[str] = []
        latest_prompt_eval_count = 0
        latest_eval_count = 0

        with self._stream_factory(
            "POST",
            f"{self.base_url}/api/chat",
            json=request_body,
            timeout=None,
        ) as response:
            if int(getattr(response, "status_code", 0) or 0) >= 400:
                raw_detail = response.read()
                if isinstance(raw_detail, bytes):
                    detail = raw_detail.decode()
                else:
                    detail = str(raw_detail)
                raise ValueError(f"error: {detail} ( kernel.model_io -> OllamaModelIO.fetch_turn )")
            response.raise_for_status()

            for line in response.iter_lines():
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode()

                data = json.loads(line)
                if data.get("error"):
                    raise ValueError(f"error: {data['error']} ( kernel.model_io -> OllamaModelIO.fetch_turn )")
                if isinstance(data.get("prompt_eval_count"), int):
                    latest_prompt_eval_count = data["prompt_eval_count"]
                if isinstance(data.get("eval_count"), int):
                    latest_eval_count = data["eval_count"]

                message = data.get("message") or {}
                content_delta = message.get("content", "") or ""
                thinking_delta = message.get("thinking", "") or ""

                if thinking_delta:
                    reasoning_chunks.append(thinking_delta)

                if content_delta:
                    collected_chunks.append(content_delta)
                    if request.emit_stream:
                        self._emit(
                            request.callback,
                            "token_delta",
                            request.run_id,
                            iteration=request.iteration,
                            provider=self.provider,
                            delta=content_delta,
                            accumulated_text="".join(collected_chunks),
                        )

                raw_tool_calls = message.get("tool_calls") or []
                if raw_tool_calls:
                    assistant_message = {
                        "role": "assistant",
                        "content": message.get("content", ""),
                        "tool_calls": copy.deepcopy(raw_tool_calls),
                    }
                    tool_calls: list[ToolCall] = []
                    for raw_tool_call in raw_tool_calls:
                        fn = raw_tool_call.get("function", {}) or {}
                        tool_calls.append(
                            ToolCall(
                                call_id=str(raw_tool_call.get("id") or str(uuid.uuid4())),
                                name=str(fn.get("name", "") or ""),
                                arguments=copy.deepcopy(fn.get("arguments", {})),
                            )
                        )

                    return ModelTurnResult(
                        assistant_messages=[assistant_message],
                        tool_calls=tool_calls,
                        final_text="",
                        response_id=None,
                        reasoning_items=[{"type": "thinking", "text": "".join(reasoning_chunks)}] if reasoning_chunks else None,
                        consumed_tokens=latest_prompt_eval_count + latest_eval_count,
                        input_tokens=latest_prompt_eval_count,
                        output_tokens=latest_eval_count,
                    )

                if data.get("done", False):
                    full_message = message.get("content") or "".join(collected_chunks)
                    return ModelTurnResult(
                        assistant_messages=[{"role": "assistant", "content": full_message}],
                        tool_calls=[],
                        final_text=full_message,
                        response_id=None,
                        reasoning_items=[{"type": "thinking", "text": "".join(reasoning_chunks)}] if reasoning_chunks else None,
                        consumed_tokens=latest_prompt_eval_count + latest_eval_count,
                        input_tokens=latest_prompt_eval_count,
                        output_tokens=latest_eval_count,
                    )

        raise ValueError("error: unexpected termination of ollama stream. ( kernel.model_io -> OllamaModelIO.fetch_turn )")
