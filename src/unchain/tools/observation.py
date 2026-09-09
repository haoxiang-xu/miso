from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from ..durability import is_durable_persistence_failure
from ..execution import ExecutionLeaseError
from ..kernel.model_io import ModelTurnRequest
from ..kernel.types import TokenUsage
from .toolkit import Toolkit


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


def _last_assistant_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages or []):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def build_observation_payload(
    payload: dict[str, Any],
    *,
    provider: str | None,
) -> dict[str, Any]:
    observe_payload = dict(payload or {})
    observe_payload["temperature"] = 0.2
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider in {"anthropic", "hyperspace"}:
        observe_payload["max_tokens"] = OBSERVATION_MAX_OUTPUT_TOKENS
        observe_payload.pop("max_output_tokens", None)
        observe_payload.pop("num_predict", None)
        return observe_payload
    if normalized_provider == "ollama":
        observe_payload["num_predict"] = OBSERVATION_MAX_OUTPUT_TOKENS
        observe_payload.pop("max_output_tokens", None)
        observe_payload.pop("max_tokens", None)
        return observe_payload
    observe_payload["max_output_tokens"] = OBSERVATION_MAX_OUTPUT_TOKENS
    observe_payload.pop("max_tokens", None)
    observe_payload.pop("num_predict", None)
    return observe_payload


def _infer_provider(model_io: Any) -> str | None:
    provider = getattr(model_io, "provider", None)
    if isinstance(provider, str) and provider.strip():
        return provider.strip()
    if hasattr(model_io, "engine"):
        engine = getattr(model_io, "engine", None)
        engine_provider = getattr(engine, "provider", None)
        if isinstance(engine_provider, str) and engine_provider.strip():
            return engine_provider.strip()
    if model_io is not None and model_io.__class__.__name__ == "OpenAIModelIO":
        return "openai"
    return None


@dataclass(frozen=True)
class ToolObservationRunner:
    model_io: Any = None

    def observe_tool_batch(
        self,
        *,
        full_messages: list[dict[str, Any]],
        tool_messages: list[dict[str, Any]],
        payload: dict[str, Any],
        iteration: int = 0,
        provider: str | None = None,
        on_model_turn: Callable[[Any, ModelTurnRequest, str], None] | None = None,
        on_model_failure: Callable[
            [BaseException, ModelTurnRequest, str], None
        ]
        | None = None,
        fetch_model_turn: Callable[[ModelTurnRequest], Any] | None = None,
    ) -> tuple[str, TokenUsage]:
        if self.model_io is None:
            return "", TokenUsage()
        observe_messages = [
            {"role": "system", "content": OBSERVATION_SYSTEM_PROMPT},
            *copy.deepcopy(list(full_messages or [])[-OBSERVATION_RECENT_MESSAGES:]),
            *copy.deepcopy(tool_messages),
            {
                "role": "user",
                "content": "Review the LAST tool result above and provide one brief actionable observation.",
            },
        ]
        observe_payload = build_observation_payload(
            payload or {},
            provider=provider or _infer_provider(self.model_io),
        )
        request = ModelTurnRequest(
            messages=observe_messages,
            payload=observe_payload,
            response_format=None,
            callback=None,
            verbose=False,
            run_id="observe",
            iteration=iteration,
            toolkit=Toolkit(),
            emit_stream=False,
            previous_response_id=None,
        )
        occurred_at = (
            datetime.now(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        try:
            turn = (
                fetch_model_turn(request)
                if fetch_model_turn is not None
                else self.model_io.fetch_turn(request)
            )
        except ExecutionLeaseError as exc:
            if on_model_failure is not None:
                on_model_failure(exc, request, occurred_at)
            raise
        except Exception as exc:
            if on_model_failure is not None:
                on_model_failure(exc, request, occurred_at)
            if is_durable_persistence_failure(exc):
                raise
            return "", TokenUsage()
        if on_model_turn is not None:
            on_model_turn(turn, request, occurred_at)
        observation = (turn.final_text or _last_assistant_text(turn.assistant_messages)).strip()
        return observation, TokenUsage(
            consumed_tokens=int(turn.consumed_tokens or 0),
            input_tokens=int(turn.input_tokens or 0),
            output_tokens=int(turn.output_tokens or 0),
        )


def inject_observation(tool_message: dict, observation: str) -> None:
    def _inject_text_payload(existing_payload):
        try:
            parsed = (
                json.loads(existing_payload)
                if isinstance(existing_payload, str) and existing_payload.strip()
                else {}
            )
            if not isinstance(parsed, dict):
                parsed = {"result": parsed}
            parsed["observation"] = observation
            return json.dumps(parsed, default=str, ensure_ascii=False)
        except Exception:
            suffix = f"\n[OBSERVATION] {observation}"
            return (
                f"{existing_payload}{suffix}"
                if existing_payload
                else suffix.strip()
            )

    content = tool_message.get("content")
    if isinstance(content, list):
        for block in reversed(content):
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            block["content"] = _inject_text_payload(block.get("content", ""))
            return

    content_key = "content" if "content" in tool_message else "output"
    existing = tool_message.get(content_key, "")

    tool_message[content_key] = _inject_text_payload(existing)


def observation_token_state(
    *,
    consumed_tokens: int,
    input_tokens: int,
    output_tokens: int,
    observe_usage: TokenUsage | None,
) -> dict[str, int]:
    if observe_usage is None:
        return {}
    return {
        "consumed_tokens": int(consumed_tokens or 0) + int(observe_usage.consumed_tokens or 0),
        "input_tokens": int(input_tokens or 0) + int(observe_usage.input_tokens or 0),
        "output_tokens": int(output_tokens or 0) + int(observe_usage.output_tokens or 0),
    }
