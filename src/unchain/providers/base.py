from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from ..kernel.types import ModelTurnResult, TokenUsage, ToolCall
from ..schemas import ResponseFormat
from ..tools.toolkit import Toolkit


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
    fallback_messages: list[dict[str, Any]] | None = None
    context_mode: str = "semantic"
    internal_context_composition_v1: Mapping[str, Any] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.internal_context_composition_v1 is None:
            return
        from ..context.composition import freeze_internal_context_composition

        object.__setattr__(
            self,
            "internal_context_composition_v1",
            freeze_internal_context_composition(
                self.internal_context_composition_v1
            ),
        )

    def __deepcopy__(self, memo: dict[int, Any]) -> "ModelTurnRequest":
        """Copy the mutable fields; share the frozen composition manifest.

        The manifest is a deeply immutable MappingProxyType, which cannot be
        deepcopied at all — and does not need to be, since nothing can mutate
        it. Copying the request used to work only because the manifest was
        usually absent; now that every turn carries one, the request has to say
        how it is copied rather than fail on the field.
        """

        from dataclasses import fields as dataclass_fields

        copied = self.__class__.__new__(self.__class__)
        memo[id(self)] = copied
        for entry in dataclass_fields(self):
            value = getattr(self, entry.name)
            object.__setattr__(
                copied,
                entry.name,
                value
                if entry.name == "internal_context_composition_v1"
                else copy.deepcopy(value, memo),
            )
        return copied

    def copied_messages(self) -> list[dict[str, Any]]:
        return _deepcopy_messages(self.messages)


@runtime_checkable
class ModelIO(Protocol):
    """Provider-facing model adapter contract used by the run loop."""

    provider: str

    def fetch_turn(self, request: ModelTurnRequest) -> ModelTurnResult:
        ...


ModelAdapter = ModelIO


__all__ = [
    "ModelAdapter",
    "ModelIO",
    "ModelTurnRequest",
    "ModelTurnResult",
    "TokenUsage",
    "ToolCall",
]
