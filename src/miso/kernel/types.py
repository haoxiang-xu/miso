from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from ..input.human_input import HumanInputRequest


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


@dataclass
class ToolBatchState:
    result_messages: list[dict[str, Any]] = field(default_factory=list)
    should_observe: bool = False
    awaiting_human_input: bool = False
    human_input_request: HumanInputRequest | None = None
    human_input_tool_call_id: str | None = None
    executed_call_ids: list[str] = field(default_factory=list)

    def copy(self) -> "ToolBatchState":
        return ToolBatchState(
            result_messages=copy.deepcopy(self.result_messages),
            should_observe=bool(self.should_observe),
            awaiting_human_input=bool(self.awaiting_human_input),
            human_input_request=copy.deepcopy(self.human_input_request),
            human_input_tool_call_id=self.human_input_tool_call_id,
            executed_call_ids=list(self.executed_call_ids),
        )


@dataclass(frozen=True)
class KernelRunResult:
    messages: list[dict[str, Any]]
    status: str
    continuation: dict[str, Any] | None = None
    human_input_request: dict[str, Any] | None = None
    consumed_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    last_turn_tokens: int = 0
    last_turn_input_tokens: int = 0
    last_turn_output_tokens: int = 0
    previous_response_id: str | None = None
    iteration: int = 0
