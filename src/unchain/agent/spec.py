from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


MissingToolPolicy = Literal["raise", "warn_skip"]


@dataclass(frozen=True)
class AgentSpec:
    name: str
    instructions: str = ""
    provider: str = "openai"
    model: str = "gpt-5"
    api_key: str | None = None
    modules: tuple[Any, ...] = ()
    allowed_tools: tuple[str, ...] | None = None
    missing_tool_policy: MissingToolPolicy = "raise"


@dataclass
class AgentState:
    module_state: dict[str, Any] = field(default_factory=dict)
