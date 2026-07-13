from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .lifecycle_events import (
    build_final_message_payload,
    build_run_completed_payload,
    build_run_max_iterations_payload,
)
from .results import build_kernel_run_result
from .state import RunState
from .types import KernelRunResult

EmitEvent = Callable[..., None]
DispatchRunFinalizing = Callable[..., None]
DispatchOnSuspend = Callable[..., None]


def _completed_iteration(state: RunState) -> int:
    return max(0, int(state.iteration) - 1)


def finish_completed_run(
    state: RunState,
    *,
    callback: Any,
    run_id: str,
    emit_event: EmitEvent,
    dispatch_run_finalizing: DispatchRunFinalizing,
) -> KernelRunResult:
    state.run_status = "completed"
    dispatch_run_finalizing(
        state,
        callback=callback,
        run_id=run_id,
        iteration=_completed_iteration(state),
        status="completed",
    )
    emit_event(
        callback,
        "final_message",
        run_id,
        **build_final_message_payload(state),
    )
    emit_event(
        callback,
        "run_completed",
        run_id,
        **build_run_completed_payload(state, status="completed"),
    )
    return build_kernel_run_result(state, status="completed")


def finish_max_iterations_run(
    state: RunState,
    *,
    callback: Any,
    run_id: str,
    emit_run_max_iterations: bool,
    emit_event: EmitEvent,
    dispatch_run_finalizing: DispatchRunFinalizing,
) -> KernelRunResult:
    state.run_status = "max_iterations"
    dispatch_run_finalizing(
        state,
        callback=callback,
        run_id=run_id,
        iteration=int(state.iteration),
        status="max_iterations",
    )
    if emit_run_max_iterations:
        emit_event(
            callback,
            "run_max_iterations",
            run_id,
            **build_run_max_iterations_payload(state),
        )
    return build_kernel_run_result(state, status="max_iterations")


def finish_awaiting_human_input_run(
    state: RunState,
    *,
    callback: Any,
    run_id: str,
    dispatch_on_suspend: DispatchOnSuspend,
) -> KernelRunResult:
    state.run_status = "awaiting_human_input"
    dispatch_on_suspend(
        state,
        callback=callback,
        run_id=run_id,
        iteration=int(state.iteration),
        status="awaiting_human_input",
    )
    return build_kernel_run_result(state, status="awaiting_human_input")


__all__ = [
    "finish_awaiting_human_input_run",
    "finish_completed_run",
    "finish_max_iterations_run",
]
