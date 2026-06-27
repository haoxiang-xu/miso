from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from ..schemas import ResponseFormat
from ..tools.toolkit import Toolkit
from .types import ModelTurnResult


def _deepcopy_messages(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [copy.deepcopy(message) for message in (messages or []) if isinstance(message, dict)]


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
    provider: str

    def fetch_turn(self, request: ModelTurnRequest) -> ModelTurnResult:
        ...


ModelAdapter = ModelIO
