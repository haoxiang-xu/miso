from __future__ import annotations

import copy
from typing import Callable

from ..capabilities import (
    ContextTarget,
    CreateArtifactOp,
    DeleteMessagesOp,
    EmitEventOp,
    InsertMessagesOp,
    PatchMessageOp,
    RequestSuspendOp,
    RunDelta,
    SetRuntimeStateOp,
)
from .delta import HarnessDelta
from .state import RunState


def _deepcopy_messages(messages: list[dict] | None) -> list[dict]:
    return [copy.deepcopy(message) for message in (messages or []) if isinstance(message, dict)]


def apply_run_delta(
    state: RunState,
    delta: RunDelta,
    *,
    emit_event: Callable[[EmitEventOp], None] | None = None,
) -> str | None:
    if isinstance(delta, HarnessDelta):
        return state.apply_delta(delta)

    version_id: str | None = state.latest_version_id
    for op in delta.context_ops:
        if isinstance(op, InsertMessagesOp):
            version_id = _apply_insert_messages_op(state, delta, op)
            continue
        if isinstance(op, SetRuntimeStateOp):
            _apply_runtime_state_op(state, op)
            continue
        if isinstance(op, CreateArtifactOp):
            state.artifacts.append(copy.deepcopy(op.artifact))
            continue
        if isinstance(op, EmitEventOp):
            if callable(emit_event):
                emit_event(op)
            continue
        if isinstance(op, RequestSuspendOp):
            state.suspend_state.signal_kind = op.kind
            state.suspend_state.payload = copy.deepcopy(op.payload)
            continue
        if isinstance(op, (PatchMessageOp, DeleteMessagesOp)):
            raise NotImplementedError(f"{type(op).__name__} is not supported by RunDelta application yet")
        raise TypeError(f"unsupported context op: {type(op).__name__}")

    if delta.state_updates:
        state._apply_state_updates(delta.state_updates)
    if delta.suspend is not None:
        state.suspend_state.signal_kind = delta.suspend.kind
        state.suspend_state.payload = copy.deepcopy(delta.suspend.payload)
    return version_id


def _apply_insert_messages_op(
    state: RunState,
    delta: RunDelta,
    op: InsertMessagesOp,
) -> str:
    if op.target == ContextTarget.MODEL_CONTEXT:
        working_messages = state.latest_messages()
    elif op.target == ContextTarget.CONVERSATION:
        working_messages = _deepcopy_messages(state.transcript)
    else:
        raise NotImplementedError(f"InsertMessagesOp target {op.target.value!r} is not supported")

    index = max(0, min(int(op.index), len(working_messages)))
    working_messages[index:index] = _deepcopy_messages(op.messages)

    if op.target == ContextTarget.CONVERSATION:
        state.transcript = _deepcopy_messages(working_messages)
    if op.target == ContextTarget.MODEL_CONTEXT:
        state.next_model_input = _deepcopy_messages(working_messages)

    version = state.versions.create_version(
        messages=working_messages,
        parent_version_id=state.latest_version_id,
        created_by=delta.created_by,
        metadata={
            "target": op.target.value,
            "op_type": type(op).__name__,
            "reason": op.reason,
            "trace": copy.deepcopy(delta.trace),
        },
    )
    state.latest_version_id = version.version_id
    return version.version_id


def _apply_runtime_state_op(state: RunState, op: SetRuntimeStateOp) -> None:
    path = tuple(str(part) for part in op.path if str(part))
    if not path:
        raise ValueError("SetRuntimeStateOp.path must not be empty")

    head = path[0]
    value = copy.deepcopy(op.value)
    if len(path) == 1 and hasattr(state, head):
        setattr(state, head, value)
        return

    if hasattr(state, head):
        root = getattr(state, head)
        if isinstance(root, dict):
            _set_nested_value(root, path[1:], value)
            return

    bucket = state.component_bucket(head)
    if len(path) == 1:
        bucket["value"] = value
        return
    _set_nested_value(bucket, path[1:], value)


def _set_nested_value(root: dict, path: tuple[str, ...], value) -> None:
    if not path:
        raise ValueError("nested state path must not be empty")
    cursor = root
    for key in path[:-1]:
        child = cursor.get(key)
        if not isinstance(child, dict):
            child = {}
            cursor[key] = child
        cursor = child
    cursor[path[-1]] = value
