from __future__ import annotations

import copy
from typing import Any

from ..capabilities import EmitEventOp
from ..kernel.delta import HarnessDelta, MessageListOp


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
) -> HarnessDelta:
    return HarnessDelta(
        created_by=created_by,
        base_version_id=base_version_id,
        rebase_to_latest=rebase_to_latest,
        ops=tuple(ops or ()),
        state_updates=_copy_dict(state_updates),
        trace=_copy_dict(trace),
    )


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
