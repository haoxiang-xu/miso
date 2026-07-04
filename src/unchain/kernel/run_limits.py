from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .lifecycle_events import (
    build_max_iterations_decision_payload,
    build_run_max_iterations_payload,
)
from .state import RunState

EmitEvent = Callable[..., None]


@dataclass(frozen=True)
class MaxIterationsBoundary:
    effective_max: int
    should_finish: bool = False
    emit_run_max_iterations_on_finish: bool = False


def resolve_max_iterations_boundary(
    state: RunState,
    *,
    effective_max: int,
    on_max_iterations: Any,
    callback: Any,
    run_id: str,
    emit_event: EmitEvent,
) -> MaxIterationsBoundary:
    current_max = int(effective_max)
    if int(state.iteration) < current_max:
        return MaxIterationsBoundary(effective_max=current_max)

    if not callable(on_max_iterations):
        return MaxIterationsBoundary(
            effective_max=current_max,
            should_finish=True,
            emit_run_max_iterations_on_finish=True,
        )

    emit_event(
        callback,
        "run_max_iterations",
        run_id,
        **build_run_max_iterations_payload(state),
    )
    response = on_max_iterations(
        build_max_iterations_decision_payload(state, max_iterations=current_max)
    )
    if isinstance(response, dict) and response.get("approved"):
        extra_iterations = max(1, int(response.get("extra_iterations", current_max)))
        return MaxIterationsBoundary(effective_max=current_max + extra_iterations)

    return MaxIterationsBoundary(
        effective_max=current_max,
        should_finish=True,
        emit_run_max_iterations_on_finish=False,
    )


__all__ = [
    "MaxIterationsBoundary",
    "resolve_max_iterations_boundary",
]
