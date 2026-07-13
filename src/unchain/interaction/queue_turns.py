"""Queued-turn primitives: buffer follow-up requests until the current run ends.

.. versionchanged:: 0.2.0
    Renamed from ``unchain.interaction.steer`` — ``SteerBuffer`` is now
    ``QueuedTurnBuffer`` and ``merge_steered_texts`` is now
    ``merge_queued_turn_texts``. The old module remains importable as a
    deprecation shim (``unchain.interaction.steer``) and is slated for
    removal in the next minor release.
"""

from __future__ import annotations

import threading

_MERGE_HEADER = (
    "The user sent several follow-up requests while the previous task was "
    "running. Address all of them, in order:\n"
)


def merge_queued_turn_texts(texts: list[str]) -> str:
    cleaned = [t.strip() for t in texts if t.strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(cleaned, start=1))
    return _MERGE_HEADER + numbered


class QueuedTurnBuffer:
    """Thread-safe buffer for 'do this next' messages; drained after a run ends."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: list[str] = []

    def post(self, text: str) -> None:
        with self._lock:
            self._pending.append(text)

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def drain_merged(self) -> str | None:
        with self._lock:
            drained = self._pending
            self._pending = []
        merged = merge_queued_turn_texts(drained)
        return merged or None
