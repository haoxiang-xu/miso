from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

from ..schemas import ResponseFormat
from ..tools.toolkit import Toolkit
from .types import ModelTurnResult, ToolCall

if TYPE_CHECKING:
    from ..runtime import Broth


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
