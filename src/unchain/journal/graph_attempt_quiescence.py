"""Shared terminal and graph-seal vocabulary for durable quiescence.

The graph checkpoint producer and generation-rebase consumer deliberately use
this one module so a terminal family cannot drift on only one side of the
durable boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


COMPLETED_ATTEMPT_TERMINALS = frozenset({"run_completed", "run.completed"})
FAILED_ATTEMPT_TERMINALS = frozenset({"run_failed", "run.failed"})
CANCELLED_ATTEMPT_TERMINALS = frozenset(
    {
        "run_cancelled",
        "run.cancelled",
        "run_canceled",
        "run.canceled",
        "run_aborted",
        "run.aborted",
    }
)
MAX_ITERATIONS_TERMINALS = frozenset(
    {"run_max_iterations", "run.max_iterations"}
)
CANONICAL_ATTEMPT_TERMINALS = (
    COMPLETED_ATTEMPT_TERMINALS
    | FAILED_ATTEMPT_TERMINALS
    | CANCELLED_ATTEMPT_TERMINALS
)
ATTEMPT_TERMINAL_EQUIVALENTS = (
    CANONICAL_ATTEMPT_TERMINALS | MAX_ITERATIONS_TERMINALS
)

GRAPH_STEP_COMPLETED = "graph.step.completed"
GRAPH_STEP_FAILED = "graph.step.failed"
GRAPH_STEP_CANCELLED = "graph.step.cancelled"
GRAPH_STEP_SEALS = frozenset(
    {GRAPH_STEP_COMPLETED, GRAPH_STEP_FAILED, GRAPH_STEP_CANCELLED}
)
GRAPH_STEP_SEAL_TERMINALS = {
    GRAPH_STEP_COMPLETED: COMPLETED_ATTEMPT_TERMINALS,
    GRAPH_STEP_FAILED: FAILED_ATTEMPT_TERMINALS | MAX_ITERATIONS_TERMINALS,
    GRAPH_STEP_CANCELLED: CANCELLED_ATTEMPT_TERMINALS,
}


class _EventLike(Protocol):
    store_seq: int
    event_type: str


@dataclass(frozen=True)
class AttemptTerminalSelection:
    """One terminal selected without treating earlier max waits as terminals."""

    event: _EventLike | None
    ambiguous: bool = False


def select_attempt_terminal(
    events: Sequence[_EventLike],
    *,
    allowed_following_event_types: frozenset[str] = frozenset(),
) -> AttemptTerminalSelection:
    """Select the canonical terminal or a narrow max-iterations equivalent.

    A canonical terminal always wins over earlier ``run_max_iterations``
    events, because an approved budget extension can legitimately continue to
    a later completed/failed/cancelled terminal.  With no canonical terminal,
    only the last max-iterations event may be terminal-equivalent, and only
    when every following event is an explicitly allowed graph seal.
    """

    ordered = tuple(sorted(events, key=lambda event: event.store_seq))
    canonical = tuple(
        event
        for event in ordered
        if event.event_type in CANONICAL_ATTEMPT_TERMINALS
    )
    if len(canonical) > 1:
        return AttemptTerminalSelection(None, ambiguous=True)
    if canonical:
        candidate = canonical[0]
        following = tuple(
            event for event in ordered if event.store_seq > candidate.store_seq
        )
        if all(
            event.event_type in allowed_following_event_types
            for event in following
        ):
            return AttemptTerminalSelection(candidate)
        return AttemptTerminalSelection(None)

    maxima = tuple(
        event
        for event in ordered
        if event.event_type in MAX_ITERATIONS_TERMINALS
    )
    if not maxima:
        return AttemptTerminalSelection(None)
    candidate = maxima[-1]
    following = tuple(
        event for event in ordered if event.store_seq > candidate.store_seq
    )
    if all(
        event.event_type in allowed_following_event_types
        for event in following
    ):
        return AttemptTerminalSelection(candidate)
    return AttemptTerminalSelection(None)


__all__ = [
    "ATTEMPT_TERMINAL_EQUIVALENTS",
    "AttemptTerminalSelection",
    "CANCELLED_ATTEMPT_TERMINALS",
    "CANONICAL_ATTEMPT_TERMINALS",
    "COMPLETED_ATTEMPT_TERMINALS",
    "FAILED_ATTEMPT_TERMINALS",
    "GRAPH_STEP_CANCELLED",
    "GRAPH_STEP_COMPLETED",
    "GRAPH_STEP_FAILED",
    "GRAPH_STEP_SEALS",
    "GRAPH_STEP_SEAL_TERMINALS",
    "MAX_ITERATIONS_TERMINALS",
    "select_attempt_terminal",
]
