"""Shared vocabulary for durable-pause versus live interaction cycles.

The graph checkpoint scan and the generation-rebase validator read the same
durable journal, so the two interaction families must be defined exactly once:

- A **durable pause** suspends the run: the kernel emits a canonical
  ``interaction.requested`` event (always carrying an ``interaction_request``
  payload mapping), the attempt exits awaiting, and continuing later requires
  a certified ``graph.step.resume.admitted`` receipt.
- A **live prompt** is answered inside the running attempt (tool
  confirmation, max-iterations continuation, live human input): the attempt
  never exits, so a resume admission cannot exist for it and must never be
  demanded of it.

Historical journals already contain live cycles without admissions; this
vocabulary makes those journals legal by definition instead of rewriting them.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any


DURABLE_INTERACTION_REQUESTS = frozenset(
    {"interaction_requested", "interaction.requested"}
)
LIVE_INTERACTION_REQUESTS = frozenset(
    {
        "human_input_requested",
        "tool_confirmation_requested",
        "continuation_request",
        "input_requested",
    }
)
INTERACTION_REQUESTS = DURABLE_INTERACTION_REQUESTS | LIVE_INTERACTION_REQUESTS

DURABLE_INTERACTION_RESOLUTIONS = frozenset(
    {"interaction_resolved", "interaction.resolved"}
)
LIVE_INTERACTION_OUTCOMES = frozenset({"tool_confirmed", "tool_denied"})
INTERACTION_RESOLUTIONS = (
    DURABLE_INTERACTION_RESOLUTIONS | LIVE_INTERACTION_OUTCOMES
)


class InteractionRequestFamily(Enum):
    DURABLE = "durable"
    LIVE = "live"


def interaction_request_family(
    event_type: str,
    payload: Mapping[str, Any] | None,
) -> InteractionRequestFamily | None:
    """Classify one journal event as opening a durable or a live cycle.

    ``human_input_requested`` is dual-natured: the projector canonicalizes its
    durable form (which carries an ``interaction_request`` mapping) into
    ``interaction.requested``, so a raw live type carrying that payload only
    appears in historical journals and still means a durable pause.  The
    payload check applies to every live type so an unexpected durable-shaped
    request classifies as the stricter durable family and fails closed.
    """

    if event_type in DURABLE_INTERACTION_REQUESTS:
        return InteractionRequestFamily.DURABLE
    if event_type not in LIVE_INTERACTION_REQUESTS:
        return None
    if isinstance(payload, Mapping) and isinstance(
        payload.get("interaction_request"), Mapping
    ):
        return InteractionRequestFamily.DURABLE
    return InteractionRequestFamily.LIVE


__all__ = [
    "DURABLE_INTERACTION_REQUESTS",
    "DURABLE_INTERACTION_RESOLUTIONS",
    "INTERACTION_REQUESTS",
    "INTERACTION_RESOLUTIONS",
    "InteractionRequestFamily",
    "LIVE_INTERACTION_OUTCOMES",
    "LIVE_INTERACTION_REQUESTS",
    "interaction_request_family",
]
