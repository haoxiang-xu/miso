from __future__ import annotations

import copy
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx
from openai import OpenAI

from .response_format import response_format
from .tool import toolkit as base_toolkit

try:
    from IPython.display import clear_output
except Exception:  # pragma: no cover
    clear_output = None

OBSERVATION_SYSTEM_PROMPT = """
You are a critical reviewer embedded in a multi-step AI agent pipeline.
You will receive recent conversation context and the results of one or more tool calls.
Your job is to review the LAST tool call result and provide a brief, actionable observation.

Check:
1. Does the result contain errors or warnings? If so, what specifically went wrong?
2. Are the returned values consistent with what was requested? (e.g. column names match, row counts make sense, no nulls where values were expected)
3. Is there anything the main assistant is likely to overlook or misinterpret in the next step?
4. Based on this result, what is the single most important thing to do or avoid next?

Rules:
- Be concise: 2-4 sentences maximum.
- Be specific: reference actual column names, values, error messages from the result.
- Do NOT repeat the result data — only comment on it.
- If everything looks correct, say so in one sentence and suggest the next logical action.
""".strip()

OBSERVATION_RECENT_MESSAGES = 6
OBSERVATION_MAX_OUTPUT_TOKENS = 512
DEFAULT_PAYLOADS_FILE = Path(__file__).with_name("model_default_payloads.json")
MODEL_CAPABILITIES_FILE = Path(__file__).with_name("model_capabilities.json")

@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any] | str | None

@dataclass
class ProviderTurnResult:
    assistant_messages: list[dict[str, Any]]
    tool_calls: list[ToolCall]
    final_text: str = ""
    response_id: str | None = None
    reasoning_items: list[dict[str, Any]] | None = None
    consumed_tokens: int = 0
    context_window_tokens: int = 0

class agent:
    def __init__(self):
        self.openai_api_key = None
        self.provider = "openai"
        self.model = "gpt-5"
        self.max_iterations = 6
        self.default_payload = self._load_default_payloads(DEFAULT_PAYLOADS_FILE)
        self.model_capabilities = self._load_model_capabilities(MODEL_CAPABILITIES_FILE)
        self.toolkit = base_toolkit()
        self.last_response_id: str | None = None
        self.last_reasoning_items: list[dict[str, Any]] = []
        self.last_consumed_tokens: int = 0

    def _load_default_payloads(self, path: str | Path) -> dict[str, dict[str, Any]]:
        file_path = Path(path)
        if not file_path.exists():
            return {}

        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        if not isinstance(raw, dict):
            return {}

        parsed: dict[str, dict[str, Any]] = {}
        for model_name, payload in raw.items():
            if isinstance(model_name, str) and isinstance(payload, dict):
                parsed[model_name] = payload
        return parsed

    def _load_model_capabilities(self, path: str | Path) -> dict[str, dict[str, Any]]:
        file_path = Path(path)
        if not file_path.exists():
            return {}

        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        if not isinstance(raw, dict):
            return {}

        parsed: dict[str, dict[str, Any]] = {}
        for model_name, capabilities in raw.items():
            if isinstance(model_name, str) and isinstance(capabilities, dict):
                parsed[model_name] = capabilities
        return parsed

    def _model_capability(self, key: str, default: Any = None) -> Any:
        model_caps = self.model_capabilities.get(self.model, {})
        if not isinstance(model_caps, dict):
            return default
        return model_caps.get(key, default)

    def run(
        self,
        messages,
        payload: dict[str, Any] | None = None,
        response_format: response_format | None = None,
        callback: Callable[[dict[str, Any]], None] | None = None,
        verbose: bool = False,
        max_iterations: int | None = None,
        previous_response_id: str | None = None,
    ):
        run_id = str(uuid.uuid4())
        seed_messages = copy.deepcopy(list(messages or []))
        conversation = copy.deepcopy(seed_messages)
        payload = dict(payload or {})
        max_loops = max_iterations or self.max_iterations
        supports_prev = bool(self._model_capability("supports_previous_response_id", True))
        next_previous_response_id = previous_response_id if supports_prev else None
        next_openai_input = copy.deepcopy(seed_messages)
        self.last_reasoning_items = []
        total_consumed_tokens = 0
        total_context_window_tokens = 0

        self._emit(callback, "run_started", run_id, iteration=0, provider=self.provider, model=self.model)

        for iteration in range(max_loops):
            self._emit(callback, "iteration_started", run_id, iteration=iteration)
            request_messages = conversation
            if self.provider == "openai":
                # With previous_response_id, OpenAI expects incremental input for each next turn.
                request_messages = next_openai_input

            turn = self._fetch_once(
                messages=request_messages,
                payload=payload,
                response_format=response_format,
                callback=callback,
                verbose=verbose,
                run_id=run_id,
                iteration=iteration,
                toolkit=self.toolkit,
                emit_stream=True,
                previous_response_id=next_previous_response_id if self.provider == "openai" else None,
            )
            if self.provider == "openai":
                next_previous_response_id = turn.response_id
                self.last_response_id = turn.response_id
            total_consumed_tokens += max(0, int(turn.consumed_tokens or 0))
            total_context_window_tokens += max(0, int(turn.context_window_tokens or 0))

            if turn.reasoning_items:
                self.last_reasoning_items = copy.deepcopy(turn.reasoning_items)
                self._emit(
                    callback,
                    "reasoning",
                    run_id,
                    iteration=iteration,
                    response_id=turn.response_id,
                    reasoning_items=turn.reasoning_items,
                )

            conversation.extend(turn.assistant_messages)

            if turn.tool_calls:
                tool_messages, should_observe = self._execute_tool_calls(
                    tool_calls=turn.tool_calls,
                    run_id=run_id,
                    iteration=iteration,
                    callback=callback,
                )

                if should_observe and tool_messages:
                    observation, observe_consumed_tokens = self._observe_tool_batch(
                        full_messages=conversation,
                        tool_messages=tool_messages,
                        payload=payload,
                    )
                    total_consumed_tokens += max(0, int(observe_consumed_tokens[0] or 0))
                    total_context_window_tokens += max(0, int(observe_consumed_tokens[1] or 0))
                    if observation:
                        self._inject_observation(tool_messages[-1], observation)
                        self._emit(
                            callback,
                            "observation",
                            run_id,
                            iteration=iteration,
                            content=observation,
                        )

                conversation.extend(tool_messages)
                if self.provider == "openai":
                    # Continue the same response chain with tool outputs only.
                    if next_previous_response_id:
                        next_openai_input = copy.deepcopy(tool_messages)
                    else:
                        next_openai_input = copy.deepcopy(conversation)
                self._emit(callback, "iteration_completed", run_id, iteration=iteration, has_tool_calls=True)
                continue

            self._apply_response_format(conversation, response_format)
            final_text = self._last_assistant_text(conversation)
            self._emit(
                callback,
                "final_message",
                run_id,
                iteration=iteration,
                content=final_text,
            )
            self._emit(callback, "run_completed", run_id, iteration=iteration)
            bundle = {"consumed_tokens": total_consumed_tokens, "context_window_tokens": total_context_window_tokens}
            self.last_consumed_tokens = total_consumed_tokens
            return conversation, bundle

        self._emit(callback, "run_max_iterations", run_id, iteration=max_loops)
        bundle = {"consumed_tokens": total_consumed_tokens, "context_window_tokens": total_context_window_tokens}
        self.last_consumed_tokens = total_consumed_tokens
        return conversation, bundle

    def _emit(
        self,
        callback: Callable[[dict[str, Any]], None] | None,
        event_type: str,
        run_id: str,
        *,
        iteration: int,
        **extra: Any,
    ):
        if callback is None:
            return
        event = {
            "type": event_type,
            "run_id": run_id,
            "iteration": iteration,
            "timestamp": time.time(),
        }
        event.update(extra)
        callback(event)

    def _fetch_once(
        self,
        *,
        messages: list[dict[str, Any]],
        payload: dict[str, Any],
        response_format: response_format | None,
        callback: Callable[[dict[str, Any]], None] | None,
        verbose: bool,
        run_id: str,
        iteration: int,
        toolkit: base_toolkit,
        emit_stream: bool,
        previous_response_id: str | None = None,
    ) -> ProviderTurnResult:
        if self.provider == "openai":
            return self._openai_fetch_once(
                messages=messages,
                payload=payload,
                response_format=response_format,
                callback=callback,
                verbose=verbose,
                run_id=run_id,
                iteration=iteration,
                toolkit=toolkit,
                emit_stream=emit_stream,
                previous_response_id=previous_response_id,
            )
        if self.provider == "ollama":
            return self._ollama_fetch_once(
                messages=messages,
                payload=payload,
                response_format=response_format,
                callback=callback,
                verbose=verbose,
                run_id=run_id,
                iteration=iteration,
                toolkit=toolkit,
                emit_stream=emit_stream,
            )
        raise ValueError("error: unsupported provider specified. ( agent -> run )")

    def _merged_payload(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        defaults = copy.deepcopy(self.default_payload.get(self.model, {}) or {})
        if not isinstance(defaults, dict):
            return {}

        user_payload = payload or {}
        for key in list(defaults.keys()):
            if key in user_payload:
                defaults[key] = user_payload[key]

        allowed_keys = self._model_capability("allowed_payload_keys", None)
        if isinstance(allowed_keys, list) and allowed_keys:
            allowed_key_set = {key for key in allowed_keys if isinstance(key, str)}
            defaults = {key: value for key, value in defaults.items() if key in allowed_key_set}

        return defaults

    def _openai_fetch_once(
        self,
        *,
        messages: list[dict[str, Any]],
        payload: dict[str, Any],
        response_format: response_format | None,
        callback: Callable[[dict[str, Any]], None] | None,
        verbose: bool,
        run_id: str,
        iteration: int,
        toolkit: base_toolkit,
        emit_stream: bool,
        previous_response_id: str | None = None,
    ) -> ProviderTurnResult:
        if not self.openai_api_key:
            raise ValueError("error: openai_api_key is required for openai provider")

        openai_client = OpenAI(api_key=self.openai_api_key)
        request_payload = self._merged_payload(payload)
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "input": messages,
            **request_payload,
            "stream": True,
        }
        if previous_response_id:
            request_kwargs["previous_response_id"] = previous_response_id

        tools_json = toolkit.to_json()
        supports_tools = bool(self._model_capability("supports_tools", True))
        if tools_json and supports_tools:
            request_kwargs["tools"] = tools_json

        if response_format is not None:
            request_kwargs["response_format"] = response_format.to_openai()

        collected_chunks: list[str] = []
        completed_response = None

        with openai_client.responses.create(**request_kwargs) as stream_response:
            for chunk in stream_response:
                chunk_type = getattr(chunk, "type", None)

                if chunk_type == "response.output_text.delta":
                    delta = getattr(chunk, "delta", "") or ""
                    if delta:
                        collected_chunks.append(delta)
                        if verbose and clear_output is not None:
                            clear_output(wait=True)
                            print("".join(collected_chunks))
                        if emit_stream:
                            self._emit(
                                callback,
                                "token_delta",
                                run_id,
                                iteration=iteration,
                                provider="openai",
                                delta=delta,
                                accumulated_text="".join(collected_chunks),
                            )
                elif chunk_type == "response.error":
                    raise ValueError("error: LLM text generation failed. ( agent -> _openai_fetch_once )")
                elif chunk_type == "response.completed":
                    completed_response = getattr(chunk, "response", None)

        if completed_response is None:
            raise ValueError("error: openai stream ended without completion payload")

        outputs = getattr(completed_response, "output", None) or []
        response_id = getattr(completed_response, "id", None)
        usage = getattr(completed_response, "usage", None)
        consumed_tokens = self._extract_openai_consumed_tokens(usage)
        context_window_tokens = self._extract_openai_input_tokens(usage)

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
                        call_id=call_id,
                        name=item.get("name", ""),
                        arguments=item.get("arguments", "{}"),
                    )
                )
                assistant_messages.append(item)
                continue

            if item_type == "message":
                text = self._extract_openai_message_text(item)
                if text:
                    assistant_messages.append({"role": "assistant", "content": text})
                    final_text_parts.append(text)
                else:
                    assistant_messages.append(item)
                continue

            if item_type == "reasoning":
                reasoning_items.append(item)
                assistant_messages.append(item)
                continue

            assistant_messages.append(item)

        if not tool_calls and not final_text_parts and collected_chunks:
            full_text = "".join(collected_chunks)
            assistant_messages.append({"role": "assistant", "content": full_text})
            final_text_parts.append(full_text)

        return ProviderTurnResult(
            assistant_messages=assistant_messages,
            tool_calls=tool_calls,
            final_text="".join(final_text_parts).strip(),
            response_id=response_id,
            reasoning_items=reasoning_items or None,
            consumed_tokens=consumed_tokens,
            context_window_tokens=context_window_tokens,
        )

    def _ollama_fetch_once(
        self,
        *,
        messages: list[dict[str, Any]],
        payload: dict[str, Any],
        response_format: response_format | None,
        callback: Callable[[dict[str, Any]], None] | None,
        verbose: bool,
        run_id: str,
        iteration: int,
        toolkit: base_toolkit,
        emit_stream: bool,
    ) -> ProviderTurnResult:
        request_body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }

        tools = []
        for tool in toolkit.to_json():
            if tool.get("type") == "function":
                fn = {k: v for k, v in tool.items() if k != "type"}
                tools.append({"type": "function", "function": fn})
            else:
                tools.append(tool)

        if tools:
            request_body["tools"] = tools
            request_body["tool_choice"] = "auto"

        merged_payload = self._merged_payload(payload)
        if merged_payload:
            request_body["options"] = merged_payload

        if response_format is not None:
            request_body["format"] = response_format.to_ollama()

        collected_chunks: list[str] = []
        latest_prompt_eval_count = 0
        latest_eval_count = 0

        with httpx.stream("POST", "http://localhost:11434/api/chat", json=request_body, timeout=None) as response:
            if response.status_code >= 400:
                detail = response.read().decode()
                raise ValueError(f"error: {detail} ( agent -> _ollama_fetch_once )")
            response.raise_for_status()

            for line in response.iter_lines():
                if not line:
                    continue

                data = json.loads(line)
                if data.get("error"):
                    raise ValueError(f"error: {data['error']} ( agent -> _ollama_fetch_once )")
                if isinstance(data.get("prompt_eval_count"), int):
                    latest_prompt_eval_count = data["prompt_eval_count"]
                if isinstance(data.get("eval_count"), int):
                    latest_eval_count = data["eval_count"]

                message = data.get("message") or {}
                delta = message.get("content", "") or message.get("thinking", "")
                if delta:
                    collected_chunks.append(delta)
                    if verbose and clear_output is not None:
                        clear_output(wait=True)
                        print("".join(collected_chunks))
                    if emit_stream:
                        self._emit(
                            callback,
                            "token_delta",
                            run_id,
                            iteration=iteration,
                            provider="ollama",
                            delta=delta,
                            accumulated_text="".join(collected_chunks),
                        )

                raw_tool_calls = message.get("tool_calls") or []
                if raw_tool_calls:
                    assistant_message = {
                        "role": "assistant",
                        "content": message.get("content", ""),
                        "tool_calls": raw_tool_calls,
                    }
                    tool_calls: list[ToolCall] = []
                    for raw_tool_call in raw_tool_calls:
                        fn = raw_tool_call.get("function", {}) or {}
                        tool_calls.append(
                            ToolCall(
                                call_id=raw_tool_call.get("id") or str(uuid.uuid4()),
                                name=fn.get("name", ""),
                                arguments=fn.get("arguments", {}),
                            )
                        )

                    return ProviderTurnResult(
                        assistant_messages=[assistant_message],
                        tool_calls=tool_calls,
                        final_text="",
                        consumed_tokens=latest_prompt_eval_count + latest_eval_count,
                        context_window_tokens=latest_prompt_eval_count,
                    )

                if data.get("done", False):
                    full_message = message.get("content") or "".join(collected_chunks)
                    return ProviderTurnResult(
                        assistant_messages=[{"role": "assistant", "content": full_message}],
                        tool_calls=[],
                        final_text=full_message,
                        consumed_tokens=latest_prompt_eval_count + latest_eval_count,
                        context_window_tokens=latest_prompt_eval_count,
                    )

        raise ValueError("error: unexpected termination of ollama stream. ( agent -> _ollama_fetch_once )")

    def _execute_tool_calls(
        self,
        *,
        tool_calls: list[ToolCall],
        run_id: str,
        iteration: int,
        callback: Callable[[dict[str, Any]], None] | None,
    ) -> tuple[list[dict[str, Any]], bool]:
        result_messages: list[dict[str, Any]] = []
        should_observe = False

        for tool_call in tool_calls:
            self._emit(
                callback,
                "tool_call",
                run_id,
                iteration=iteration,
                tool_name=tool_call.name,
                call_id=tool_call.call_id,
                arguments=tool_call.arguments,
            )

            tool = self.toolkit.get(tool_call.name)
            if tool is not None and tool.observe:
                should_observe = True

            tool_result = self.toolkit.execute(tool_call.name, tool_call.arguments)
            content = json.dumps(tool_result, default=str, ensure_ascii=False)

            if self.provider == "openai":
                tool_message = {
                    "type": "function_call_output",
                    "call_id": tool_call.call_id,
                    "output": content,
                }
            else:
                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call.call_id,
                    "content": content,
                }

            result_messages.append(tool_message)

            self._emit(
                callback,
                "tool_result",
                run_id,
                iteration=iteration,
                tool_name=tool_call.name,
                call_id=tool_call.call_id,
                result=tool_result,
            )

        return result_messages, should_observe

    def _observe_tool_batch(
        self,
        *,
        full_messages: list[dict[str, Any]],
        tool_messages: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> tuple[str, int]:
        observe_messages = self._build_observation_messages(full_messages, tool_messages)
        observe_payload = self._build_observation_payload(payload)

        observe_turn = self._fetch_once(
            messages=observe_messages,
            payload=observe_payload,
            response_format=None,
            callback=None,
            verbose=False,
            run_id="observe",
            iteration=0,
            toolkit=base_toolkit(),
            emit_stream=False,
            previous_response_id=None,
        )

        if observe_turn.final_text:
            return observe_turn.final_text.strip(), (int(observe_turn.consumed_tokens or 0), int(observe_turn.context_window_tokens or 0))

        return self._last_assistant_text(observe_turn.assistant_messages).strip(), (int(observe_turn.consumed_tokens or 0), int(observe_turn.context_window_tokens or 0))

    def _build_observation_messages(
        self,
        full_messages: list[dict[str, Any]],
        tool_messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        recent = full_messages[-OBSERVATION_RECENT_MESSAGES:]
        observe_messages: list[dict[str, Any]] = [
            {"role": "system", "content": OBSERVATION_SYSTEM_PROMPT},
            *recent,
            *tool_messages,
            {
                "role": "user",
                "content": "Review the LAST tool result above and provide one brief actionable observation.",
            },
        ]
        return observe_messages

    def _build_observation_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        observe_payload = dict(payload or {})
        observe_payload["temperature"] = 0.2
        if self.provider == "openai":
            observe_payload["max_output_tokens"] = OBSERVATION_MAX_OUTPUT_TOKENS
        if self.provider == "ollama":
            observe_payload["num_predict"] = OBSERVATION_MAX_OUTPUT_TOKENS
        return observe_payload

    def _inject_observation(self, tool_message: dict[str, Any], observation: str):
        content_key = "content" if "content" in tool_message else "output"
        existing = tool_message.get(content_key, "")

        try:
            parsed = json.loads(existing) if isinstance(existing, str) and existing.strip() else {}
            if not isinstance(parsed, dict):
                parsed = {"result": parsed}
            parsed["observation"] = observation
            tool_message[content_key] = json.dumps(parsed, default=str, ensure_ascii=False)
        except Exception:
            suffix = f"\n[OBSERVATION] {observation}"
            tool_message[content_key] = f"{existing}{suffix}" if existing else suffix.strip()

    def _apply_response_format(
        self,
        messages: list[dict[str, Any]],
        response_format: response_format | None,
    ):
        if response_format is None:
            return

        for message in reversed(messages):
            if message.get("role") != "assistant":
                continue
            raw_content = message.get("content", "")
            parsed = response_format.parse(raw_content)
            message["content"] = json.dumps(parsed, ensure_ascii=False)
            return

    def _extract_openai_message_text(self, item: dict[str, Any]) -> str:
        content = item.get("content") or []
        text_parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in ("output_text", "text"):
                text = block.get("text", "")
                if text:
                    text_parts.append(text)
        return "".join(text_parts)

    def _as_dict(self, obj: Any) -> dict[str, Any]:
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        return {"value": str(obj)}

    def _extract_openai_input_tokens(self, usage: Any) -> int:
        if usage is None:
            return 0
        usage_dict = self._as_dict(usage)
        input_tokens = usage_dict.get("input_tokens")
        if isinstance(input_tokens, int):
            return max(0, input_tokens)
        prompt_tokens = usage_dict.get("prompt_tokens")
        if isinstance(prompt_tokens, int):
            return max(0, prompt_tokens)
        return 0

    def _extract_openai_consumed_tokens(self, usage: Any) -> int:
        if usage is None:
            return 0

        usage_dict = self._as_dict(usage)
        total_tokens = usage_dict.get("total_tokens")
        if isinstance(total_tokens, int):
            return max(0, total_tokens)

        input_tokens = usage_dict.get("input_tokens")
        output_tokens = usage_dict.get("output_tokens")
        if isinstance(input_tokens, int) and isinstance(output_tokens, int):
            return max(0, input_tokens + output_tokens)
        return 0

    def _last_assistant_text(self, messages: list[dict[str, Any]]) -> str:
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                return (msg.get("content") or "").strip()
        return ""

__all__ = [
    "agent",
    "response_format",
]
