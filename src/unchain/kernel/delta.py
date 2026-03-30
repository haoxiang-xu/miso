from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


def _deepcopy_messages(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [copy.deepcopy(message) for message in (messages or []) if isinstance(message, dict)]


@dataclass(frozen=True)
class AppendMessagesOp:
    messages: list[dict[str, Any]]


@dataclass(frozen=True)
class InsertMessagesOp:
    index: int
    messages: list[dict[str, Any]]


@dataclass(frozen=True)
class ReplaceSpanOp:
    start: int
    end: int
    messages: list[dict[str, Any]]


@dataclass(frozen=True)
class DeleteSpanOp:
    start: int
    end: int


@dataclass(frozen=True)
class ActivateVersionOp:
    version_id: str


MessageListOp = (
    AppendMessagesOp
    | InsertMessagesOp
    | ReplaceSpanOp
    | DeleteSpanOp
    | ActivateVersionOp
)


@dataclass(frozen=True)
class SuspendSignal:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessDelta:
    """A constrained state/message delta emitted by one harness."""

    created_by: str
    base_version_id: str | None = None
    rebase_to_latest: bool = True
    ops: tuple[MessageListOp, ...] = ()
    state_updates: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    suspend: SuspendSignal | None = None

    @classmethod
    def append(
        cls,
        *,
        created_by: str,
        messages: list[dict[str, Any]],
        base_version_id: str | None = None,
        rebase_to_latest: bool = True,
        state_updates: dict[str, Any] | None = None,
        trace: dict[str, Any] | None = None,
        suspend: SuspendSignal | None = None,
    ) -> "HarnessDelta":
        return cls(
            created_by=created_by,
            base_version_id=base_version_id,
            rebase_to_latest=rebase_to_latest,
            ops=(AppendMessagesOp(messages=_deepcopy_messages(messages)),),
            state_updates=copy.deepcopy(state_updates) if isinstance(state_updates, dict) else {},
            trace=copy.deepcopy(trace) if isinstance(trace, dict) else {},
            suspend=suspend,
        )
