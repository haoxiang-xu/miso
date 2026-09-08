"""Question-level acceptance rule evaluated on one in-transaction snapshot.

This is the shared precondition every canonical interaction-resolution
ingress path evaluates inside its own ``append_with_artifacts`` write
transaction: exactly one open durable request for the identity, and no
existing durable resolution for it yet. It has no side effect and does not
itself decide graph lineage; a graph-aware caller composes this with
:func:`unchain.context.graph_checkpoint.prove_graph_interaction_lineage`.
"""

from __future__ import annotations

from collections.abc import Mapping

from unchain.journal.interaction_cycles import (
    DURABLE_INTERACTION_REQUESTS,
    DURABLE_INTERACTION_RESOLUTIONS,
)
from unchain.journal.models import AttemptRef, EventCursor
from unchain.journal.snapshot import JournalSnapshot


class InteractionAcceptanceConflict(RuntimeError):
    """The canonical journal no longer allows accepting this answer."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"interaction answer not acceptable: {reason}")
        self.reason = reason


def _durable_interaction_id(event) -> str:
    raw = event.payload.get("interaction_id")
    if raw is None:
        request = event.payload.get("interaction_request")
        if isinstance(request, Mapping):
            raw = request.get("interaction_id")
    return str(raw or "").strip()


def assert_interaction_unresolved(
    snapshot: JournalSnapshot,
    *,
    attempt: AttemptRef,
    interaction_id: str,
) -> EventCursor:
    """Return the request cursor when exactly one open request exists.

    Raises :class:`InteractionAcceptanceConflict` with reason ``"not_pending"``
    when the identity has no exact unique durable request on ``attempt``, or
    ``"already_resolved"`` when a durable resolution for it already exists.
    """

    if not isinstance(snapshot, JournalSnapshot):
        raise TypeError("snapshot must be a JournalSnapshot")
    if not isinstance(attempt, AttemptRef):
        raise TypeError("attempt must be an AttemptRef")
    normalized_interaction_id = str(interaction_id or "").strip()
    if not normalized_interaction_id:
        raise TypeError("interaction_id is required")
    requests = tuple(
        event
        for event in snapshot.events
        if event.attempt == attempt
        and event.event_type in DURABLE_INTERACTION_REQUESTS
        and _durable_interaction_id(event) == normalized_interaction_id
    )
    if len(requests) != 1:
        raise InteractionAcceptanceConflict("not_pending")
    already_resolved = any(
        event.attempt == attempt
        and event.event_type in DURABLE_INTERACTION_RESOLUTIONS
        and _durable_interaction_id(event) == normalized_interaction_id
        for event in snapshot.events
    )
    if already_resolved:
        raise InteractionAcceptanceConflict("already_resolved")
    request_event = requests[0]
    return EventCursor(request_event.store_seq, request_event.event_id)


__all__ = [
    "InteractionAcceptanceConflict",
    "assert_interaction_unresolved",
]
