from __future__ import annotations

import copy
from typing import Any

from ..capabilities import (
    ContextOp,
    ContextTarget,
    EmitEventOp,
    MergeRuntimeStateOp,
    ReplaceMessagesOp,
    RunDelta,
    SetRuntimeStateOp,
)
from ..kernel.delta import MessageListOp, ReplaceSpanOp


MEMORY_EFFECT_CREATED_BY_PREFIX = "memory."


def _copy_dict(value: dict[str, Any] | None) -> dict[str, Any]:
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def build_memory_delta(
    *,
    created_by: str,
    state_updates: dict[str, Any] | None = None,
    trace: dict[str, Any] | None = None,
    base_version_id: str | None = None,
    rebase_to_latest: bool = True,
    ops: tuple[MessageListOp, ...] = (),
) -> RunDelta:
    context_ops = (
        *_message_ops_to_context_ops(ops),
        *_state_updates_to_context_ops(state_updates),
    )
    return RunDelta(
        created_by=created_by,
        base_version_id=base_version_id,
        rebase_to_latest=rebase_to_latest,
        context_ops=context_ops,
        trace=_copy_dict(trace),
    )


def _message_ops_to_context_ops(ops: tuple[MessageListOp, ...]) -> tuple[ContextOp, ...]:
    context_ops: list[ContextOp] = []
    for op in tuple(ops or ()):
        if isinstance(op, ReplaceSpanOp):
            context_ops.append(
                ReplaceMessagesOp(
                    target=ContextTarget.MODEL_CONTEXT,
                    start=op.start,
                    end=op.end,
                    messages=copy.deepcopy(op.messages),
                    reason="memory.model_context.replace",
                )
            )
            continue
        raise TypeError(f"unsupported memory message op: {type(op).__name__}")
    return tuple(context_ops)


def _state_updates_to_context_ops(updates: dict[str, Any] | None) -> tuple[ContextOp, ...]:
    context_ops: list[ContextOp] = []
    for key, value in _copy_dict(updates).items():
        if key == "transcript":
            context_ops.append(
                ReplaceMessagesOp(
                    target=ContextTarget.CONVERSATION,
                    messages=copy.deepcopy(value) if isinstance(value, list) else [],
                    reason="memory.conversation.replace",
                )
            )
            continue
        if key == "provider_replay_frame":
            context_ops.append(
                MergeRuntimeStateOp(
                    path=("component_state", "provider_replay"),
                    value={"frame": _copy_dict(value)},
                    reason="memory.provider_replay.restore",
                )
            )
            continue
        if key in {"iteration", "next_model_input", "workspace_change_state"}:
            context_ops.append(
                SetRuntimeStateOp(
                    path=(key,),
                    value=copy.deepcopy(value),
                    reason="memory.state.restore",
                )
            )
            continue
        if key in {
            "memory_state",
            "memory_prepare_info",
            "memory_commit_info",
            "optimizer_state",
            "provider_state",
            "token_state",
        }:
            context_ops.append(
                MergeRuntimeStateOp(
                    path=(key,),
                    value=_copy_dict(value),
                    reason="memory.state.merge",
                )
            )
            continue
        raise TypeError(f"unsupported memory state update: {key}")
    return tuple(context_ops)


def memory_state_update(memory_state: dict[str, Any]) -> dict[str, Any]:
    return {"memory_state": _copy_dict(memory_state)}


def memory_prepare_update(prepare_info: dict[str, Any]) -> dict[str, Any]:
    return {"memory_prepare_info": _copy_dict(prepare_info)}


def memory_commit_update(commit_info: dict[str, Any]) -> dict[str, Any]:
    return {"memory_commit_info": _copy_dict(commit_info)}


def build_memory_prepare_event(prepare_info: dict[str, Any]) -> EmitEventOp:
    return EmitEventOp(
        type="memory_prepare",
        payload=_copy_dict(prepare_info),
        reason="memory.prepare",
    )


def build_memory_commit_event(commit_info: dict[str, Any]) -> EmitEventOp:
    return EmitEventOp(
        type="memory_commit",
        payload=_copy_dict(commit_info),
        reason="memory.commit",
    )


__all__ = [
    "MEMORY_EFFECT_CREATED_BY_PREFIX",
    "build_memory_commit_event",
    "build_memory_delta",
    "build_memory_prepare_event",
    "memory_commit_update",
    "memory_prepare_update",
    "memory_state_update",
]
