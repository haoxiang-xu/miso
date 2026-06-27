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
        if isinstance(op, PatchMessageOp):
            version_id = _apply_patch_message_op(state, delta, op)
            continue
        if isinstance(op, DeleteMessagesOp):
            version_id = _apply_delete_messages_op(state, delta, op)
            continue
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
    working_messages = _messages_for_target(state, op.target, type(op).__name__)
    index = max(0, min(int(op.index), len(working_messages)))
    working_messages[index:index] = _deepcopy_messages(op.messages)

    return _commit_messages_for_target(
        state,
        delta,
        op.target,
        working_messages,
        op_type=type(op).__name__,
        reason=op.reason,
    )


def _apply_patch_message_op(
    state: RunState,
    delta: RunDelta,
    op: PatchMessageOp,
) -> str | None:
    working_messages = _messages_for_target(state, op.target, type(op).__name__)
    indices = _resolve_message_indices(working_messages, op.selector)
    if not indices:
        return state.latest_version_id

    patch = copy.deepcopy(op.patch)
    for index in indices:
        working_messages[index] = {
            **copy.deepcopy(working_messages[index]),
            **patch,
        }

    return _commit_messages_for_target(
        state,
        delta,
        op.target,
        working_messages,
        op_type=type(op).__name__,
        reason=op.reason,
    )


def _apply_delete_messages_op(
    state: RunState,
    delta: RunDelta,
    op: DeleteMessagesOp,
) -> str | None:
    working_messages = _messages_for_target(state, op.target, type(op).__name__)
    indices = _resolve_message_indices(working_messages, op.selector)
    if not indices:
        return state.latest_version_id

    for index in sorted(indices, reverse=True):
        del working_messages[index]

    return _commit_messages_for_target(
        state,
        delta,
        op.target,
        working_messages,
        op_type=type(op).__name__,
        reason=op.reason,
    )


def _messages_for_target(
    state: RunState,
    target: ContextTarget,
    op_type: str,
) -> list[dict]:
    if target == ContextTarget.MODEL_CONTEXT:
        return state.latest_messages()
    if target == ContextTarget.CONVERSATION:
        return _deepcopy_messages(state.transcript)
    raise NotImplementedError(f"{op_type} target {target.value!r} is not supported")


def _commit_messages_for_target(
    state: RunState,
    delta: RunDelta,
    target: ContextTarget,
    working_messages: list[dict],
    *,
    op_type: str,
    reason: str,
) -> str:
    if target == ContextTarget.CONVERSATION:
        state.transcript = _deepcopy_messages(working_messages)
    if target == ContextTarget.MODEL_CONTEXT:
        state.next_model_input = _deepcopy_messages(working_messages)

    version = state.versions.create_version(
        messages=working_messages,
        parent_version_id=state.latest_version_id,
        created_by=delta.created_by,
        metadata={
            "target": target.value,
            "op_type": op_type,
            "reason": reason,
            "trace": copy.deepcopy(delta.trace),
        },
    )
    state.latest_version_id = version.version_id
    return version.version_id


def _resolve_message_indices(messages: list[dict], selector) -> list[int]:
    if isinstance(selector, int):
        return [_normalize_message_index(selector, len(messages))]

    if isinstance(selector, dict):
        if "index" in selector:
            return [_normalize_message_index(int(selector["index"]), len(messages))]
        if "indices" in selector:
            return _unique_indices(
                _normalize_message_index(int(index), len(messages))
                for index in selector["indices"]
            )
        if "start" in selector or "end" in selector:
            start = int(selector.get("start", 0))
            end = int(selector.get("end", len(messages)))
            if start < 0:
                start += len(messages)
            if end < 0:
                end += len(messages)
            start = max(0, min(start, len(messages)))
            end = max(start, min(end, len(messages)))
            return list(range(start, end))
        return [
            index
            for index, message in enumerate(messages)
            if all(message.get(key) == value for key, value in selector.items())
        ]

    if isinstance(selector, (list, tuple, set)):
        return _unique_indices(
            _normalize_message_index(int(index), len(messages))
            for index in selector
        )

    raise TypeError(f"unsupported message selector: {type(selector).__name__}")


def _normalize_message_index(index: int, length: int) -> int:
    resolved_index = index + length if index < 0 else index
    if resolved_index < 0 or resolved_index >= length:
        raise IndexError(f"message index {index} is out of range")
    return resolved_index


def _unique_indices(indices) -> list[int]:
    resolved: list[int] = []
    seen: set[int] = set()
    for index in indices:
        if index in seen:
            continue
        seen.add(index)
        resolved.append(index)
    return resolved


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
