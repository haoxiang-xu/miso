from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from ..input.human_input import HumanInputRequest


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
