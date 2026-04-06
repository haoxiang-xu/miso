from __future__ import annotations

import copy
from typing import Any

from .types import ToolBatchState


def copy_messages(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [copy.deepcopy(message) for message in (messages or []) if isinstance(message, dict)]


def append_executed_call_id(batch_state: ToolBatchState, call_id: str) -> list[str]:
    executed = list(batch_state.executed_call_ids)
    if call_id not in executed:
        executed.append(call_id)
    return executed


def emit_loop_event(
    loop: Any,
    callback: Any,
    event_type: str,
    run_id: str,
    *,
    iteration: int,
    **extra: Any,
) -> None:
    if loop is None or not hasattr(loop, "emit_event"):
        return
    loop.emit_event(
        callback,
        event_type,
        run_id,
        iteration=iteration,
        **extra,
    )
