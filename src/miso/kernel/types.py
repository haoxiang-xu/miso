from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any] | str | None


@dataclass(frozen=True)
class TokenUsage:
    consumed_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ModelTurnResult:
    assistant_messages: list[dict[str, Any]]
    tool_calls: list[ToolCall]
    final_text: str = ""
    response_id: str | None = None
    reasoning_items: list[dict[str, Any]] | None = None
    consumed_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
