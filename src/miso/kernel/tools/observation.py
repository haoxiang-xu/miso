from __future__ import annotations

import json

from ..types import TokenUsage


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


def inject_observation(tool_message: dict, observation: str) -> None:
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
