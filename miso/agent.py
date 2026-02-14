import copy
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import httpx
from openai import OpenAI

from .response_format import response_format
from .tool import toolkit
from .predefined_tools import predefined_toolkit

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

class agent:
    def __init__(self):
        self.retrieval_mode = "force_retrieve"  # reserved for future retrieval strategy
        self.openai_api_key = None
        self.openai_base_url = None
        self.provider = "openai"
        self.model = "gpt-4.1"
        self.max_iterations = 6
        self.default_payload = {
            "gpt-4.1": {
                "instructions": "",
                "temperature": 0.7,
                "top_p": 1,
                "max_output_tokens": 2048,
                "truncation": "auto",  # options: "auto", "disabled"
            }
        }
        self.toolkit = toolkit()

    def use_predefined_toolkit(
        self,
        *,
        workspace_root: str | None = None,
        include_python_runtime: bool = True,
    ) -> predefined_toolkit:
        toolkit = predefined_toolkit(
            workspace_root=workspace_root,
            include_python_runtime=include_python_runtime,
        )
        self.toolkit = toolkit
        return toolkit

    def chat_completion(
        self,
        messages,
        payload: dict[str, Any] | None = None,
        response_format: response_format | None = None,
        callback: Callable[[dict[str, Any]], None] | None = None,
        verbose: bool = False,
        max_iterations: int | None = None,
    ):
        return self.run(
            messages=messages,
            payload=payload,
            response_format=response_format,
            callback=callback,
            verbose=verbose,
            max_iterations=max_iterations,
        )

    def run(
        self,
        messages,
        payload: dict[str, Any] | None = None,
        response_format: response_format | None = None,
        callback: Callable[[dict[str, Any]], None] | None = None,
        verbose: bool = False,
        max_iterations: int | None = None,
    ):
        run_id = str(uuid.uuid4())
        conversation = copy.deepcopy(list(messages or []))
        payload = dict(payload or {})
        max_loops = max_iterations or self.max_iterations

        self._emit(callback, "run_started", run_id, iteration=0, provider=self.provider, model=self.model)

        for iteration in range(max_loops):
            self._emit(callback, "iteration_started", run_id, iteration=iteration)

            turn = self._fetch_once(
                messages=conversation,
                payload=payload,
                response_format=response_format,
                callback=callback,
                verbose=verbose,
                run_id=run_id,
                iteration=iteration,
                toolkit=self.toolkit,
                emit_stream=True,
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
                    observation = self._observe_tool_batch(
                        full_messages=conversation,
                        tool_messages=tool_messages,
                        payload=payload,
                    )
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
            return conversation

        self._emit(callback, "run_max_iterations", run_id, iteration=max_loops)
        return conversation

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
        toolkit: toolkit,
        emit_stream: bool,
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
        if self.provider == "openai":
            merged = copy.deepcopy(self.default_payload.get(self.model, {}) or {})
        else:
            merged = {}
        merged.update(payload or {})
        return merged

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
        toolkit: toolkit,
        emit_stream: bool,
    ) -> ProviderTurnResult:
        if not self.openai_api_key:
            raise ValueError("error: openai_api_key is required for openai provider")

        client_kwargs: dict[str, Any] = {"api_key": self.openai_api_key}
        if self.openai_base_url:
            client_kwargs["base_url"] = self.openai_base_url
        openai_client = OpenAI(**client_kwargs)
        request_payload = self._merged_payload(payload)
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "input": messages,
            **request_payload,
            "stream": True,
        }

        tools_json = toolkit.to_json()
        if tools_json:
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

        assistant_messages: list[dict[str, Any]] = []
        tool_calls: list[ToolCall] = []
        final_text_parts: list[str] = []

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

            assistant_messages.append(item)

        if not tool_calls and not final_text_parts and collected_chunks:
            full_text = "".join(collected_chunks)
            assistant_messages.append({"role": "assistant", "content": full_text})
            final_text_parts.append(full_text)

        return ProviderTurnResult(
            assistant_messages=assistant_messages,
            tool_calls=tool_calls,
            final_text="".join(final_text_parts).strip(),
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
        toolkit: toolkit,
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
                    )

                if data.get("done", False):
                    full_message = message.get("content") or "".join(collected_chunks)
                    return ProviderTurnResult(
                        assistant_messages=[{"role": "assistant", "content": full_message}],
                        tool_calls=[],
                        final_text=full_message,
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
    ) -> str:
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
            toolkit=toolkit(),
            emit_stream=False,
        )

        if observe_turn.final_text:
            return observe_turn.final_text.strip()

        return self._last_assistant_text(observe_turn.assistant_messages).strip()

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

    def _last_assistant_text(self, messages: list[dict[str, Any]]) -> str:
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                return (msg.get("content") or "").strip()
        return ""

__all__ = [
    "agent",
    "response_format",
]
