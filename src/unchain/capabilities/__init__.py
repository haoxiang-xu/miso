from __future__ import annotations

import copy
from abc import ABC
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class ContextTarget(str, Enum):
    CONVERSATION = "conversation"
    MODEL_CONTEXT = "model_context"
    RUNTIME_STATE = "runtime_state"
    ARTIFACTS = "artifacts"
    EVENTS = "events"


@dataclass(frozen=True)
class InsertMessagesOp:
    target: ContextTarget
    index: int
    messages: list[dict[str, Any]]
    reason: str = ""


@dataclass(frozen=True)
class PatchMessageOp:
    target: ContextTarget
    selector: Any
    patch: dict[str, Any]
    reason: str = ""


@dataclass(frozen=True)
class DeleteMessagesOp:
    target: ContextTarget
    selector: Any
    reason: str = ""


@dataclass(frozen=True)
class SetRuntimeStateOp:
    path: tuple[str, ...]
    value: Any
    reason: str = ""


@dataclass(frozen=True)
class CreateArtifactOp:
    artifact: dict[str, Any]
    reason: str = ""


@dataclass(frozen=True)
class EmitEventOp:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class RequestSuspendOp:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_suspend_signal(self):
        from ..kernel.delta import SuspendSignal

        return SuspendSignal(
            kind=self.kind,
            payload=copy.deepcopy(self.payload),
        )


ContextOp = (
    InsertMessagesOp
    | PatchMessageOp
    | DeleteMessagesOp
    | SetRuntimeStateOp
    | CreateArtifactOp
    | EmitEventOp
    | RequestSuspendOp
)


@dataclass(frozen=True)
class RunDelta(ABC):
    created_by: str
    base_version_id: str | None = None
    rebase_to_latest: bool = True
    context_ops: tuple[ContextOp, ...] = ()
    ops: tuple[Any, ...] = ()
    state_updates: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    suspend: Any = None


@dataclass(frozen=True)
class CapabilityOutcome:
    value: Any = None
    delta: RunDelta | None = None
    created_by: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def result(self) -> Any:
        return self.value


@dataclass(frozen=True)
class RunContext:
    messages: list[dict[str, Any]] = field(default_factory=list)
    phase: str = ""
    event: dict[str, Any] = field(default_factory=dict)
    state: Any = None
    latest_version_id: str | None = None

    @classmethod
    def from_harness_context(cls, context: Any) -> "RunContext":
        latest_messages = context.latest_messages() if hasattr(context, "latest_messages") else []
        event_payload = context.event_payload() if hasattr(context, "event_payload") else {}
        return cls(
            messages=copy.deepcopy(latest_messages),
            phase=str(getattr(context, "phase", "") or ""),
            event=copy.deepcopy(event_payload) if isinstance(event_payload, dict) else {},
            state=getattr(context, "state", None),
            latest_version_id=getattr(context, "latest_version_id", None),
        )


@runtime_checkable
class ActiveCapability(Protocol):
    name: str

    def invoke(self, call: Any, context: RunContext) -> CapabilityOutcome | RunDelta | dict[str, Any] | None:
        ...


def normalize_run_delta(delta: Any, *, created_by: str = "") -> RunDelta | None:
    if delta is None:
        return None
    if isinstance(delta, RunDelta):
        if created_by and not getattr(delta, "created_by", "") and isinstance(delta, RunDelta):
            try:
                return replace(delta, created_by=created_by)
            except TypeError:
                pass
        return delta
    if isinstance(delta, dict):
        return RunDelta(
            created_by=created_by,
            state_updates=copy.deepcopy(delta.get("state_updates", {}))
            if isinstance(delta.get("state_updates"), dict)
            else {},
            trace=copy.deepcopy(delta.get("trace", {})) if isinstance(delta.get("trace"), dict) else {},
        )
    raise TypeError(f"expected RunDelta-compatible value, got {type(delta).__name__}")


def normalize_capability_outcome(
    outcome: Any,
    *,
    created_by: str = "",
) -> CapabilityOutcome:
    if isinstance(outcome, CapabilityOutcome):
        delta = normalize_run_delta(outcome.delta, created_by=created_by) if outcome.delta is not None else None
        if created_by and not outcome.created_by:
            return replace(outcome, created_by=created_by, delta=delta)
        if delta is not outcome.delta:
            return replace(outcome, delta=delta)
        return outcome

    if isinstance(outcome, RunDelta):
        return CapabilityOutcome(
            delta=normalize_run_delta(outcome, created_by=created_by),
            created_by=created_by,
        )

    return CapabilityOutcome(
        value=_copy_outcome_value(outcome),
        delta=None,
        created_by=created_by,
    )


def _copy_outcome_value(value: Any) -> Any:
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


from ..kernel.delta import HarnessDelta  # noqa: E402
from ..kernel.harness import RuntimeHarness as PassiveCapability  # noqa: E402

RuntimeHook = PassiveCapability
RunDelta.register(HarnessDelta)


__all__ = [
    "ActiveCapability",
    "CapabilityOutcome",
    "ContextOp",
    "ContextTarget",
    "CreateArtifactOp",
    "DeleteMessagesOp",
    "EmitEventOp",
    "InsertMessagesOp",
    "PatchMessageOp",
    "PassiveCapability",
    "RequestSuspendOp",
    "RunContext",
    "RunDelta",
    "RuntimeHook",
    "SetRuntimeStateOp",
    "normalize_capability_outcome",
    "normalize_run_delta",
]
